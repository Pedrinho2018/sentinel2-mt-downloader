from pathlib import Path
from unittest import TestCase

import yaml

from sentinel2_mt import __version__


ROOT = Path(__file__).resolve().parents[1]


class TestPackaging(TestCase):
    def test_workflow_e_templates_estao_presentes(self) -> None:
        workflow = yaml.safe_load((ROOT / ".github/workflows/packages.yml").read_text(encoding="utf-8"))
        self.assertIn("packages", workflow["jobs"])
        self.assertEqual(workflow["jobs"]["packages"]["runs-on"], "ubuntu-22.04")
        self.assertTrue((ROOT / "packaging/arch/PKGBUILD.in").is_file())
        self.assertTrue((ROOT / "packaging/rpm/sentinel2-mt.spec").is_file())

    def test_versao_do_workflow_acompanha_codigo(self) -> None:
        workflow = (ROOT / ".github/workflows/packages.yml").read_text(encoding="utf-8")
        self.assertIn(f'default: "{__version__}"', workflow)

    def test_configuracao_de_pacote_nao_contem_segredos(self) -> None:
        conteudo = (ROOT / "packaging/config.yaml").read_text(encoding="utf-8")
        self.assertNotIn("client_secret", conteudo)
        self.assertNotIn("client_id", conteudo)
