from __future__ import annotations

import hashlib
from pathlib import Path
import re


def componente_seguro(valor: object, fallback: str = "item", limite: int = 120) -> str:
    """Converte um identificador externo em um componente de caminho não ambíguo."""
    original = str(valor).strip()
    normalizado = re.sub(r"[^A-Za-z0-9_.-]+", "_", original).strip("._")
    if not normalizado:
        normalizado = fallback
    alterado = normalizado != original or original in {".", ".."}
    reserva_hash = 13 if alterado else 0
    normalizado = normalizado[: max(1, limite - reserva_hash)]
    if alterado:
        digest = hashlib.sha256(original.encode("utf-8")).hexdigest()[:12]
        normalizado = f"{normalizado}-{digest}"
    return normalizado


def caminho_contido(raiz: Path, *componentes: str) -> Path:
    """Monta um caminho e rejeita qualquer escape da raiz configurada."""
    raiz_resolvida = raiz.expanduser().resolve()
    destino = raiz_resolvida.joinpath(*componentes).resolve()
    try:
        destino.relative_to(raiz_resolvida)
    except ValueError as exc:
        raise ValueError(f"Caminho de saída fora da raiz permitida: {destino}") from exc
    return destino
