from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class GeradorConfiguracao:
    """Monta e persiste o config.yaml sem depender da interface gráfica."""

    BBOX_PADRAO = [-61.65, -18.05, -50.20, -7.30]

    @staticmethod
    def validar_bbox(bbox: list[float]) -> list[float]:
        if len(bbox) != 4:
            raise ValueError(
                "A bounding box deve conter exatamente 4 valores: "
                "[oeste, sul, leste, norte]."
            )
        oeste, sul, leste, norte = (float(valor) for valor in bbox)
        if oeste >= leste or sul >= norte:
            raise ValueError("A bounding box deve respeitar oeste < leste e sul < norte.")
        return [oeste, sul, leste, norte]

    def gerar(self, dados: dict[str, Any]) -> dict[str, Any]:
        bbox = self.validar_bbox(dados.get("bbox", self.BBOX_PADRAO))
        bandas = dados.get("bandas", ["B02", "B03", "B04", "B08", "NDVI"])
        return {
            "stac": {
                "url": dados.get("stac_url", "https://data.inpe.br/bdc/stac/v1/"),
                "colecao": dados.get("colecao", "S2-16D-2"),
            },
            "area": {
                "nome": dados.get("nome_regiao", "Região personalizada"),
                "uf": dados.get("uf", "MT"),
                "bbox": bbox,
            },
            "periodo": {
                "inicio": dados.get("inicio", "2025-09-01"),
                "fim": dados.get("fim", "2026-04-30"),
            },
            "bandas": bandas,
            "qualidade": {
                "filtrar_nuvens": bool(dados.get("filtrar_nuvens", True)),
                "nuvem_max_pct": float(dados.get("nuvem_max_pct", 20)),
                "manter_scl": bool(dados.get("manter_scl", True)),
            },
            "preview": {
                "gerar_rgb": bool(dados.get("gerar_rgb", True)),
                "tamanho_max_px": int(dados.get("tamanho_max_px", 1600)),
                "percentil_min": float(dados.get("percentil_min", 2)),
                "percentil_max": float(dados.get("percentil_max", 98)),
                "qualidade_jpeg": int(dados.get("qualidade_jpeg", 92)),
            },
            "download": {
                "pasta": dados.get("pasta_download", "data/sentinel2"),
                "catalogo": dados.get("catalogo", "catalogo/catalogo_imagens.csv"),
                "timeout_segundos": int(dados.get("timeout_segundos", 120)),
                "chunk_mb": int(dados.get("chunk_mb", 1)),
                "max_itens_teste": int(dados.get("max_itens_teste", 5)),
                "max_candidatos_teste": int(dados.get("max_candidatos_teste", 40)),
            },
            "sincronizacao": {
                "pasta_remota": dados.get("pasta_remota", "sentinel2-mt"),
                "oauth_json": dados.get("oauth_json", "${GOOGLE_OAUTH_JSON:-}"),
                "token_json": dados.get(
                    "token_json", "${GOOGLE_TOKEN_JSON:-config/google-token.json}"
                ),
                "pasta_id": dados.get("pasta_id", "${GOOGLE_PASTA_ID:-root}"),
                "tamanho_lote": int(dados.get("tamanho_lote", 100)),
                "extensoes": dados.get(
                    "extensoes", [".tif", ".tiff", ".jpg", ".jpeg"]
                ),
            },
        }

    def salvar(self, caminho: str | Path, dados: dict[str, Any]) -> Path:
        destino = Path(caminho).expanduser()
        destino.parent.mkdir(parents=True, exist_ok=True)
        with destino.open("w", encoding="utf-8") as arquivo:
            yaml.safe_dump(
                self.gerar(dados), arquivo, allow_unicode=True, sort_keys=False
            )
        return destino


_gerador = GeradorConfiguracao()


def bbox_para_yaml(bbox: list[float]) -> list[float]:
    return _gerador.validar_bbox(bbox)


def gerar_config(dados: dict[str, Any]) -> dict[str, Any]:
    return _gerador.gerar(dados)


def salvar_config(caminho: str | Path, dados: dict[str, Any]) -> Path:
    return _gerador.salvar(caminho, dados)
