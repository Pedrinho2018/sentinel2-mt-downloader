from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from pystac_client import Client

from gerar_dataset_soja import (
    DataAPIError,
    bbox_intersecta,
    buscar_itens,
    carregar_config,
    compor_patch,
    grid_patches,
    iterar_meses,
    salvar_preview,
    sessao_http,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PADRAO = ROOT / "config" / "config.yaml"

CAMPOS = [
    "patch_id",
    "spatial_id",
    "mes",
    "split",
    "row",
    "col",
    "cropland_pct",
    "minlon",
    "minlat",
    "maxlon",
    "maxlat",
    "clean_coverage_pct",
    "obs_2plus_pct",
    "fontes_usadas",
    "source_ids",
    "source_dates",
    "ndvi_medio",
    "preview",
    "label",
    "status_rotulacao",
    "revisor",
    "observacao",
]


def sid(patch: dict) -> str:
    return f"r{int(patch['row']):04d}_c{int(patch['col']):04d}"


def ordenar_grid(grid: list[dict], seed: int) -> list[dict]:
    return sorted(
        grid,
        key=lambda p: hashlib.sha256(
            f"{seed}:{sid(p)}".encode("utf-8")
        ).hexdigest(),
    )


def definir_split(spatial_id: str, seed: int, treino: float, validacao: float) -> str:
    numero = int(
        hashlib.sha256(
            f"split:{seed}:{spatial_id}".encode("utf-8")
        ).hexdigest()[:12],
        16,
    )
    valor = (numero % 1_000_000) / 1_000_000
    if valor < treino:
        return "treino"
    if valor < treino + validacao:
        return "validacao"
    return "teste"


def quotas_balanceadas(meses: list[str], total: int) -> dict[str, int]:
    base, resto = divmod(total, len(meses))
    return {
        mes: base + (1 if i < resto else 0)
        for i, mes in enumerate(meses)
    }


def ler_npy_asset(
    sessao,
    data_api_url: str,
    colecao: str,
    item_id: str,
    asset: str,
    bbox,
    tamanho: int,
    crs_destino: str,
    timeout: int,
) -> tuple[np.ndarray, np.ndarray]:
    bbox_txt = ",".join(f"{v:.10f}" for v in bbox)
    url = (
        f"{data_api_url.rstrip('/')}/item/bbox/"
        f"{bbox_txt}/{tamanho}x{tamanho}.npy"
    )
    params = [
        ("collection", colecao),
        ("item", item_id),
        ("assets", asset),
        ("asset_as_band", "true"),
        ("coord_crs", "EPSG:4326"),
        ("dst_crs", crs_destino),
        ("resampling", "nearest"),
        ("reproject", "nearest"),
        ("return_mask", "true"),
    ]
    resposta = sessao.get(url, params=params, timeout=timeout)
    if resposta.status_code != 200:
        texto = resposta.text[:250].replace("\n", " ")
        raise DataAPIError(f"HTTP {resposta.status_code}: {texto}")
    try:
        arr = np.load(io.BytesIO(resposta.content), allow_pickle=False)
    except Exception as exc:
        raise DataAPIError(f"NPY inválido: {exc}") from exc
    if arr.ndim != 3 or arr.shape[0] < 2:
        raise DataAPIError(f"shape inesperado: {arr.shape}")
    return arr[0], arr[-1] > 0


def carregar_cache(caminho: Path) -> dict[str, float]:
    if not caminho.exists():
        return {}
    cache: dict[str, float] = {}
    with caminho.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            try:
                cache[row["spatial_id"]] = float(row["cropland_pct"])
            except (KeyError, ValueError):
                continue
    return cache


def salvar_cache(caminho: Path, cache: dict[str, float]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["spatial_id", "cropland_pct"])
        writer.writeheader()
        for spatial_id in sorted(cache):
            writer.writerow(
                {
                    "spatial_id": spatial_id,
                    "cropland_pct": f"{cache[spatial_id]:.4f}",
                }
            )


def buscar_worldcover(cliente: Client, colecao: str, bbox, ano: int):
    busca = cliente.search(
        collections=[colecao],
        bbox=bbox,
        datetime=f"{ano}-01-01/{ano}-12-31",
        max_items=500,
    )
    return list(busca.items())


def cropland_pct_patch(
    sessao,
    itens,
    data_api_url: str,
    colecao: str,
    asset: str,
    classe_cropland: int,
    bbox,
    tamanho: int,
    crs_destino: str,
    timeout: int,
    cobertura_min_pct: float,
) -> float | None:
    candidatos = [item for item in itens if bbox_intersecta(item.bbox, bbox)]
    if not candidatos:
        return None

    classes = np.zeros((tamanho, tamanho), dtype=np.int16)
    preenchido = np.zeros((tamanho, tamanho), dtype=bool)

    for item in candidatos:
        try:
            dados, mask = ler_npy_asset(
                sessao,
                data_api_url,
                colecao,
                item.id,
                asset,
                bbox,
                tamanho,
                crs_destino,
                timeout,
            )
        except Exception:
            continue
        novos = mask & ~preenchido
        if novos.any():
            classes[novos] = np.rint(dados[novos]).astype(np.int16)
            preenchido[novos] = True
        if preenchido.all():
            break

    cobertura = float(preenchido.mean() * 100.0)
    if cobertura < cobertura_min_pct:
        return None
    return float(
        ((classes == classe_cropland) & preenchido).sum()
        * 100.0
        / max(int(preenchido.sum()), 1)
    )


def preparar_pool_agricola(
    cliente: Client,
    sessao,
    grid: list[dict],
    cfg: dict,
    alvo: int,
    timeout: int,
    sem_mascara: bool,
) -> list[dict]:
    mcfg = cfg.get("mascara_agricola", {})
    usar = bool(mcfg.get("usar", True)) and not sem_mascara
    if not usar:
        pool = []
        for patch in grid[:alvo]:
            p = dict(patch)
            p["cropland_pct"] = 100.0
            pool.append(p)
        return pool

    colecao = str(mcfg.get("colecao", "esa-worldcover"))
    asset = str(mcfg.get("asset", "map"))
    ano = int(mcfg.get("ano_referencia", 2021))
    classe = int(mcfg.get("classe_cropland", 40))
    minimo = float(mcfg.get("cropland_min_pct", 35.0))
    cobertura_min = float(mcfg.get("cobertura_min_pct", 95.0))
    amostra_px = int(mcfg.get("tamanho_amostra_px", 64))
    cache_path = ROOT / str(
        mcfg.get("cache", "catalogo/cache_mascara_agricola.csv")
    )

    itens = buscar_worldcover(
        cliente,
        colecao,
        cfg["aoi"]["bbox_wgs84"],
        ano,
    )
    if not itens:
        raise RuntimeError(
            f"Coleção de máscara agrícola {colecao} não retornou itens."
        )

    cache = carregar_cache(cache_path)
    pool: list[dict] = []
    avaliados = 0
    novos_cache = 0

    print(
        f"\n[MÁSCARA AGRÍCOLA] {colecao} {ano} | "
        f"cropland >= {minimo:.1f}%"
    )

    for patch in grid:
        if len(pool) >= alvo:
            break
        avaliados += 1
        spatial_id = sid(patch)
        pct = cache.get(spatial_id)
        if pct is None:
            pct = cropland_pct_patch(
                sessao=sessao,
                itens=itens,
                data_api_url=cfg["fonte"]["data_api_url"],
                colecao=colecao,
                asset=asset,
                classe_cropland=classe,
                bbox=patch["bbox_wgs84"],
                tamanho=amostra_px,
                crs_destino=cfg["aoi"]["crs_metrico"],
                timeout=timeout,
                cobertura_min_pct=cobertura_min,
            )
            if pct is not None:
                cache[spatial_id] = pct
                novos_cache += 1
                if novos_cache % 25 == 0:
                    salvar_cache(cache_path, cache)
        if pct is None or pct < minimo:
            continue
        p = dict(patch)
        p["cropland_pct"] = pct
        pool.append(p)

    salvar_cache(cache_path, cache)
    print(
        f"  avaliados={avaliados} | agrícolas={len(pool)} | "
        f"cache={cache_path.relative_to(ROOT)}"
    )
    return pool


def normalizar(row: dict, seed: int, treino: float, validacao: float) -> dict:
    out = {campo: row.get(campo, "") for campo in CAMPOS}
    if out["spatial_id"] and not out["split"]:
        out["split"] = definir_split(
            out["spatial_id"], seed, treino, validacao
        )
    if not out["status_rotulacao"]:
        out["status_rotulacao"] = "ROTULADO" if out["label"] else "PENDENTE"
    return out


def carregar_catalogo(
    caminho: Path,
    seed: int,
    treino: float,
    validacao: float,
) -> list[dict]:
    if not caminho.exists():
        return []
    with caminho.open("r", encoding="utf-8-sig", newline="") as f:
        return [
            normalizar(row, seed, treino, validacao)
            for row in csv.DictReader(f)
            if row.get("patch_id")
        ]


def escrever_catalogo(caminho: Path, registros: list[dict]) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    registros = sorted(
        registros,
        key=lambda r: (r.get("mes", ""), r.get("spatial_id", "")),
    )
    with caminho.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CAMPOS)
        writer.writeheader()
        for row in registros:
            writer.writerow({campo: row.get(campo, "") for campo in CAMPOS})


