"""Registro da cobertura estadual, execução de lotes, catálogo, mapa e estimativas."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
import random

from ..catalogo import RepositorioCatalogoPatchesCSV
from ..modelos import RegistroPatch
from .amostragem import AmostradorJanelas, OpcoesAmostragem, ResultadoAmostra
from .grade import Celula, Regiao, distancia_km

ESTADOS = ("amostrada", "sem_imagem_adequada")


class RegistroCobertura:
    """Estado de cada célula da grade; células ausentes estão pendentes."""

    def __init__(self, caminho: Path) -> None:
        self.caminho = Path(caminho)
        self.celulas: dict[str, dict] = {}
        if self.caminho.is_file():
            self.celulas = json.loads(self.caminho.read_text(encoding="utf-8")).get("celulas", {})

    def registrar(self, resultado: ResultadoAmostra, lote: str) -> None:
        anterior = self.celulas.get(resultado.celula.id, {})
        if anterior.get("estado") == "amostrada" and not resultado.aceita:
            return  # nunca rebaixa uma célula já amostrada
        self.celulas[resultado.celula.id] = {
            "estado": "amostrada" if resultado.aceita else "sem_imagem_adequada",
            "regiao_codigo": resultado.celula.regiao_codigo,
            "regiao_nome": resultado.celula.regiao_nome,
            "lon": resultado.celula.lon,
            "lat": resultado.celula.lat,
            "lote": lote,
            "patch_id": resultado.patch_id,
            "pasta": resultado.pasta,
            "data": resultado.metadata.get("date", ""),
            "tentativas": [asdict(t) for t in resultado.tentativas],
            "atualizado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def amostradas(self) -> list[dict]:
        return [c for c in self.celulas.values() if c["estado"] == "amostrada"]

    def salvar(self) -> None:
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        temporario = self.caminho.with_suffix(".tmp")
        temporario.write_text(
            json.dumps({"atualizado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "celulas": self.celulas}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        temporario.replace(self.caminho)

    def resumo_por_regiao(self, celulas: list[Celula]) -> dict[str, dict[str, int]]:
        resumo: dict[str, Counter] = {}
        for celula in celulas:
            estado = self.celulas.get(celula.id, {}).get("estado", "pendente")
            resumo.setdefault(celula.regiao_nome, Counter())[estado] += 1
        return {regiao: dict(contagem) for regiao, contagem in sorted(resumo.items())}


def executar_lote(
    amostrador: AmostradorJanelas,
    celulas: list[Celula],
    alocacao: dict[str, int],
    opcoes: OpcoesAmostragem,
    registro: RegistroCobertura,
    *,
    lote: str,
    distancia_minima_km: float = 20.0,
    semente: int = 2026,
    max_celulas_por_vaga: int = 3,
    progresso: Callable[[ResultadoAmostra, int, int], None] | None = None,
) -> list[ResultadoAmostra]:
    """Preenche a cota de cada região, alternando regiões e substituindo células sem imagem.

    Células já registradas (em qualquer lote) não são repetidas, e novas amostras
    respeitam a distância mínima também em relação às de lotes anteriores.
    """
    gerador = random.Random(semente)
    filas: dict[str, list[Celula]] = {}
    for codigo in sorted(alocacao):
        candidatas = [c for c in celulas if c.regiao_codigo == codigo and c.id not in registro.celulas]
        gerador.shuffle(candidatas)
        filas[codigo] = candidatas
    aceitas_por_regiao = Counter()
    tentativas_por_regiao = Counter()
    pontos = [(c["lon"], c["lat"]) for c in registro.amostradas()]
    resultados: list[ResultadoAmostra] = []
    alvo = sum(alocacao.values())
    while True:
        andou = False
        for codigo in sorted(alocacao):
            if aceitas_por_regiao[codigo] >= alocacao[codigo]:
                continue
            if tentativas_por_regiao[codigo] >= alocacao[codigo] * max_celulas_por_vaga:
                continue  # região sem imagens adequadas suficientes no período
            fila = filas[codigo]
            while fila and not all(distancia_km(fila[0].lon, fila[0].lat, x, y) >= distancia_minima_km for x, y in pontos):
                fila.pop(0)
            if not fila:
                continue
            celula = fila.pop(0)
            tentativas_por_regiao[codigo] += 1
            resultado = amostrador.amostrar(celula, opcoes)
            registro.registrar(resultado, lote)
            registro.salvar()
            resultados.append(resultado)
            if resultado.aceita:
                aceitas_por_regiao[codigo] += 1
                pontos.append((celula.lon, celula.lat))
            if progresso:
                progresso(resultado, sum(aceitas_por_regiao.values()), alvo)
            andou = True
        if not andou:
            break
    return resultados


def gravar_catalogo(resultados: list[ResultadoAmostra], caminho: Path, raiz: Path) -> int:
    """Catálogo no formato do pipeline (`patches.csv`) para o Laboratório importar."""
    registros = []
    for r in resultados:
        if not r.aceita:
            continue
        m = r.metadata
        pasta = raiz / r.pasta
        registros.append(RegistroPatch(
            patch_id=m["patch_id"], scene_id=m["scene_id"], collection=m["collection"], date=m["date"],
            bbox=json.dumps(m["bounds"]), crs=m["crs"], width=m["width"], height=m["height"],
            pixel_size=json.dumps(m["pixel_size"]), cloud_pct=f"{m['cloud_pct']:.2f}",
            valid_pixel_pct=f"{m['valid_pixel_pct']:.2f}", source_scene=m["scene_id"],
            rgb_png=(pasta / m["rgb_png"]["arquivo"]).relative_to(raiz).as_posix(),
            geotiff_path=(pasta / "multiband.tif").relative_to(raiz).as_posix(),
            bands=";".join(m["bands"]), scl_path=(pasta / "scl.tif").relative_to(raiz).as_posix(),
            status="APROVADO",
        ))
    RepositorioCatalogoPatchesCSV().salvar(caminho, registros)
    return len(registros)


def estimativas(celulas_total: int, tamanho_medio_mb: float, segundos_por_amostra: float, taxa_aceite: float) -> dict:
    """Armazenamento e tempo para cobrir toda a grade com uma amostra por célula e época."""
    tentativas = celulas_total / max(taxa_aceite, 0.01)
    return {
        "celulas": celulas_total,
        "armazenamento_gb_por_epoca": round(celulas_total * tamanho_medio_mb / 1024, 2),
        "horas_por_epoca": round(tentativas * segundos_por_amostra / 3600, 1),
        "taxa_aceite_usada": round(taxa_aceite, 2),
    }


def gerar_mapa(
    estado: Regiao, regioes: list[Regiao], celulas: list[Celula], registro: RegistroCobertura,
    destino: Path, titulo: str, amostras: list[tuple[float, float]] | None = None,
) -> None:
    """Mapa: regiões IBGE, células por estado e, se informadas, posições reais das amostras (lon, lat)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Patch, Rectangle

    cores_regiao = ["#e8f1ec", "#eef0e1", "#f3e9e4", "#e6edf5", "#f2ecf6"]
    fig, eixo = plt.subplots(figsize=(10, 10), dpi=130)
    for indice, regiao in enumerate(regioes):
        for aneis in regiao.poligonos:
            xs, ys = zip(*aneis[0])
            eixo.fill(xs, ys, color=cores_regiao[indice % len(cores_regiao)], zorder=0)
            eixo.plot(xs, ys, color="#8a9a92", linewidth=0.6, zorder=1)
    estados = {"amostrada": "#0f7548", "sem_imagem_adequada": "#c2410c"}
    for celula in celulas:
        estado_celula = registro.celulas.get(celula.id, {}).get("estado")
        x0, y0, x1, y1 = celula.limites()
        eixo.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, fill=estado_celula is not None,
                                 facecolor=estados.get(estado_celula, "none"), edgecolor="#c9d3ce",
                                 linewidth=0.15, alpha=0.9 if estado_celula else 1, zorder=2))
    for aneis in estado.poligonos:
        xs, ys = zip(*aneis[0])
        eixo.plot(xs, ys, color="#17201d", linewidth=1.2, zorder=3)
    if amostras:
        eixo.scatter([lon for lon, _ in amostras], [lat for _, lat in amostras], s=10, c="#111827",
                     marker="o", linewidths=0, zorder=5, label="posição real da amostra")
    for regiao in regioes:
        pontos = [p for aneis in regiao.poligonos for p in aneis[0]]
        cx = sum(p[0] for p in pontos) / len(pontos)
        cy = sum(p[1] for p in pontos) / len(pontos)
        amostras = sum(1 for c in registro.amostradas() if c["regiao_codigo"] == regiao.codigo)
        eixo.text(cx, cy, f"{regiao.nome}\n{amostras} amostra(s)", ha="center", va="center", fontsize=9,
                  color="#17201d", zorder=4, bbox={"facecolor": "white", "alpha": 0.7, "edgecolor": "none"})
    eixo.legend(handles=[
        Patch(facecolor="#0f7548", label="célula amostrada"),
        Patch(facecolor="#c2410c", label="sem imagem adequada no período"),
        Patch(facecolor="white", edgecolor="#c9d3ce", label="pendente"),
    ] + ([eixo.collections[-1]] if amostras else []), loc="lower left", fontsize=8)
    eixo.set_title(titulo, fontsize=11)
    eixo.set_xlabel("longitude (°)")
    eixo.set_ylabel("latitude (°)")
    eixo.set_aspect("equal")
    eixo.text(0.99, 0.01, "Limites: IBGE (malhas v3, SIRGAS 2000). Grade 0,25°.", transform=eixo.transAxes,
              ha="right", fontsize=7, color="#485750")
    destino.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(destino)
    plt.close(fig)


def cobertura_geojson(celulas: list[Celula], registro: RegistroCobertura, destino: Path) -> None:
    feicoes = []
    for c in celulas:
        x0, y0, x1, y1 = c.limites()
        info = registro.celulas.get(c.id, {})
        feicoes.append({
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]},
            "properties": {"celula": c.id, "regiao": c.regiao_nome, "estado": info.get("estado", "pendente"),
                           "patch_id": info.get("patch_id", ""), "data": info.get("data", ""), "lote": info.get("lote", "")},
        })
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps({"type": "FeatureCollection", "features": feicoes}, ensure_ascii=False), encoding="utf-8")
