"""Cobertura estadual de Mato Grosso: plano, lote piloto por janelas e mapa.

Exemplos:
    python tools/cobertura_mt.py plano
    python tools/cobertura_mt.py lote --nome piloto-001 --total 100 --minimo 12 \
        --inicio 2026-06-01 --fim 2026-09-30
    python tools/cobertura_mt.py mapa

Lê somente janelas 512×512 dos COGs Sentinel-2 L2A (sem baixar cenas inteiras).
Não apaga dados e não sobrescreve lotes existentes.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

RAIZ_CODIGO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ_CODIGO / "src"))

from sentinel2_mt.cobertura.amostragem import AMBIENTE_GDAL, AmostradorJanelas, OpcoesAmostragem  # noqa: E402
from sentinel2_mt.cobertura.grade import (  # noqa: E402
    alocar_por_regiao,
    carregar_estado,
    carregar_regioes,
    criar_grade,
)
from sentinel2_mt.cobertura.registro import (  # noqa: E402
    RegistroCobertura,
    cobertura_geojson,
    executar_lote,
    gerar_mapa,
    gravar_catalogo,
)

STAC_PADRAO = "https://data.inpe.br/bdc/stac/v1/"


def relatorio_lote(base: Path, nome: str, raiz: Path, celulas, registro) -> str:
    """Relatório de qualidade em Markdown a partir do diário do lote."""
    from collections import Counter
    from statistics import mean, median

    from sentinel2_mt.cobertura.registro import estimativas

    linhas = [json.loads(l) for l in (base / f"{nome}-resultados.jsonl").read_text(encoding="utf-8").splitlines() if l]
    aceitas = [r for r in linhas if r["aceita"]]
    por_regiao = Counter(r["celula"]["regiao_nome"] for r in aceitas)
    avaliadas = Counter(r["celula"]["regiao_nome"] for r in linhas)
    tentativas = [t for r in linhas for t in r["tentativas"]]
    motivos = Counter(t["resultado"].split(" (")[0].split(" na ")[0].split(":")[0] for t in tentativas if t["resultado"] != "aceita")
    nuvem = [r["metadata"]["cloud_pct"] for r in aceitas]
    validos = [r["metadata"]["valid_pixel_pct"] for r in aceitas]
    datas = Counter(r["metadata"]["date"][:7] for r in aceitas)
    tiles = Counter(r["metadata"]["scene_id"].split("_T")[1][:5] for r in aceitas if "_T" in r["metadata"]["scene_id"])
    tentativas_aceitas = Counter(len(r["tentativas"]) for r in aceitas)
    tamanho = sum(f.stat().st_size for f in (base / nome).rglob("*") if f.is_file())
    tamanho_medio_mb = tamanho / max(len(aceitas), 1) / 1024**2
    est = estimativas(len(celulas), tamanho_medio_mb, 3.8, max(len(aceitas) / max(len(linhas), 1), 0.01))
    saida = [f"## Lote {nome}", "",
             f"- Células avaliadas: {len(linhas)}; amostras aceitas: **{len(aceitas)}**; "
             f"sem imagem adequada: {len(linhas) - len(aceitas)}",
             f"- Espaço em disco do lote: {tamanho / 1024**2:.1f} MB ({tamanho_medio_mb:.2f} MB por amostra)", "",
             "| Região intermediária (IBGE) | Avaliadas | Aceitas |", "| --- | --- | --- |"]
    saida += [f"| {reg} | {avaliadas[reg]} | {por_regiao[reg]} |" for reg in sorted(avaliadas)]
    saida += ["", "| Qualidade (aceitas) | Valor |", "| --- | --- |",
              f"| Nuvem/sombra na janela (SCL) — média / mediana / máx. | {mean(nuvem):.2f}% / {median(nuvem):.2f}% / {max(nuvem):.2f}% |" if nuvem else "| Nuvem | — |",
              f"| Pixels válidos — mínimo | {min(validos):.2f}% |" if validos else "| Válidos | — |",
              f"| Tiles MGRS distintos | {len(tiles)} |",
              f"| Aceitas na 1ª / 2ª / 3ª+ tentativa | {tentativas_aceitas[1]} / {tentativas_aceitas[2]} / {sum(v for k, v in tentativas_aceitas.items() if k >= 3)} |",
              "", "Datas (mês) das amostras aceitas: " + ", ".join(f"{m}: {n}" for m, n in sorted(datas.items())), "",
              "Motivos de recusa de tentativas: " + (", ".join(f"{m}: {n}" for m, n in motivos.most_common()) or "nenhum"), "",
              "### Estimativa para cobrir toda a grade (1 amostra por célula, por época)", "",
              f"- Células: {est['celulas']}; armazenamento ≈ {est['armazenamento_gb_por_epoca']} GB; "
              f"tempo ≈ {est['horas_por_epoca']} h (≈3,8 s por célula avaliada; aceite observado {est['taxa_aceite_usada']:.0%})."]
    return "\n".join(saida)


def principal() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("acao", choices=("plano", "lote", "mapa", "relatorio"))
    parser.add_argument("--raiz", type=Path, default=RAIZ_CODIGO, help="Raiz da aplicação (onde fica data/).")
    parser.add_argument("--pasta", default="data/amostras-mt", help="Pasta das amostras, relativa à raiz.")
    parser.add_argument("--nome", default="piloto-001")
    parser.add_argument("--total", type=int, default=100)
    parser.add_argument("--minimo", type=int, default=12, help="Amostras mínimas por região intermediária.")
    parser.add_argument("--inicio", default="2026-06-01")
    parser.add_argument("--fim", default="2026-09-30")
    parser.add_argument("--grade", type=float, default=0.25, help="Tamanho da célula em graus.")
    parser.add_argument("--distancia", type=float, default=20.0, help="Distância mínima entre amostras (km).")
    parser.add_argument("--semente", type=int, default=2026)
    args = parser.parse_args()

    raiz = args.raiz.resolve()
    base = raiz / args.pasta
    regioes = carregar_regioes()
    estado = carregar_estado()
    celulas = criar_grade(regioes, estado, args.grade)
    registro = RegistroCobertura(base / "cobertura.json")

    if args.acao == "plano":
        alocacao = alocar_por_regiao(regioes, args.total, args.minimo)
        print(json.dumps({
            "celulas": len(celulas),
            "celulas_por_regiao": {r.nome: sum(1 for c in celulas if c.regiao_codigo == r.codigo) for r in regioes},
            "alocacao_lote": {r.nome: alocacao[r.codigo] for r in regioes},
            "registro": registro.resumo_por_regiao(celulas),
        }, ensure_ascii=False, indent=2))
        return 0

    if args.acao == "lote":
        pasta_lote = base / args.nome
        if pasta_lote.exists():
            print(f"[ERRO] O lote {pasta_lote} já existe; escolha outro --nome.", file=sys.stderr)
            return 1
        import rasterio
        from sentinel2_mt.servico import abrir_cliente_stac

        alocacao = alocar_por_regiao(regioes, args.total, args.minimo)
        opcoes = OpcoesAmostragem(inicio=args.inicio, fim=args.fim)
        amostrador = AmostradorJanelas(pasta_lote, raiz, abrir_cliente_stac(STAC_PADRAO))
        inicio = time.monotonic()
        diario = (base / f"{args.nome}-resultados.jsonl")
        diario.parent.mkdir(parents=True, exist_ok=True)

        def progresso(resultado, aceitas, alvo):
            with diario.open("a", encoding="utf-8") as arquivo:
                arquivo.write(json.dumps(resultado.para_dict(), ensure_ascii=False) + "\n")
            estado_txt = "aceita" if resultado.aceita else "sem imagem adequada"
            print(f"[{aceitas:3d}/{alvo}] {resultado.celula.regiao_nome:<16} {resultado.celula.id} "
                  f"{estado_txt} ({len(resultado.tentativas)} tentativa(s)) {time.monotonic() - inicio:6.0f}s", flush=True)

        with rasterio.Env(**AMBIENTE_GDAL):
            resultados = executar_lote(
                amostrador, celulas, alocacao, opcoes, registro, lote=args.nome,
                distancia_minima_km=args.distancia, semente=args.semente, progresso=progresso,
            )
        n = gravar_catalogo(resultados, pasta_lote / "patches.csv", raiz)
        print(f"[LOTE] {n} amostra(s) aceitas em {len(resultados)} célula(s) avaliadas; "
              f"{time.monotonic() - inicio:.0f}s. Catálogo: {pasta_lote / 'patches.csv'}")
        args.acao = "mapa"

    if args.acao == "relatorio":
        print(relatorio_lote(base, args.nome, raiz, celulas, registro))
        return 0

    if args.acao == "mapa":
        amostras = []
        for celula in registro.amostradas():
            meta = raiz / celula["pasta"] / "metadata.json"
            if meta.is_file():
                w, s_, e, n = json.loads(meta.read_text(encoding="utf-8"))["bounds_wgs84"]
                amostras.append(((w + e) / 2, (s_ + n) / 2))  # centro real do recorte gravado
        gerar_mapa(estado, regioes, celulas, registro, base / "mapa-cobertura.png",
                   f"Cobertura de amostras Sentinel-2 L2A — Mato Grosso ({len(amostras)} amostras; "
                   f"{len(registro.amostradas())} de {len(celulas)} células)", amostras=amostras)
        cobertura_geojson(celulas, registro, base / "cobertura.geojson")
        print(f"[MAPA] {base / 'mapa-cobertura.png'} e {base / 'cobertura.geojson'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(principal())