def anexar(caminho: Path, row: dict) -> None:
    caminho.parent.mkdir(parents=True, exist_ok=True)
    existe = caminho.exists() and caminho.stat().st_size > 0
    with caminho.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CAMPOS)
        if not existe:
            writer.writeheader()
        writer.writerow({campo: row.get(campo, "") for campo in CAMPOS})


def gerar_fila(caminho: Path, registros: list[dict]) -> None:
    campos = [
        "ordem",
        "patch_id",
        "spatial_id",
        "mes",
        "split",
        "preview",
        "cropland_pct",
        "clean_coverage_pct",
        "obs_2plus_pct",
        "ndvi_medio",
        "label",
        "status_rotulacao",
        "revisor",
        "observacao",
    ]
    registros = sorted(
        registros,
        key=lambda r: (r.get("mes", ""), r.get("spatial_id", "")),
    )
    caminho.parent.mkdir(parents=True, exist_ok=True)
    with caminho.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=campos)
        writer.writeheader()
        for i, row in enumerate(registros, start=1):
            out = {campo: row.get(campo, "") for campo in campos}
            out["ordem"] = i
            writer.writerow(out)


def estatisticas(registros: list[dict], meses: list[str]) -> dict:
    por_mes = {mes: 0 for mes in meses}
    por_split = {"treino": 0, "validacao": 0, "teste": 0}
    meses_por_sid: dict[str, set[str]] = {}

    for row in registros:
        mes = row.get("mes", "")
        if mes in por_mes:
            por_mes[mes] += 1
        split = row.get("split", "")
        if split in por_split:
            por_split[split] += 1
        spatial_id = row.get("spatial_id", "")
        if spatial_id and mes:
            meses_por_sid.setdefault(spatial_id, set()).add(mes)

    alvo_meses = set(meses)
    series_completas = sum(
        1 for presentes in meses_por_sid.values()
        if alvo_meses.issubset(presentes)
    )
    return {
        "por_mes": por_mes,
        "por_split_imagens": por_split,
        "spatial_ids_unicos": len(meses_por_sid),
        "spatial_ids_serie_completa": series_completas,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Gera uma fila balanceada de imagens Sentinel-2 para catalogação de soja, "
            "com máscara agrícola e split por localização."
        )
    )
    parser.add_argument("--config", type=Path, default=CONFIG_PADRAO)
    parser.add_argument("--meta-total", type=int)
    parser.add_argument("--mes", help="Modo de teste: processa apenas YYYY-MM")
    parser.add_argument("--limpar", action="store_true")
    parser.add_argument("--sem-mascara-agricola", action="store_true")
    args = parser.parse_args()

    cfg = carregar_config(args.config)
    dcfg = cfg.get("dataset_5000", {})
    pcfg = cfg["patches"]
    fonte = cfg["fonte"]
    http_cfg = cfg.get("http", {})
    busca_cfg = cfg["busca"]

    meta = args.meta_total or int(dcfg.get("meta_total_imagens", 5000))
    inicio = cfg["periodo"]["inicio"]
    fim = cfg["periodo"]["fim"]
    meses = [
        ini.strftime("%Y-%m")
        for ini, _ in iterar_meses(inicio, fim)
        if not args.mes or ini.strftime("%Y-%m") == args.mes
    ]
    if not meses:
        print("[ERRO] Nenhum mês selecionado.")
        return 2

    quotas = quotas_balanceadas(meses, meta)
    seed = int(dcfg.get("seed_espacial", 20260820))
    split_cfg = dcfg.get("split", {})
    treino = float(split_cfg.get("treino_pct", 70)) / 100.0
    validacao = float(split_cfg.get("validacao_pct", 15)) / 100.0
    teste = float(split_cfg.get("teste_pct", 15)) / 100.0
    if abs(treino + validacao + teste - 1.0) > 1e-6:
        print("[ERRO] dataset_5000.split deve somar 100%.")
        return 2

    tamanho = int(pcfg["tamanho_px"])
    resolucao = float(pcfg["resolucao_m"])
    clean_min = float(pcfg.get("cobertura_limpa_min_pct", 99.5))
    classes_validas = set(
        int(x) for x in pcfg.get("scl_classes_validas", [4, 5, 6])
    )
    margem = int(pcfg.get("margem_nuvem_px", 4))
    saida_px = int(pcfg.get("preview_saida_px", 768))
    qualidade = int(pcfg.get("qualidade_jpeg", 95))
    pmin = float(pcfg.get("percentil_min", 2))
    pmax = float(pcfg.get("percentil_max", 98))
    salvar_npz = bool(pcfg.get("salvar_npz", False))

    cloud_max = float(busca_cfg.get("nuvem_cena_max_pct", 80))
    max_itens_mes = int(busca_cfg.get("max_itens_busca_mes", 120))
    max_cenas_patch = int(busca_cfg.get("max_cenas_por_patch", 10))
    timeout = int(http_cfg.get("timeout_segundos", 90))
    tentativas = int(http_cfg.get("tentativas", 4))
    backoff = float(http_cfg.get("backoff_segundos", 1.0))

    out_cfg = cfg.get("saida_5000", {})
    pasta_saida = ROOT / str(
        out_cfg.get("pasta_patches", "data/dataset_soja_5000")
    )
    catalogo = ROOT / str(
        out_cfg.get("catalogo", "catalogo/catalogo_soja_5000.csv")
    )
    fila = ROOT / str(
        out_cfg.get("fila", "catalogo/fila_catalogacao_5000.csv")
    )
    resumo_path = ROOT / str(
        out_cfg.get("resumo", "catalogo/resumo_soja_5000.json")
    )

    if args.limpar:
        if pasta_saida.exists():
            shutil.rmtree(pasta_saida)
        catalogo.unlink(missing_ok=True)
        fila.unlink(missing_ok=True)
        resumo_path.unlink(missing_ok=True)

    cliente = Client.open(fonte["stac_url"])
    sessao = sessao_http(tentativas, backoff)

    grid = ordenar_grid(
        list(
            grid_patches(
                cfg["aoi"]["bbox_wgs84"],
                cfg["aoi"]["crs_metrico"],
                tamanho,
                resolucao,
            )
        ),
        seed,
    )
    if not grid:
        print("[ERRO] AOI não gerou grid.")
        return 2

    maior_quota = max(quotas.values())
    reserva = float(dcfg.get("fator_reserva_espacial", 2.0))
    alvo_pool = min(len(grid), int(math.ceil(maior_quota * reserva)))

    print("=" * 92)
    print(" DATASET SOJA 5000 | produção balanceada")
    print("=" * 92)
    print(f"Meta total: {meta}")
    print(f"Meses: {', '.join(meses)}")
    print(f"Cotas: {quotas}")
    print(f"Grid total: {len(grid)} | pool alvo: {alvo_pool}")
    print("Split por spatial_id: 70% treino | 15% validação | 15% teste")
    print("Mesmo spatial_id nunca muda de split.")
    print("Cenas completas salvas no PC: NÃO")

    pool = preparar_pool_agricola(
        cliente=cliente,
        sessao=sessao,
        grid=grid,
        cfg=cfg,
        alvo=alvo_pool,
        timeout=timeout,
        sem_mascara=args.sem_mascara_agricola,
    )
    if len(pool) < maior_quota:
        print(
            f"[ERRO] Apenas {len(pool)} localizações agrícolas disponíveis; "
            f"precisamos de pelo menos {maior_quota}."
        )
        return 2

    registros = carregar_catalogo(catalogo, seed, treino, validacao)
    if registros:
        escrever_catalogo(catalogo, registros)
    existentes = {row["patch_id"] for row in registros}
    por_mes_existente: dict[str, int] = {}
    for row in registros:
        mes = row.get("mes", "")
        por_mes_existente[mes] = por_mes_existente.get(mes, 0) + 1

    total_descartados = 0
    total_erros_api = 0
    erros_amostra: list[str] = []

    for mes_inicio, mes_fim in iterar_meses(inicio, fim):
        mes = mes_inicio.strftime("%Y-%m")
        if mes not in quotas:
            continue
        quota = quotas[mes]
        aprovados_mes = por_mes_existente.get(mes, 0)
        if aprovados_mes >= quota:
            print(f"\n[{mes}] já concluído: {aprovados_mes}/{quota}")
            continue

        print(f"\n[{mes}] buscando cenas Sentinel-2...")
        itens = buscar_itens(
            cliente=cliente,
            colecao=fonte["colecao"],
            bbox=cfg["aoi"]["bbox_wgs84"],
            inicio=mes_inicio,
            fim=mes_fim,
            cloud_max=cloud_max,
            max_itens=max_itens_mes,
        )
        print(f"  cenas no AOI: {len(itens)}")
        if not itens:
            continue

        avaliados = 0
        for patch in pool:
            if aprovados_mes >= quota:
                break
            spatial_id = sid(patch)
            patch_id = f"{mes}_{spatial_id}"
            if patch_id in existentes:
                continue

            avaliados += 1
            resultado, status = compor_patch(
                sessao=sessao,
                itens=itens,
                data_api_url=fonte["data_api_url"],
                colecao=fonte["colecao"],
                bbox_patch=patch["bbox_wgs84"],
                tamanho=tamanho,
                crs_destino=cfg["aoi"]["crs_metrico"],
                timeout=timeout,
                classes_validas=classes_validas,
                margem=margem,
                max_cenas=max_cenas_patch,
            )
            if resultado is None:
                total_descartados += 1
                if "HTTP" in status or "NPY" in status or "shape" in status:
                    total_erros_api += 1
                    if len(erros_amostra) < 5:
                        erros_amostra.append(status)
                continue

            coverage = float(resultado["coverage_pct"])
            if coverage < clean_min:
                total_descartados += 1
                continue

            pasta_patch = pasta_saida / mes / patch_id
            preview = pasta_patch / "preview_rgb.jpg"
            salvar_preview(
                resultado["B02"],
                resultado["B03"],
                resultado["B04"],
                resultado["VALID"],
                preview,
                saida_px,
                pmin,
                pmax,
                qualidade,
            )

            if salvar_npz:
                pasta_patch.mkdir(parents=True, exist_ok=True)
                np.savez_compressed(
                    pasta_patch / "dados_cientificos.npz",
                    B02=resultado["B02"],
                    B03=resultado["B03"],
                    B04=resultado["B04"],
                    B08=resultado["B08"],
                    NDVI=resultado["NDVI"],
                    VALID=resultado["VALID"],
                    OBS_COUNT=resultado["OBS_COUNT"],
                )

            fontes = resultado["fontes"]
            bbox_patch = patch["bbox_wgs84"]
            ndvi_valid = resultado["NDVI"][resultado["VALID"]]
            ndvi_valid = ndvi_valid[np.isfinite(ndvi_valid)]
            ndvi_medio = (
                float(ndvi_valid.mean()) if ndvi_valid.size else float("nan")
            )
            split = definir_split(spatial_id, seed, treino, validacao)

            row = {
                "patch_id": patch_id,
                "spatial_id": spatial_id,
                "mes": mes,
                "split": split,
                "row": patch["row"],
                "col": patch["col"],
                "cropland_pct": f"{float(patch.get('cropland_pct', 100.0)):.4f}",
                "minlon": f"{bbox_patch[0]:.8f}",
                "minlat": f"{bbox_patch[1]:.8f}",
                "maxlon": f"{bbox_patch[2]:.8f}",
                "maxlat": f"{bbox_patch[3]:.8f}",
                "clean_coverage_pct": f"{coverage:.4f}",
                "obs_2plus_pct": f"{resultado['obs2_pct']:.4f}",
                "fontes_usadas": len(fontes),
                "source_ids": ";".join(f["item_id"] for f in fontes),
                "source_dates": ";".join(f["data"] for f in fontes),
                "ndvi_medio": (
                    "" if not np.isfinite(ndvi_medio) else f"{ndvi_medio:.6f}"
                ),
                "preview": str(preview.relative_to(ROOT)),
                "label": "",
                "status_rotulacao": "PENDENTE",
                "revisor": "",
                "observacao": "",
            }
            anexar(catalogo, row)
            registros.append(row)
            existentes.add(patch_id)
            aprovados_mes += 1
            por_mes_existente[mes] = aprovados_mes

            print(
                f"  [OK] {patch_id} | crop={float(patch.get('cropland_pct', 100.0)):.1f}% "
                f"| limpo={coverage:.2f}% | split={split}"
            )

        escrever_catalogo(catalogo, registros)
        print(
            f"  resumo {mes}: avaliados={avaliados} | "
            f"aprovados={aprovados_mes}/{quota}"
        )

    escrever_catalogo(catalogo, registros)
    gerar_fila(fila, registros)
    stats = estatisticas(registros, meses)
    deficits = {
        mes: max(0, quotas[mes] - stats["por_mes"].get(mes, 0))
        for mes in meses
    }
    completo = all(v == 0 for v in deficits.values())

    resumo = {
        "gerado_em_utc": datetime.now(timezone.utc).isoformat(),
        "meta_total": meta,
        "quotas_por_mes": quotas,
        "deficits_por_mes": deficits,
        "dataset_completo": completo,
        "patches_gerados": len(registros),
        "usa_mascara_agricola": not args.sem_mascara_agricola,
        "usa_rasterio_gdal": False,
        "salva_cenas_completas": False,
        "patches_descartados_execucao": total_descartados,
        "erros_api_estimados": total_erros_api,
        "erros_amostra": erros_amostra,
        **stats,
        "catalogo": str(catalogo.relative_to(ROOT)),
        "fila_catalogacao": str(fila.relative_to(ROOT)),
        "classes": dcfg.get(
            "classes_rotulacao", ["SOJA", "NAO_SOJA", "INCERTO"]
        ),
        "regra_split": (
            "split determinístico por spatial_id; o mesmo local nunca aparece "
            "em conjuntos diferentes"
        ),
    }
    resumo_path.parent.mkdir(parents=True, exist_ok=True)
    resumo_path.write_text(
        json.dumps(resumo, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\n" + "=" * 92)
    print(" RESUMO DATASET 5000")
    print("=" * 92)
    print(json.dumps(resumo, ensure_ascii=False, indent=2))

    if not registros:
        return 1
    if not completo:
        print(
            "\n[ATENÇÃO] Meta ainda incompleta. Nada foi perdido. "
            "Rode novamente SEM --limpar para continuar."
        )
        return 3

    print(f"\n[OK] Fila pronta: {fila}")
    print(f"[OK] Catálogo técnico: {catalogo}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
