"""Descoberta, importação, integridade e filtros de imagens do Laboratório de IA.

- Recortes do sistema são referenciados no lugar (sem cópia): o PNG RGB e o
  GeoTIFF multibanda continuam onde o pipeline os gravou.
- Imagens externas são copiadas para a pasta do laboratório com nome derivado
  do SHA-256; o arquivo original do usuário não é alterado.
- Nenhuma função aqui modifica GeoTIFFs científicos ou catálogos do pipeline.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
import csv
from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import re
import shutil

from ..configuracao import ConfiguracaoProjeto
from .projetos import ESTADOS_ROTULO, ImagemDataset


EXTENSOES_IMAGEM = {".png", ".jpg", ".jpeg"}
LIMITE_IMAGEM_BYTES = 100 * 1024 * 1024
STATUS_APROVADOS = {"APROVADO", "APROVADO_SEM_SCL"}
TILE_RE = re.compile(r"_(\d{6})_\d{8}$")
PATCH_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")
Progresso = Callable[[int, int], None]


@dataclass(frozen=True)
class RecorteDisponivel:
    patch_id: str
    rgb: Path
    geotiff: Path | None
    cena: str
    data: str
    colecao: str
    nuvem_pct: float | None
    largura: int
    altura: int


# --------------------------------------------------------------------------- utilitários


def sha256_arquivo(caminho: Path) -> str:
    digest = hashlib.sha256()
    with caminho.open("rb") as arquivo:
        for bloco in iter(lambda: arquivo.read(1024 * 1024), b""):
            digest.update(bloco)
    return digest.hexdigest()


def safra_de(data: str) -> str:
    """Ano-safra agrícola de setembro a agosto (ex.: 2026-03-22 -> "2025/2026").

    Convenção usada para soja/milho em MT; datas inválidas retornam "".
    """
    correspondencia = re.fullmatch(r"(\d{4})-(\d{2})-\d{2}", data or "")
    if not correspondencia:
        return ""
    ano, mes = int(correspondencia.group(1)), int(correspondencia.group(2))
    inicio = ano if mes >= 9 else ano - 1
    return f"{inicio}/{inicio + 1}"


def tile_de(cena: str) -> str:
    """Código da grade BDC no ID da cena (ex.: S2-16D_V2_014015_20260423 -> 014015)."""
    correspondencia = TILE_RE.search(cena or "")
    return correspondencia.group(1) if correspondencia else ""


def relativo(caminho: Path, raiz: Path) -> str:
    return caminho.resolve().relative_to(raiz.resolve()).as_posix()


def _dentro(caminho: Path, raiz: Path) -> bool:
    try:
        caminho.resolve().relative_to(raiz.resolve())
    except ValueError:
        return False
    return True


def _float(valor: object) -> float | None:
    try:
        return float(valor) if valor not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _int(valor: object) -> int:
    try:
        return int(valor)
    except (TypeError, ValueError):
        return 0


def resolver_pasta_laboratorio(config: ConfiguracaoProjeto) -> Path:
    """Pasta do laboratório; precisa ficar dentro da raiz da aplicação e sem links."""
    raiz = config.raiz.resolve()
    pasta = config.caminho(config.laboratorio.pasta).resolve(strict=False)
    try:
        relativo_raiz = pasta.relative_to(raiz)
    except ValueError as exc:
        raise ValueError("laboratorio.pasta deve permanecer dentro da raiz da aplicação") from exc
    if not relativo_raiz.parts:
        raise ValueError("laboratorio.pasta não pode ser a raiz da aplicação")
    atual = raiz
    for parte in relativo_raiz.parts:
        atual = atual / parte
        if atual.is_symlink():
            raise ValueError("laboratorio.pasta não pode usar links simbólicos")
    return pasta


# --------------------------------------------------------------------------- descoberta


def descobrir_recortes(config: ConfiguracaoProjeto) -> tuple[list[RecorteDisponivel], str]:
    """Lista recortes RGB aprovados: pelo catálogo, ou pela pasta se ele não existir."""
    raiz_dataset = config.caminho(config.dataset.pasta)
    catalogo = config.caminho(config.dataset.catalogo)
    if catalogo.is_file():
        return _recortes_do_catalogo(config, catalogo, raiz_dataset), "catalogo"
    return _recortes_da_pasta(raiz_dataset), "pasta"


def _recortes_do_catalogo(
    config: ConfiguracaoProjeto, catalogo: Path, raiz_dataset: Path
) -> list[RecorteDisponivel]:
    recortes = []
    with catalogo.open("r", newline="", encoding="utf-8-sig") as arquivo:
        for linha in csv.DictReader(arquivo):
            if str(linha.get("status", "")) not in STATUS_APROVADOS or not linha.get("rgb_png"):
                continue
            patch_id = str(linha.get("patch_id", ""))
            rgb = config.caminho(linha["rgb_png"])
            if not PATCH_ID_RE.fullmatch(patch_id) or not _arquivo_valido(rgb, raiz_dataset):
                continue
            geotiff = config.caminho(linha["geotiff_path"]) if linha.get("geotiff_path") else None
            if geotiff is not None and not _arquivo_valido(geotiff, raiz_dataset):
                geotiff = None
            recortes.append(
                RecorteDisponivel(
                    patch_id=patch_id,
                    rgb=rgb.resolve(),
                    geotiff=geotiff.resolve() if geotiff else None,
                    cena=str(linha.get("scene_id", "")),
                    data=str(linha.get("date", "")),
                    colecao=str(linha.get("collection", "")),
                    nuvem_pct=_float(linha.get("cloud_pct")),
                    largura=_int(linha.get("width")),
                    altura=_int(linha.get("height")),
                )
            )
    return sorted(recortes, key=lambda r: (r.data, r.patch_id))


def _recortes_da_pasta(raiz_dataset: Path) -> list[RecorteDisponivel]:
    """Fallback: lê `metadata.json` de cada patch quando o catálogo está ausente."""
    if not raiz_dataset.is_dir():
        return []
    recortes = []
    for diretorio, subpastas, arquivos in os.walk(raiz_dataset, followlinks=False):
        subpastas[:] = sorted(nome for nome in subpastas if not nome.startswith("."))
        if "metadata.json" not in arquivos:
            continue
        pasta = Path(diretorio)
        try:
            metadata = json.loads((pasta / "metadata.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(metadata, dict):
            continue
        pngs = sorted(nome for nome in arquivos if nome.lower().endswith(".png") and not nome.startswith("."))
        patch_id = str(metadata.get("patch_id", pasta.name))
        if len(pngs) != 1 or not PATCH_ID_RE.fullmatch(patch_id):
            continue
        rgb = pasta / pngs[0]
        geotiff = pasta / "multiband.tif"
        if not _arquivo_valido(rgb, raiz_dataset):
            continue
        recortes.append(
            RecorteDisponivel(
                patch_id=patch_id,
                rgb=rgb.resolve(),
                geotiff=geotiff.resolve() if _arquivo_valido(geotiff, raiz_dataset) else None,
                cena=str(metadata.get("scene_id", "")),
                data=str(metadata.get("date", "")),
                colecao=str(metadata.get("collection", "")),
                nuvem_pct=_float(metadata.get("cloud_pct")),
                largura=_int(metadata.get("width")),
                altura=_int(metadata.get("height")),
            )
        )
    return sorted(recortes, key=lambda r: (r.data, r.patch_id))


def _arquivo_valido(caminho: Path, raiz: Path) -> bool:
    return caminho.is_file() and not caminho.is_symlink() and _dentro(caminho, raiz)


# --------------------------------------------------------------------------- importação


def preparar_recortes(
    recortes: list[RecorteDisponivel],
    raiz: Path,
    origem: str,
    regiao: str = "",
    progresso: Progresso | None = None,
) -> list[ImagemDataset]:
    """Calcula o SHA-256 de cada PNG e monta os registros, sem copiar arquivos."""
    imagens = []
    for indice, recorte in enumerate(recortes, start=1):
        imagens.append(
            ImagemDataset(
                patch_id=recorte.patch_id,
                caminho_rgb=relativo(recorte.rgb, raiz),
                caminho_geotiff=relativo(recorte.geotiff, raiz) if recorte.geotiff else "",
                sha256=sha256_arquivo(recorte.rgb),
                origem=origem,
                cena=recorte.cena,
                tile=tile_de(recorte.cena),
                data=recorte.data,
                safra=safra_de(recorte.data),
                regiao=regiao,
                colecao=recorte.colecao,
                nuvem_pct=recorte.nuvem_pct,
                largura=recorte.largura,
                altura=recorte.altura,
            )
        )
        if progresso:
            progresso(indice, len(recortes))
    return imagens


def importar_externas(
    arquivos: Iterable[Path],
    pasta_laboratorio: Path,
    raiz: Path,
    regiao: str = "",
    progresso: Progresso | None = None,
) -> tuple[list[ImagemDataset], list[str]]:
    """Valida e copia imagens externas para `pasta_laboratorio/externas/`.

    Retorna (imagens importadas, erros legíveis). O original não é modificado.
    """
    from PIL import Image, UnidentifiedImageError

    destino_base = pasta_laboratorio / "externas"
    lista = list(arquivos)
    imagens: list[ImagemDataset] = []
    erros: list[str] = []
    for indice, origem in enumerate(lista, start=1):
        origem = Path(origem)
        try:
            if origem.is_symlink() or not origem.is_file():
                raise ValueError("arquivo inexistente ou link simbólico")
            if origem.suffix.lower() not in EXTENSOES_IMAGEM:
                raise ValueError("formato não suportado (use PNG ou JPEG)")
            if origem.stat().st_size > LIMITE_IMAGEM_BYTES:
                raise ValueError("arquivo maior que 100 MB")
            with Image.open(origem) as imagem:
                imagem.verify()
            with Image.open(origem) as imagem:
                largura, altura = imagem.size
            digest = sha256_arquivo(origem)
            destino_base.mkdir(parents=True, exist_ok=True)
            destino = destino_base / f"{digest[:24]}{origem.suffix.lower()}"
            if not destino.exists():
                temporario = destino.with_name(f".{destino.name}.tmp")
                shutil.copyfile(origem, temporario)
                if sha256_arquivo(temporario) != digest:
                    temporario.unlink(missing_ok=True)
                    raise ValueError("cópia divergente do original")
                temporario.replace(destino)
            imagens.append(
                ImagemDataset(
                    patch_id=f"ext_{digest[:24]}",
                    caminho_rgb=relativo(destino, raiz),
                    sha256=digest,
                    origem="externa",
                    regiao=regiao,
                    largura=largura,
                    altura=altura,
                )
            )
        except (OSError, ValueError, UnidentifiedImageError) as exc:
            erros.append(f"{origem.name}: {exc}")
        if progresso:
            progresso(indice, len(lista))
    return imagens, erros


# --------------------------------------------------------------------------- integridade


def verificar_integridade(imagem: ImagemDataset, raiz: Path) -> str:
    """'ok', 'ausente', 'alterada' (SHA-256 mudou) ou 'ilegivel'."""
    from PIL import Image, UnidentifiedImageError

    rgb = raiz / imagem.caminho_rgb
    if not rgb.is_file() or rgb.is_symlink() or not _dentro(rgb, raiz):
        return "ausente"
    try:
        if sha256_arquivo(rgb) != imagem.sha256:
            return "alterada"
        with Image.open(rgb) as arquivo:
            arquivo.verify()
    except (OSError, UnidentifiedImageError):
        return "ilegivel"
    if imagem.caminho_geotiff:
        geotiff = raiz / imagem.caminho_geotiff
        if not geotiff.is_file() or geotiff.is_symlink() or not _dentro(geotiff, raiz):
            return "ausente"
        from ..http import ClienteDownloadHTTP

        if not ClienteDownloadHTTP._arquivo_integro(geotiff):
            return "ilegivel"
    return "ok"


def verificar_lote(
    imagens: list[ImagemDataset], raiz: Path, progresso: Progresso | None = None
) -> dict[str, str]:
    estados = {}
    for indice, imagem in enumerate(imagens, start=1):
        estados[imagem.patch_id] = verificar_integridade(imagem, raiz)
        if progresso:
            progresso(indice, len(imagens))
    return estados


# --------------------------------------------------------------------------- filtros


def filtrar_imagens(
    imagens: Iterable[ImagemDataset],
    *,
    texto: str = "",
    safra: str = "",
    nuvem_max_pct: float | None = None,
    estado_rotulo: str = "",
    somente_duplicatas: bool = False,
    somente_problemas: bool = False,
) -> list[ImagemDataset]:
    if estado_rotulo and estado_rotulo not in ESTADOS_ROTULO:
        raise ValueError(f"Estado de rotulagem inválido: {estado_rotulo}")
    alvo = texto.strip().casefold()
    resultado = []
    for imagem in imagens:
        if alvo and not any(
            alvo in campo.casefold()
            for campo in (imagem.patch_id, imagem.cena, imagem.tile, imagem.regiao, imagem.data)
        ):
            continue
        if safra and imagem.safra != safra:
            continue
        if nuvem_max_pct is not None and imagem.nuvem_pct is not None and imagem.nuvem_pct > nuvem_max_pct:
            continue
        if estado_rotulo and imagem.estado_rotulo != estado_rotulo:
            continue
        if somente_duplicatas and not imagem.duplicata_de:
            continue
        if somente_problemas and imagem.integridade not in {"ausente", "alterada", "ilegivel"}:
            continue
        resultado.append(imagem)
    return resultado


# --------------------------------------------------------------------------- miniaturas


def caminho_miniatura(pasta_laboratorio: Path, imagem: ImagemDataset) -> Path:
    return pasta_laboratorio / "miniaturas" / f"{imagem.sha256[:32]}.jpg"


def gerar_miniatura(imagem: ImagemDataset, raiz: Path, pasta_laboratorio: Path, lado: int = 128) -> Path | None:
    """Gera (uma vez) uma miniatura JPEG em cache; nunca altera a imagem de origem."""
    from PIL import Image, UnidentifiedImageError

    destino = caminho_miniatura(pasta_laboratorio, imagem)
    if destino.is_file():
        return destino
    origem = raiz / imagem.caminho_rgb
    if not origem.is_file() or not _dentro(origem, raiz):
        return None
    try:
        destino.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(origem) as arquivo:
            miniatura = arquivo.convert("RGB")
            miniatura.thumbnail((lado, lado))
            temporario = destino.with_name(f".{destino.name}.tmp")
            miniatura.save(temporario, "JPEG", quality=85)
        temporario.replace(destino)
    except (OSError, UnidentifiedImageError):
        return None
    return destino


def resumo_safras(imagens: Iterable[ImagemDataset]) -> list[str]:
    return sorted({imagem.safra for imagem in imagens if imagem.safra}, reverse=True)


def atualizar_regiao(imagens: list[ImagemDataset], regiao: str) -> list[ImagemDataset]:
    return [replace(imagem, regiao=regiao) for imagem in imagens]
