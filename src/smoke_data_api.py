from __future__ import annotations

import io
import sys

import numpy as np
import requests
from pystac_client import Client

STAC = "https://planetarycomputer.microsoft.com/api/stac/v1"
DATA = "https://planetarycomputer.microsoft.com/api/data/v1"
COLLECTION = "sentinel-2-l2a"
AOI = [-56.00, -13.00, -55.30, -12.20]


def main() -> int:
    catalog = Client.open(STAC)
    search = catalog.search(
        collections=[COLLECTION],
        bbox=AOI,
        datetime="2026-04-01/2026-04-30",
        query={"eo:cloud_cover": {"lte": 40}},
        max_items=10,
    )
    items = list(search.items())
    if not items:
        print("SMOKE FAIL: nenhuma cena encontrada")
        return 2

    item = items[0]
    ib = item.bbox
    minlon = max(AOI[0], ib[0])
    minlat = max(AOI[1], ib[1])
    maxlon = min(AOI[2], ib[2])
    maxlat = min(AOI[3], ib[3])
    if minlon >= maxlon or minlat >= maxlat:
        print("SMOKE FAIL: item sem interseção útil")
        return 3

    # Pequeno recorte no centro da interseção.
    cx = (minlon + maxlon) / 2
    cy = (minlat + maxlat) / 2
    dx = min(0.01, (maxlon - minlon) / 4)
    dy = min(0.01, (maxlat - minlat) / 4)
    bbox = f"{cx-dx:.8f},{cy-dy:.8f},{cx+dx:.8f},{cy+dy:.8f}"
    url = f"{DATA}/item/bbox/{bbox}/32x32.npy"
    params = [
        ("collection", COLLECTION),
        ("item", item.id),
        ("assets", "B04"),
        ("assets", "SCL"),
        ("asset_as_band", "true"),
        ("return_mask", "true"),
        ("resampling", "nearest"),
        ("reproject", "nearest"),
    ]

    r = requests.get(url, params=params, timeout=60)
    if r.status_code != 200:
        print(f"SMOKE FAIL HTTP {r.status_code}: {r.text[:500]}")
        return 4

    try:
        arr = np.load(io.BytesIO(r.content), allow_pickle=False)
    except Exception as exc:
        print(f"SMOKE FAIL NPY: {exc}")
        return 5

    if arr.ndim != 3 or arr.shape[0] < 3 or arr.shape[1:] != (32, 32):
        print(f"SMOKE FAIL SHAPE: {arr.shape}")
        return 6

    print(f"SMOKE OK: item={item.id} shape={arr.shape}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
