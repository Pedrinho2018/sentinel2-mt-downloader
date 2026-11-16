"""Leitura, indicadores e exportação de resultados de análise, sem Qt.

Regras de apresentação aplicadas aqui (ver docs/transicao-talhoes-culturas.md):
- detecção de talhões e classificação de culturas são resultados distintos;
- sem modelo de classificação aprovado, a cultura é "não determinada";
- caixas ficam em pixels da imagem RGB e nunca são convertidas em área.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Iterable

from .historico import RegistroHistoricoAnalise


LIMITE_JSON_BYTES = 10 * 1024 * 1024

# Escala dos índices por coleção, conforme `eo:bands` do STAC do INPE/BDC
# (S2-16D-2: NDVI e EVI int16, scale 0.0001, nodata -9999, faixa -10000..10000;
# conferido em 16/11/2026). Coleções fora desta tabela não exibem indicadores,
# para não apresentar valores com escala presumida.
ESCALA_INDICES = {
    "S2-16D-2": {"NDVI": 0.0001, "EVI": 0.0001},
}
FAIXA_INDICE_BRUTO = (-10000, 10000)
NODATA_INDICE = -9999

AVISO_AREA = (
    "As caixas estão em pixels da imagem RGB do recorte. Elas indicam onde o modelo "
    "detectou talhões, mas não medem área: não convertemos caixas em hectares."
)
AVISO_CULTURA = (
    "Cultura não determinada. Nenhum modelo foi validado e aprovado para identificar "
    "culturas. O modelo ativo é de detecção por caixas; mesmo que os nomes das classes "
    "dele citem culturas, esses rótulos não são apresentados como cultura identificada."
)
CLASSES_DE_CULTURA = {
    "soja", "milho", "algodao", "cana_de_acucar", "arroz", "cafe", "feijao", "trigo",
}


def aviso_classes_modelo(classes: Iterable[str]) -> str:
    """Alerta quando um modelo de detecção usa nomes de culturas como classes."""
    culturas = sorted({str(classe) for classe in classes} & CLASSES_DE_CULTURA)
    if not culturas:
        return ""
    return (
        "As classes deste modelo incluem nomes de culturas (" + ", ".join(culturas) + "). "
        "Elas aparecem como \"classe do modelo\" e não foram validadas em campo: "
        "não as interprete como cultura identificada."
    )


@dataclass(frozen=True)
class DeteccaoVisual:
    classe: str
    confianca: float
    caixa_px: tuple[float, float, float, float]


@dataclass(frozen=True)
class ItemResultado:
    patch_id: str
    cena: str
    data: str
    nuvens_pct: float | None
    imagem_original: Path | None
    imagem_analisada: Path | None
    multibanda: Path | None
    colecao: str
    deteccoes: tuple[DeteccaoVisual, ...]


@dataclass(frozen=True)
class ResultadoVisualizacao:
    analysis_id: str
    criado_em: str
    regiao: str
    periodo_inicio: str
    periodo_fim: str
    cenas: tuple[str, ...]
    modelo_nome: str
    modelo_versao: str
    modelo_sha256: str
    modelo_classes: tuple[str, ...]
    tarefa: str
    dispositivo: str
    estatisticas: dict
    itens: tuple[ItemResultado, ...]
    caminho_json: Path
    caminho_relatorio: Path | None
    dados: dict = field(repr=False, default_factory=dict)


def _contido(caminho: Path, raiz: Path) -> bool:
    try:
        caminho.resolve().relative_to(raiz.resolve())
    except ValueError:
        return False
    return True


def _arquivo_seguro(valor: object, raiz_projeto: Path, raiz_permitida: Path) -> Path | None:
    if not valor:
        return None
    caminho = Path(str(valor)).expanduser()
    caminho = caminho if caminho.is_absolute() else raiz_projeto / caminho
    if caminho.is_symlink() or not caminho.is_file() or not _contido(caminho, raiz_permitida):
        return None
    return caminho.resolve()


def _metadata_patch(imagem: Path | None) -> dict:
    if imagem is None:
        return {}
    metadata = imagem.parent / "metadata.json"
    if not metadata.is_file() or metadata.is_symlink() or metadata.stat().st_size > 1024 * 1024:
        return {}
    try:
        dados = json.loads(metadata.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return dados if isinstance(dados, dict) else {}


def carregar_resultado(
    caminho_json: Path,
    *,
    raiz_projeto: Path,
    raiz_dataset: Path,
    raiz_analises: Path,
) -> ResultadoVisualizacao:
    """Lê `resultado.json` e resolve somente arquivos dentro das raízes permitidas."""
    caminho_json = Path(caminho_json)
    if not _contido(caminho_json, raiz_analises) or caminho_json.is_symlink():
        raise ValueError("resultado.json fora da pasta de análises")
    if caminho_json.stat().st_size > LIMITE_JSON_BYTES:
        raise ValueError("resultado.json excede o limite de 10 MB")
    dados = json.loads(caminho_json.read_text(encoding="utf-8"))
    if not isinstance(dados, dict) or not isinstance(dados.get("estatisticas"), dict):
        raise ValueError("estrutura de resultado.json inesperada")

    pasta_analise = caminho_json.parent
    itens: list[ItemResultado] = []
    modelo: dict = {}
    for bruto in dados.get("resultados") or []:
        if not isinstance(bruto, dict):
            continue
        modelo = modelo or (bruto.get("modelo") if isinstance(bruto.get("modelo"), dict) else {})
        original = _arquivo_seguro(bruto.get("entrada"), raiz_projeto, raiz_dataset)
        metadata = _metadata_patch(original)
        patch_id = str(metadata.get("patch_id") or (original.parent.name if original else ""))
        overlay = None
        if patch_id:
            overlay = _arquivo_seguro(
                pasta_analise / f"{patch_id}-deteccoes.png", raiz_projeto, raiz_analises
            )
        multibanda = None
        if original is not None:
            multibanda = _arquivo_seguro(original.parent / "multiband.tif", raiz_projeto, raiz_dataset)
        deteccoes = []
        for item in bruto.get("deteccoes") or []:
            try:
                caixa = tuple(float(valor) for valor in item["caixa"])
                deteccoes.append(
                    DeteccaoVisual(str(item["classe"]), float(item["confianca"]), caixa)  # type: ignore[arg-type]
                )
            except (KeyError, TypeError, ValueError):
                continue
        nuvens = bruto.get("nuvens_pct")
        itens.append(
            ItemResultado(
                patch_id=patch_id or "recorte",
                cena=str(metadata.get("scene_id", "")),
                data=str(metadata.get("date", "")),
                nuvens_pct=float(nuvens) if isinstance(nuvens, (int, float)) else None,
                imagem_original=original,
                imagem_analisada=overlay,
                multibanda=multibanda,
                colecao=str(metadata.get("collection", "")),
                deteccoes=tuple(sorted(deteccoes, key=lambda d: -d.confianca)),
            )
        )
    classes = modelo.get("classes") if isinstance(modelo.get("classes"), dict) else {}
    relatorio = _arquivo_seguro(dados.get("caminho_relatorio"), raiz_projeto, raiz_analises)
    return ResultadoVisualizacao(
        analysis_id=str(dados.get("analysis_id", pasta_analise.name)),
        criado_em=str(dados.get("criado_em", "")),
        regiao=str(dados.get("regiao", "")),
        periodo_inicio=str(dados.get("periodo_inicio", "")),
        periodo_fim=str(dados.get("periodo_fim", "")),
        cenas=tuple(str(c) for c in dados.get("scene_ids") or ()),
        modelo_nome=str(modelo.get("nome", "")),
        modelo_versao=str(dados.get("modelo_versao", "")),
        modelo_sha256=str(dados.get("modelo_hash", "")),
        modelo_classes=tuple(str(v) for _, v in sorted(classes.items(), key=lambda kv: str(kv[0]))),
        # O ServicoAnaliseAgricola executa somente o DetectorAgricola.
        tarefa="deteccao",
        dispositivo=str(dados.get("dispositivo", "")),
        estatisticas=dados["estatisticas"],
        itens=tuple(itens),
        caminho_json=caminho_json.resolve(),
        caminho_relatorio=relatorio,
        dados=dados,
    )


@dataclass(frozen=True)
class IndicadorVegetacao:
    indice: str
    media: float
    minimo: float
    maximo: float
    pixels_validos_pct: float


def indicadores_vegetacao(item: ItemResultado) -> list[IndicadorVegetacao]:
    """Média de NDVI/EVI do recorte inteiro, somente com escala documentada.

    Usa a máscara do GeoTIFF multibanda, descarta nodata (-9999) e valores fora
    da faixa válida. É um indicador do recorte, não de um talhão específico.
    """
    escalas = ESCALA_INDICES.get(item.colecao)
    if not escalas or item.multibanda is None:
        return []
    import numpy as np
    import rasterio

    resultado: list[IndicadorVegetacao] = []
    with rasterio.open(item.multibanda) as raster:
        for indice_banda, nome in enumerate(raster.descriptions, start=1):
            if nome not in escalas:
                continue
            dados = raster.read(indice_banda, masked=True)
            total = dados.size
            invalidos = (dados == NODATA_INDICE) | (dados < FAIXA_INDICE_BRUTO[0]) | (
                dados > FAIXA_INDICE_BRUTO[1]
            )
            validos = np.ma.masked_where(np.ma.filled(invalidos, True), dados)
            quantidade = int(validos.count())
            if not quantidade:
                continue
            escala = escalas[nome]
            resultado.append(
                IndicadorVegetacao(
                    indice=nome,
                    media=float(validos.mean()) * escala,
                    minimo=float(validos.min()) * escala,
                    maximo=float(validos.max()) * escala,
                    pixels_validos_pct=100.0 * quantidade / total,
                )
            )
    return resultado


def modelo_classificacao_aprovado(pasta_modelos: Path | None = None) -> dict | None:
    """Ainda não existe registro de modelos de classificação aprovados (entrega E9).

    Enquanto não houver, a interface deve mostrar a cultura como não determinada.
    """
    return None


# --------------------------------------------------------------------------- Exportação

CAMPOS_CSV = (
    "analysis_id",
    "regiao",
    "patch_id",
    "cena",
    "data_cena",
    "tarefa",
    "classe",
    "confianca",
    "x1_px",
    "y1_px",
    "x2_px",
    "y2_px",
    "modelo_versao",
    "modelo_sha256",
)


def _gravar_atomico(destino: Path, escrever) -> None:
    destino.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        "w", prefix=f".{destino.name}.", suffix=".tmp", dir=destino.parent,
        delete=False, encoding="utf-8", newline="",
    ) as arquivo:
        temporario = Path(arquivo.name)
        escrever(arquivo)
        arquivo.flush()
        os.fsync(arquivo.fileno())
    try:
        os.replace(temporario, destino)
    finally:
        temporario.unlink(missing_ok=True)


def exportar_csv(resultado: ResultadoVisualizacao, destino: Path) -> int:
    """Uma linha por detecção; coordenadas em pixels. Retorna o número de linhas."""
    linhas = [
        {
            "analysis_id": resultado.analysis_id,
            "regiao": resultado.regiao,
            "patch_id": item.patch_id,
            "cena": item.cena,
            "data_cena": item.data,
            "tarefa": "deteccao_de_talhoes",
            "classe": deteccao.classe,
            "confianca": f"{deteccao.confianca:.4f}",
            "x1_px": f"{deteccao.caixa_px[0]:.1f}",
            "y1_px": f"{deteccao.caixa_px[1]:.1f}",
            "x2_px": f"{deteccao.caixa_px[2]:.1f}",
            "y2_px": f"{deteccao.caixa_px[3]:.1f}",
            "modelo_versao": resultado.modelo_versao,
            "modelo_sha256": resultado.modelo_sha256,
        }
        for item in resultado.itens
        for deteccao in item.deteccoes
    ]

    def escrever(arquivo) -> None:
        escritor = csv.DictWriter(arquivo, fieldnames=CAMPOS_CSV)
        escritor.writeheader()
        escritor.writerows(linhas)

    _gravar_atomico(destino, escrever)
    return len(linhas)


def exportar_json(resultado: ResultadoVisualizacao, destino: Path) -> None:
    dados = dict(resultado.dados)
    dados["observacoes"] = {
        "tarefa": "detecção por caixas em pixels (classes do modelo não validadas)",
        "area": AVISO_AREA,
        "cultura": AVISO_CULTURA,
    }

    def escrever(arquivo) -> None:
        json.dump(dados, arquivo, ensure_ascii=False, indent=2)
        arquivo.write("\n")

    _gravar_atomico(destino, escrever)


# --------------------------------------------------------------------------- Histórico


def _data_registro(registro: RegistroHistoricoAnalise) -> date | None:
    try:
        return datetime.fromisoformat(registro.criado_em.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def filtrar_historico(
    registros: Iterable[RegistroHistoricoAnalise],
    texto: str = "",
    ultimos_dias: int | None = None,
    hoje: date | None = None,
) -> list[RegistroHistoricoAnalise]:
    """Filtra por texto (região, cena, modelo ou ID) e por idade da análise."""
    alvo = texto.strip().casefold()
    limite = (hoje or date.today()) - timedelta(days=ultimos_dias) if ultimos_dias else None
    filtrados = []
    for registro in registros:
        if alvo:
            campos = (registro.regiao, registro.scene_id, registro.modelo_versao, registro.analysis_id)
            if not any(alvo in str(campo).casefold() for campo in campos):
                continue
        if limite is not None:
            criado = _data_registro(registro)
            if criado is None or criado < limite:
                continue
        filtrados.append(registro)
    return filtrados
