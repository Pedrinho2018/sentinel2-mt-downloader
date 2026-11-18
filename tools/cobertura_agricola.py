"""Lote agrícola de Mato Grosso: candidatas priorizadas por uso do solo e amostragem por janelas.

Exemplos:
    python tools/cobertura_agricola.py candidatas --raiz <app> --nome agricola-001
    python tools/cobertura_agricola.py lote --raiz <app> --nome agricola-001 --alvo 120

A camada ESA WorldCover 2021 só PRIORIZA onde procurar; ela não gera rótulos.
A decisão final é a inspeção visual de cada recorte. Não apaga dados e não
sobrescreve lotes existentes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

RAIZ_CODIGO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_CODIGO / "src"))

from sentinel2_mt.cobertura.agricola import (  # noqa: E402
    MUNICIPIOS_PRIORITARIOS,
    mapa_cultivo,
    selecionar_candidatas,
)
from sentinel2_mt.cobertura.amostragem import AMBIENTE_GDAL, AmostradorJanelas, OpcoesAmostragem  # noqa: E402
from sentinel2_mt.cobertura.grade import Celula, carregar_estado, carregar_regioes  # noqa: E402
from sentinel2_mt.cobertura.referencia import ReferenciaUsoSolo  # noqa: E402
from sentinel2_mt.cobertura.registro import gravar_catalogo  # noqa: E402

STAC_PADRAO = "https://data.inpe.br/bdc/stac/v1/"


def centros_existentes(base: Path) -> list[tuple[float, float]]:
    """Centro real de todas as amostras já gravadas (de qualquer lote, ativas ou arquivadas)."""
    pontos = []
    for meta in base.rglob("metadata.json"):
        w, s, e, n = json.loads(meta.read_text(encoding="utf-8"))["bounds_wgs84"]
        pontos.append(((w + e) / 2, (s + n) / 2))
    return pontos


def principal() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("acao", choices=("candidatas", "lote"))
    parser.add_argument("--raiz", type=Path, default=RAIZ_CODIGO)
    parser.add_argument("--pasta", default="data/amostras-mt")
    parser.add_argument("--nome", default="agricola-001")
    parser.add_argument("--total-candidatas", type=int, default=170)
    parser.add_argument("--por-municipio", type=int, default=7)
    parser.add_argument("--min-cultivo", type=float, default=0.4)
    parser.add_argument("--alvo", type=int, default=120, help="Amostras aceitas (qualidade) antes de parar.")
    parser.add_argument("--inicio", default="2026-06-01")
    parser.add_argument("--fim", default="2026-09-30")
    parser.add_argument("--continuar", action="store_true",
                        help="Retoma o lote existente: pula candidatas já registradas no diário (nada é apagado).")
    parser.add_argument("--max-nuvem", type=float, default=2.0, help="Nuvem/sombra máx. na janela (%%, SCL).")
    args = parser.parse_args()

    raiz = args.raiz.resolve()
    base = raiz / args.pasta
    arquivo_candidatas = base / f"{args.nome}-candidatas.json"
    regioes = carregar_regioes()
    estado = carregar_estado()

    if args.acao == "candidatas":
        if arquivo_candidatas.exists():
            print(f"[ERRO] {arquivo_candidatas} já existe.", file=sys.stderr)
            return 1
        cache = base / "mapa-cultivo-worldcover-2021.json"
        if cache.is_file():
            celulas = json.loads(cache.read_text(encoding="utf-8"))["celulas"]
        else:
            import rasterio

            caixa = (-61.7, -18.1, -50.1, -7.3)
            with rasterio.Env(**AMBIENTE_GDAL):
                celulas = mapa_cultivo(caixa)
            cache.write_text(json.dumps({"fonte": "ESA WorldCover 10 m v200 (2021), CC BY 4.0", "passo_graus": 0.05,
                                         "caixa": caixa, "celulas": celulas}), encoding="utf-8")
        municipios_ibge = json.loads((RAIZ_CODIGO / "assets/municipios_ibge.json").read_text(encoding="utf-8"))["municipios"]
        bbox = {m["nome"]: tuple(m["bbox"]) for m in municipios_ibge if m["nome"] in MUNICIPIOS_PRIORITARIOS}
        faltando = set(MUNICIPIOS_PRIORITARIOS) - set(bbox)
        if faltando:
            print(f"[AVISO] municípios sem bbox: {sorted(faltando)}", file=sys.stderr)
        existentes = centros_existentes(base)
        candidatas = selecionar_candidatas(
            celulas, estado, regioes, bbox, existentes, total=args.total_candidatas,
            por_municipio=args.por_municipio, min_cultivo=args.min_cultivo,
        )
        arquivo_candidatas.write_text(json.dumps({
            "referencia": "ESA WorldCover 10 m v200 (2021) — somente priorização; ano diferente das imagens",
            "criterios": {"min_cultivo": args.min_cultivo, "max_arvore": 0.5, "distancia_entre_km": 12,
                          "distancia_amostras_existentes_km": 8, "por_municipio": args.por_municipio},
            "amostras_existentes_consideradas": len(existentes),
            "candidatas": candidatas,
        }, ensure_ascii=False, indent=1), encoding="utf-8")
        from collections import Counter

        print(json.dumps({"celulas_mapa": len(celulas), "candidatas": len(candidatas),
                          "por_grupo": dict(Counter(c["grupo"] for c in candidatas))}, ensure_ascii=False, indent=1))
        return 0

    pasta_lote = base / args.nome
    diario = base / f"{args.nome}-resultados.jsonl"
    if pasta_lote.exists() and not args.continuar:
        print(f"[ERRO] O lote {pasta_lote} já existe; use --continuar ou outro --nome.", file=sys.stderr)
        return 1
    anteriores = []
    if args.continuar and diario.is_file():
        anteriores = [json.loads(l) for l in diario.read_text(encoding="utf-8").splitlines() if l.strip()]
    import rasterio
    from sentinel2_mt.servico import abrir_cliente_stac

    candidatas = json.loads(arquivo_candidatas.read_text(encoding="utf-8"))["candidatas"]
    opcoes = OpcoesAmostragem(inicio=args.inicio, fim=args.fim, max_nuvem_janela_pct=args.max_nuvem)
    referencia = ReferenciaUsoSolo()
    amostrador = AmostradorJanelas(pasta_lote, raiz, abrir_cliente_stac(STAC_PADRAO, timeout=(20, 90)),
                                   anotar=referencia.resumo)
    feitas = {r["celula"]["id"] for r in anteriores}
    inicio, resultados, aceitas = time.monotonic(), [], sum(1 for r in anteriores if r["aceita"])
    with rasterio.Env(**AMBIENTE_GDAL):
        for indice, c in enumerate(candidatas, 1):
            if aceitas >= args.alvo:
                break
            celula = Celula(f"agr{indice:03d}_{c['grupo'].split(':')[1].replace(' ', '_')}", c["lon"], c["lat"],
                            0.05, c["regiao_codigo"], c["regiao"])
            if celula.id in feitas:
                continue
            resultado = amostrador.amostrar(celula, opcoes)
            resultados.append(resultado)
            aceitas += resultado.aceita
            registro = resultado.para_dict()
            registro["candidata"] = c
            with diario.open("a", encoding="utf-8") as arquivo:
                arquivo.write(json.dumps(registro, ensure_ascii=False) + "\n")
            print(f"[{aceitas:3d}/{args.alvo}] {indice:3d} {c['grupo']:<34} "
                  f"{'aceita' if resultado.aceita else 'sem imagem adequada'} "
                  f"({len(resultado.tentativas)} tent.) {time.monotonic() - inicio:6.0f}s", flush=True)
    from sentinel2_mt.cobertura.amostragem import ResultadoAmostra

    todas = [ResultadoAmostra(None, r["aceita"], [], r.get("patch_id", ""), r.get("pasta", ""), r.get("metadata", {}))
             for r in anteriores] + resultados
    n = gravar_catalogo(todas, pasta_lote / "patches.csv", raiz)
    print(f"[LOTE] {n} aceita(s) por qualidade em {len(resultados)} candidata(s); {time.monotonic() - inicio:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(principal())
