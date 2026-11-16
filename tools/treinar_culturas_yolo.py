"""Treinamento experimental YOLO para culturas em patches RGB rotulados."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

CLASSES = ("soja", "milho", "algodao", "outras")
EXT = {".png", ".jpg", ".jpeg"}


def audit_dataset(root: Path):
    counts = {}
    for split in ("train", "val"):
        counts[split] = {}
        for label in CLASSES:
            folder = root / split / label
            if not folder.is_dir():
                raise ValueError(f"Diretorio obrigatorio ausente: {folder}")
            files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in EXT]
            if not files:
                raise ValueError(f"Sem imagens em {folder}")
            counts[split][label] = len(files)
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data/culturas"))
    parser.add_argument("--model", default="yolo11n-cls.pt")
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--imgsz", type=int, default=256)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--project", default="runs/culturas")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if min(args.epochs, args.batch) < 1 or args.imgsz < 32:
        parser.error("epochs/batch devem ser positivos e imgsz >= 32")
    root = args.data.resolve()
    try:
        counts = audit_dataset(root)
    except ValueError as exc:
        parser.error(str(exc))
    print("Contagem de imagens:", json.dumps(counts, ensure_ascii=False))
    if args.dry_run:
        print("Verificacao OK; nenhum treino executado.")
        return
    from ultralytics import YOLO
    model = YOLO(args.model)
    model.train(data=str(root), epochs=args.epochs, imgsz=args.imgsz,
                batch=args.batch, device=args.device, project=args.project,
                name="piloto-rgb", seed=42, plots=True, exist_ok=False)
    out = Path(model.trainer.save_dir)
    (out / "proveniencia.json").write_text(json.dumps({
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": str(root), "counts": counts, "classes": CLASSES,
        "initial_weights": args.model, "epochs": args.epochs,
        "imgsz": args.imgsz, "batch": args.batch, "device": args.device,
        "limitation": "Acuracia agricola nao validada; conferir rotulos, safra e vazamento espacial-temporal"
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Resultado:", out)


if __name__ == "__main__":
    main()
