from pathlib import Path
from unittest import TestCase


ROOT = Path(__file__).resolve().parents[1]


class TestWindowsPackaging(TestCase):
    def test_arquivos_do_pacote_windows_estao_presentes(self) -> None:
        self.assertTrue((ROOT / ".github/workflows/windows.yml").is_file())
        self.assertTrue((ROOT / "packaging/sentinel2-mt-gui-windows.spec").is_file())
        self.assertTrue((ROOT / "src/gui_windows.py").is_file())
        self.assertTrue((ROOT / "docs/windows.md").is_file())

    def test_gui_windows_usa_backend_executavel_distinto(self) -> None:
        conteudo = (ROOT / "src/gui_windows.py").read_text(encoding="utf-8")
        self.assertIn('BACKEND_EXE = ROOT / "Sentinel2-MT-Core.exe"', conteudo)
        self.assertIn('ambiente.insert("PYTHONUNBUFFERED", "1")', conteudo)
        self.assertIn("self.processo.start(str(BACKEND_EXE), argumentos)", conteudo)
        self.assertNotEqual("Sentinel2-MT.exe".casefold(), "Sentinel2-MT-Core.exe".casefold())

    def test_spec_windows_gera_aplicacao_sem_console(self) -> None:
        conteudo = (ROOT / "packaging/sentinel2-mt-gui-windows.spec").read_text(
            encoding="utf-8"
        )
        self.assertIn('name="Sentinel2-MT"', conteudo)
        self.assertIn("console=False", conteudo)
        self.assertIn('name="Sentinel2-MT-Windows"', conteudo)

    def test_workflow_publica_zip_x64_e_preserva_dois_executaveis(self) -> None:
        conteudo = (ROOT / ".github/workflows/windows.yml").read_text(encoding="utf-8")
        self.assertIn("runs-on: windows-2022", conteudo)
        self.assertIn("sentinel2-mt-${{ steps.version.outputs.version }}-windows-x64", conteudo)
        self.assertIn("Compress-Archive", conteudo)
        self.assertIn('Sentinel2-MT-Core.exe', conteudo)
        self.assertIn('foreach ($obrigatorio in @("Sentinel2-MT.exe", "Sentinel2-MT-Core.exe"))', conteudo)
