from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import yaml

from sentinel2_mt.analise.historico import RepositorioHistoricoAnalises
from sentinel2_mt.catalogo import RepositorioCatalogoCSV, RepositorioCatalogoPatchesCSV
from sentinel2_mt.configuracao import ConfiguracaoProjeto
from sentinel2_mt.painel import (
    coletar_resumo_painel,
    formatar_bytes,
    listar_modelos_instalados,
)


CONFIG_MINIMA = {
    "stac": {"url": "https://exemplo.invalid/stac/", "colecao": "S2-16D-2"},
    "area": {"nome": "Teste", "uf": "MT", "bbox": [-56.0, -13.0, -55.0, -12.0]},
    "periodo": {"inicio": "2026-01-01", "fim": "2026-03-31"},
    "bandas": ["B02", "B03", "B04"],
    "qualidade": {"filtrar_nuvens": True, "nuvem_max_pct": 40.0, "manter_scl": True},
    "preview": {"gerar_rgb": True, "tamanho_max_px": 800, "qualidade_jpeg": 90},
    "dataset": {"pasta": "data/dataset", "catalogo": "catalogo/patches.csv"},
    "download": {
        "pasta": "data/sentinel2",
        "catalogo": "catalogo/catalogo_imagens.csv",
        "timeout_segundos": 30,
        "chunk_mb": 1,
        "max_itens_teste": 1,
        "max_candidatos_teste": 1,
    },
    "analise": {"pasta": "data/analises", "historico": "data/historico.sqlite3"},
    "sincronizacao": {"pasta_remota": "x", "oauth_json": "", "token_json": "t.json"},
}


