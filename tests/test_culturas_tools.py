"""Testes das ferramentas de piloto de classificação de culturas em `tools/`.

Cobrem a validação de dataset rotulado (vazamento de grupo entre splits,
contenção de caminho, cobertura de classes) e a auditoria pré-treino, sem
baixar pesos, acessar rede ou executar treinamento real.
"""
import csv
import importlib.util
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

ROOT = Path(__file__).resolve().parents[1]
CLASSES = ("soja", "milho", "algodao", "outras")


def _carregar(nome: str):
    """Importa um script de `tools/` por caminho, sem exigir pacote."""
    caminho = ROOT / "tools" / f"{nome}.py"
    spec = importlib.util.spec_from_file_location(f"_tools_{nome}", caminho)
    if spec is None or spec.loader is None:
        raise ImportError(f"Não foi possível carregar {caminho}")
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo
    spec.loader.exec_module(modulo)
    return modulo


preparar_dataset = _carregar("preparar_dataset_culturas")
treinar_culturas = _carregar("treinar_culturas_yolo")


def _png(destino: Path) -> None:
    """Grava um PNG 1x1 válido sem depender de Pillow."""
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc`\x00\x00"
        b"\x00\x02\x00\x01\xe2!\xbc\x33\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def _linhas_completas() -> list[list[str]]:
    """Dataset mínimo válido: toda classe presente em train e val."""
    return [
        [f"{split}_{classe}.png", classe, f"g_{split}_{classe}", split]
        for split in ("train", "val")
        for classe in CLASSES
    ]


def _montar(base: Path, linhas, extras=()) -> tuple[Path, Path]:
    imagens = base / "img"
    imagens.mkdir(parents=True, exist_ok=True)
    for nome in {linha[0] for linha in linhas} | set(extras):
        if ".." in Path(nome).parts:
            continue  # alvo de teste de containment: não deve existir
        alvo = imagens / nome
        if alvo.suffix.lower() in {".png", ".jpg", ".jpeg"}:
            _png(alvo)
        else:
            alvo.parent.mkdir(parents=True, exist_ok=True)
            alvo.write_text("x", encoding="utf-8")
    rotulos = base / "rotulos.csv"
    with rotulos.open("w", newline="", encoding="utf-8") as arquivo:
        escritor = csv.writer(arquivo)
        escritor.writerow(["image", "classe", "grupo", "split"])
        escritor.writerows(linhas)
    return imagens, rotulos


class TestPrepararDatasetCulturas(TestCase):
    def test_dry_run_conta_sem_copiar_arquivos(self) -> None:
        with TemporaryDirectory() as temporario:
            base = Path(temporario)
            imagens, rotulos = _montar(base, _linhas_completas())
            destino = base / "saida"

            resumo = preparar_dataset.preparar(imagens, rotulos, destino, True)

            self.assertEqual(resumo["imagens"], 8)
            self.assertEqual(resumo["grupos"], 8)
            self.assertTrue(resumo["simulacao"])
            self.assertFalse(destino.exists())

    def test_execucao_real_copia_e_grava_manifesto(self) -> None:
        with TemporaryDirectory() as temporario:
            base = Path(temporario)
            imagens, rotulos = _montar(base, _linhas_completas())
            destino = base / "saida"

            resumo = preparar_dataset.preparar(imagens, rotulos, destino, False)

            self.assertEqual(resumo["imagens"], 8)
            self.assertEqual(len(list(destino.rglob("*.png"))), 8)
            self.assertTrue((destino / "dataset_manifest.json").is_file())
            for split in ("train", "val"):
                for classe in CLASSES:
                    self.assertEqual(resumo["contagem"][f"{split}/{classe}"], 1)

    def test_grupo_em_splits_diferentes_e_rejeitado(self) -> None:
        """Vazamento espacial: o mesmo talhão não pode cair em train e val."""
        with TemporaryDirectory() as temporario:
            base = Path(temporario)
            linhas = _linhas_completas()
            linhas[4][2] = linhas[0][2]
            imagens, rotulos = _montar(base, linhas)

            with self.assertRaisesRegex(ValueError, "vazamento"):
                preparar_dataset.preparar(imagens, rotulos, base / "saida", True)

    def test_classe_ausente_em_validacao_e_rejeitada(self) -> None:
        with TemporaryDirectory() as temporario:
            base = Path(temporario)
            linhas = [
                linha
                for linha in _linhas_completas()
                if not (linha[3] == "val" and linha[1] == "algodao")
            ]
            imagens, rotulos = _montar(base, linhas)

            with self.assertRaisesRegex(ValueError, "Sem dados para val/algodao"):
                preparar_dataset.preparar(imagens, rotulos, base / "saida", True)

    def test_caminho_fora_da_raiz_de_imagens_e_rejeitado(self) -> None:
        with TemporaryDirectory() as temporario:
            base = Path(temporario)
            linhas = _linhas_completas()
            linhas.append(["../../etc/passwd.png", "soja", "g_intruso", "train"])
            imagens, rotulos = _montar(base, linhas)

            with self.assertRaisesRegex(ValueError, "fora da raiz|ausente"):
                preparar_dataset.preparar(imagens, rotulos, base / "saida", True)

    def test_extensao_nao_suportada_e_rejeitada(self) -> None:
        with TemporaryDirectory() as temporario:
            base = Path(temporario)
            linhas = _linhas_completas()
            linhas[0][0] = "amostra.txt"
            imagens, rotulos = _montar(base, linhas)

            with self.assertRaisesRegex(ValueError, "invalida|ausente"):
                preparar_dataset.preparar(imagens, rotulos, base / "saida", True)

    def test_classe_desconhecida_e_rejeitada(self) -> None:
        with TemporaryDirectory() as temporario:
            base = Path(temporario)
            linhas = _linhas_completas()
            linhas[0][1] = "cana"
            imagens, rotulos = _montar(base, linhas)

            with self.assertRaisesRegex(ValueError, "invalido"):
                preparar_dataset.preparar(imagens, rotulos, base / "saida", True)

    def test_destino_nao_vazio_e_rejeitado(self) -> None:
        """Evita misturar execuções e corromper a proveniência do dataset."""
        with TemporaryDirectory() as temporario:
            base = Path(temporario)
            imagens, rotulos = _montar(base, _linhas_completas())
            destino = base / "saida"
            destino.mkdir()
            (destino / "residuo.txt").write_text("x", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "vazio"):
                preparar_dataset.preparar(imagens, rotulos, destino, True)

    def test_destino_aninhado_na_origem_e_rejeitado(self) -> None:
        with TemporaryDirectory() as temporario:
            base = Path(temporario)
            imagens, rotulos = _montar(base, _linhas_completas())

            with self.assertRaisesRegex(ValueError, "aninhados"):
                preparar_dataset.preparar(imagens, rotulos, imagens / "sub", True)

    def test_csv_sem_colunas_obrigatorias_e_rejeitado(self) -> None:
        with TemporaryDirectory() as temporario:
            base = Path(temporario)
            imagens = base / "img"
            imagens.mkdir(parents=True)
            _png(imagens / "a.png")
            rotulos = base / "rotulos.csv"
            rotulos.write_text("image,classe\na.png,soja\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "CSV exige"):
                preparar_dataset.preparar(imagens, rotulos, base / "saida", True)


class TestAuditoriaTreinoCulturas(TestCase):
    def test_auditoria_conta_imagens_por_split_e_classe(self) -> None:
        with TemporaryDirectory() as temporario:
            raiz = Path(temporario)
            for split in ("train", "val"):
                for classe in CLASSES:
                    _png(raiz / split / classe / "patch.png")

            contagem = treinar_culturas.audit_dataset(raiz)

            self.assertEqual(set(contagem), {"train", "val"})
            self.assertEqual(contagem["train"]["soja"], 1)
            self.assertEqual(contagem["val"]["algodao"], 1)

    def test_auditoria_rejeita_diretorio_de_classe_ausente(self) -> None:
        with TemporaryDirectory() as temporario:
            raiz = Path(temporario)
            for split in ("train", "val"):
                for classe in CLASSES:
                    if (split, classe) == ("val", "milho"):
                        continue
                    _png(raiz / split / classe / "patch.png")

            with self.assertRaisesRegex(ValueError, "ausente"):
                treinar_culturas.audit_dataset(raiz)

    def test_auditoria_rejeita_classe_sem_imagens(self) -> None:
        with TemporaryDirectory() as temporario:
            raiz = Path(temporario)
            for split in ("train", "val"):
                for classe in CLASSES:
                    _png(raiz / split / classe / "patch.png")
            vazia = raiz / "val" / "outras"
            for arquivo in vazia.iterdir():
                arquivo.unlink()

            with self.assertRaisesRegex(ValueError, "Sem imagens"):
                treinar_culturas.audit_dataset(raiz)