class TestPainel(TestCase):
    def setUp(self) -> None:
        self.temporario = TemporaryDirectory()
        self.raiz = Path(self.temporario.name)
        (self.raiz / "config").mkdir()
        self.config_path = self.raiz / "config" / "config.yaml"
        self.config_path.write_text(yaml.safe_dump(CONFIG_MINIMA), encoding="utf-8")
        self.config = ConfiguracaoProjeto.carregar(self.config_path, raiz=self.raiz)
        self.modelos = self.raiz / "modelos"
        self.modelos.mkdir()

    def tearDown(self) -> None:
        self.temporario.cleanup()

    def _escrever(self, relativo: str, conteudo: bytes = b"x") -> Path:
        caminho = self.raiz / relativo
        caminho.parent.mkdir(parents=True, exist_ok=True)
        caminho.write_bytes(conteudo)
        return caminho

    def _modelo(self, conteudo: bytes = b"peso", sha: str | None = None) -> Path:
        peso = self.modelos / "best.pt"
        peso.write_bytes(conteudo)
        manifesto = {
            "name": "Detector",
            "version": "1.0",
            "task": "detection",
            "sha256": sha if sha is not None else hashlib.sha256(conteudo).hexdigest(),
        }
        (self.modelos / "model_metadata.json").write_text(json.dumps(manifesto), encoding="utf-8")
        return peso

    def test_estado_vazio_orienta_selecionar_regiao(self) -> None:
        resumo = coletar_resumo_painel(self.config, pasta_modelos=self.modelos)

        self.assertEqual(resumo.cenas_catalogadas, 0)
        self.assertEqual(resumo.cenas_baixadas, 0)
        self.assertEqual(resumo.patches_em_disco, 0)
        self.assertIsNone(resumo.ultima_analise)
        self.assertEqual(resumo.bytes_total, 0)
        self.assertIn("Selecione uma região", resumo.proxima_acao)
        self.assertIn("Nenhum modelo ativo", " ".join(resumo.avisos))

    def test_conta_cenas_patches_parciais_e_espaco(self) -> None:
        catalogo = self.raiz / "catalogo" / "catalogo_imagens.csv"
        catalogo.parent.mkdir(parents=True)
        with catalogo.open("w", newline="", encoding="utf-8") as arquivo:
            escritor = csv.DictWriter(arquivo, fieldnames=RepositorioCatalogoCSV.CAMPOS)
            escritor.writeheader()
            for cena in ("A", "A", "B", "C"):
                escritor.writerow({"id": cena, "status": "candidato_catalogado"})
        self._escrever("data/sentinel2/2026-01-01/A/B02.tif", b"1234")
        self._escrever("data/sentinel2/2026-01-01/A/qualidade/SCL.tif", b"12")
        # Cena com apenas camadas de qualidade não conta como baixada.
        self._escrever("data/sentinel2/2026-01-01/B/qualidade/SCL.tif", b"12")
        self._escrever("data/sentinel2/2026-01-01/C/.B03.tif.abc123.part.tif", b"123")
        self._escrever("data/dataset/2026-01-01/A/P1/multiband.tif", b"55555")
        self._escrever("data/dataset/2026-01-01/A/P2/metadata.json", b"{}")

        resumo = coletar_resumo_painel(self.config, regioes_salvas=2, pasta_modelos=self.modelos)

        self.assertEqual(resumo.regioes_salvas, 2)
        self.assertEqual(resumo.cenas_catalogadas, 3)
        self.assertEqual(resumo.cenas_baixadas, 1)
        self.assertEqual(resumo.downloads_parciais, 1)
        self.assertEqual(resumo.patches_em_disco, 2)
        self.assertFalse(resumo.catalogo_patches_existe)
        self.assertEqual(resumo.bytes_por_area["imagens"], 4 + 2 + 2 + 3)
        self.assertEqual(resumo.bytes_por_area["dataset"], 5 + 2)
        avisos = " ".join(resumo.avisos)
        self.assertIn(".part", avisos)
        self.assertIn("catálogo de patches não foi encontrado", avisos)
        self.assertIn("Gere o dataset", resumo.proxima_acao)

    def test_conta_somente_patches_aprovados_no_catalogo(self) -> None:
        catalogo = self.raiz / "catalogo" / "patches.csv"
        catalogo.parent.mkdir(parents=True)
        with catalogo.open("w", newline="", encoding="utf-8") as arquivo:
            escritor = csv.DictWriter(arquivo, fieldnames=RepositorioCatalogoPatchesCSV.CAMPOS)
            escritor.writeheader()
            for patch_id, status in (
                ("p1", "APROVADO"),
                ("p2", "APROVADO_SEM_SCL"),
                ("p3", "REJEITADO_NUVEM"),
            ):
                escritor.writerow({"patch_id": patch_id, "status": status})

        resumo = coletar_resumo_painel(self.config, pasta_modelos=self.modelos)

        self.assertTrue(resumo.catalogo_patches_existe)
        self.assertEqual(resumo.patches_aprovados_catalogo, 2)

    def test_ultima_analise_vem_do_historico(self) -> None:
        banco = self.raiz / "data" / "historico.sqlite3"
        repositorio = RepositorioHistoricoAnalises(banco, self.raiz)
        repositorio.salvar(
            {
                "analysis_id": "analise-1",
                "status": "concluida",
                "regiao": "Sorriso",
                "modelo_versao": "2.1.0",
                "resumo": {"total_deteccoes": 7},
            }
        )

        resumo = coletar_resumo_painel(self.config, pasta_modelos=self.modelos)

        self.assertIsNotNone(resumo.ultima_analise)
        self.assertEqual(resumo.ultima_analise.analysis_id, "analise-1")
        self.assertEqual(resumo.ultima_analise.total_deteccoes, 7)
        self.assertEqual(resumo.ultima_analise.regiao, "Sorriso")

    def test_modelo_com_hash_conferido(self) -> None:
        self._modelo()

        modelos = listar_modelos_instalados(self.modelos)

        self.assertEqual(len(modelos), 1)
        self.assertTrue(modelos[0].ativo)
        self.assertEqual(modelos[0].integridade, "conferida")
        self.assertEqual(modelos[0].tarefa, "detection")

    def test_modelo_com_hash_divergente_gera_aviso(self) -> None:
        self._modelo(sha="0" * 64)

        resumo = coletar_resumo_painel(self.config, pasta_modelos=self.modelos)

        self.assertEqual(resumo.modelos[0].integridade, "divergente")
        self.assertIn("diverge", " ".join(resumo.avisos))

    def test_peso_extra_fica_sem_manifesto_e_nao_e_ativo(self) -> None:
        self._modelo()
        (self.modelos / "experimento.pt").write_bytes(b"outro")

        modelos = {modelo.arquivo: modelo for modelo in listar_modelos_instalados(self.modelos)}

        self.assertFalse(modelos["experimento.pt"].ativo)
        self.assertEqual(modelos["experimento.pt"].integridade, "sem_manifesto")

    def test_painel_nao_modifica_arquivos(self) -> None:
        self._modelo()
        arquivo = self._escrever("data/sentinel2/2026-01-01/A/B02.tif", b"1234")
        antes = {
            caminho: caminho.stat().st_mtime_ns
            for caminho in self.raiz.rglob("*")
            if caminho.is_file()
        }

        coletar_resumo_painel(self.config, pasta_modelos=self.modelos)

        depois = {
            caminho: caminho.stat().st_mtime_ns
            for caminho in self.raiz.rglob("*")
            if caminho.is_file()
        }
        self.assertEqual(antes, depois)
        self.assertTrue(arquivo.is_file())

    def test_formatar_bytes(self) -> None:
        self.assertEqual(formatar_bytes(0), "0 B")
        self.assertEqual(formatar_bytes(512), "512 B")
        self.assertEqual(formatar_bytes(1536), "1,5 KB")
        self.assertEqual(formatar_bytes(7 * 1024**3), "7,0 GB")
