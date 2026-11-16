from __future__ import annotations

import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

import numpy as np
from PIL import Image
import yaml

from sentinel2_mt.catalogo import RepositorioCatalogoPatchesCSV
from sentinel2_mt.configuracao import ConfiguracaoProjeto
from sentinel2_mt.laboratorio.datasets import (
    caminho_miniatura,
    descobrir_recortes,
    filtrar_imagens,
    gerar_miniatura,
    importar_externas,
    preparar_recortes,
    resolver_pasta_laboratorio,
    safra_de,
    sha256_arquivo,
    tile_de,
    verificar_lote,
)
from sentinel2_mt.laboratorio.projetos import ImagemDataset, RepositorioLaboratorio
from tests.test_patches import criar_raster
from tests.test_painel import CONFIG_MINIMA


class BaseLaboratorio(TestCase):
    def setUp(self) -> None:
        self.temporario = TemporaryDirectory()
        self.raiz = Path(self.temporario.name)
        (self.raiz / "config").mkdir()
        caminho = self.raiz / "config" / "config.yaml"
        caminho.write_text(yaml.safe_dump(CONFIG_MINIMA), encoding="utf-8")
        self.config = ConfiguracaoProjeto.carregar(caminho, raiz=self.raiz)
        self.repo = RepositorioLaboratorio(self.raiz / "data/laboratorio/laboratorio.sqlite3")

    def tearDown(self) -> None:
        self.temporario.cleanup()

    def criar_patch(self, patch_id: str, data: str, cor=(0, 128, 0), nuvem=2.0, cena="S2-16D_V2_014015_20260423") -> Path:
        pasta = self.raiz / "data/dataset" / data / cena / patch_id
        pasta.mkdir(parents=True)
        png = pasta / f"{patch_id[-6:]}-uuid.png"
        Image.new("RGB", (8, 8), cor).save(png)
        criar_raster(pasta / "multiband.tif", np.ones((8, 8), dtype=np.int16))
        (pasta / "metadata.json").write_text(
            json.dumps(
                {"patch_id": patch_id, "scene_id": cena, "date": data, "collection": "S2-16D-2",
                 "cloud_pct": nuvem, "width": 8, "height": 8}
            ),
            encoding="utf-8",
        )
        return png


class TestProjetos(BaseLaboratorio):
    def test_cria_projetos_com_esquema_padrao_por_tipo(self) -> None:
        deteccao = self.repo.criar_projeto("Talhões MT", "deteccao")
        classificacao = self.repo.criar_projeto("Culturas MT", "classificacao")

        self.assertEqual(self.repo.esquema_atual(deteccao.id).classes, ("talhao",))
        self.assertEqual(
            self.repo.esquema_atual(classificacao.id).classes, ("soja", "milho", "algodao", "outras")
        )
        self.assertEqual([p.nome for p in self.repo.listar_projetos()], ["Culturas MT", "Talhões MT"])

    def test_rejeita_tipo_nome_duplicado_e_classes_invalidas(self) -> None:
        self.repo.criar_projeto("A", "deteccao")
        with self.assertRaisesRegex(ValueError, "Já existe"):
            self.repo.criar_projeto("A", "classificacao")
        with self.assertRaisesRegex(ValueError, "Tipo"):
            self.repo.criar_projeto("B", "segmentacao")
        with self.assertRaisesRegex(ValueError, "inválido"):
            self.repo.criar_projeto("C", "classificacao", ["Algodão"])
        with self.assertRaisesRegex(ValueError, "repetidas"):
            self.repo.criar_projeto("D", "classificacao", ["soja", "soja"])

    def test_alterar_classes_cria_nova_versao_e_preserva_anteriores(self) -> None:
        projeto = self.repo.criar_projeto("Culturas", "classificacao")

        v2 = self.repo.nova_versao_esquema(projeto.id, ["soja", "milho", "algodao", "pastagem", "outras"])
        mesma = self.repo.nova_versao_esquema(projeto.id, list(v2.classes))

        self.assertEqual(v2.versao, 2)
        self.assertEqual(mesma.versao, 2)
        versoes = self.repo.listar_esquemas(projeto.id)
        self.assertEqual([e.versao for e in versoes], [1, 2])
        self.assertEqual(versoes[0].classes, ("soja", "milho", "algodao", "outras"))

    def test_adicionar_e_idempotente_e_marca_duplicatas_por_hash(self) -> None:
        projeto = self.repo.criar_projeto("P", "deteccao")
        a = ImagemDataset("a", "x/a.png", "h1", "pasta")
        b = ImagemDataset("b", "x/b.png", "h1", "pasta")
        c = ImagemDataset("c", "x/c.png", "h2", "pasta")

        self.assertEqual(self.repo.adicionar_imagens(projeto.id, [a, b, c]), (3, 0))
        self.assertEqual(self.repo.adicionar_imagens(projeto.id, [a]), (0, 1))

        por_id = {i.patch_id: i for i in self.repo.listar_imagens(projeto.id)}
        self.assertEqual(por_id["a"].duplicata_de, "")
        self.assertEqual(por_id["b"].duplicata_de, "a")
        self.assertEqual(self.repo.contagens(projeto.id)["duplicatas"], 1)

        self.repo.remover_imagens(projeto.id, ["a"])
        self.assertEqual(self.repo.listar_imagens(projeto.id)[0].duplicata_de, "")

    def test_banco_com_permissao_restrita(self) -> None:
        modo = self.repo.caminho_banco.stat().st_mode & 0o777
        self.assertEqual(modo, 0o600)


class TestDatasets(BaseLaboratorio):
    def test_safra_e_tile(self) -> None:
        self.assertEqual(safra_de("2026-03-22"), "2025/2026")
        self.assertEqual(safra_de("2025-09-01"), "2025/2026")
        self.assertEqual(safra_de("2025-08-31"), "2024/2025")
        self.assertEqual(safra_de("sem_data"), "")
        self.assertEqual(tile_de("S2-16D_V2_014015_20260423"), "014015")
        self.assertEqual(tile_de("outra"), "")

    def test_descobre_pela_pasta_quando_catalogo_ausente(self) -> None:
        self.criar_patch("P_x000000_y000000_8", "2026-03-22")
        self.criar_patch("P_x000008_y000000_8", "2026-04-07")

        recortes, origem = descobrir_recortes(self.config)

        self.assertEqual(origem, "pasta")
        self.assertEqual([r.patch_id for r in recortes], ["P_x000000_y000000_8", "P_x000008_y000000_8"])
        self.assertIsNotNone(recortes[0].geotiff)

    def test_descobre_pelo_catalogo_somente_aprovados(self) -> None:
        png = self.criar_patch("P_ok", "2026-03-22")
        self.criar_patch("P_rejeitado", "2026-03-22")
        catalogo = self.raiz / "catalogo/patches.csv"
        catalogo.parent.mkdir()
        with catalogo.open("w", newline="", encoding="utf-8") as arquivo:
            escritor = csv.DictWriter(arquivo, fieldnames=RepositorioCatalogoPatchesCSV.CAMPOS)
            escritor.writeheader()
            escritor.writerow({"patch_id": "P_ok", "status": "APROVADO", "rgb_png": str(png.relative_to(self.raiz)),
                               "geotiff_path": str((png.parent / "multiband.tif").relative_to(self.raiz)),
                               "date": "2026-03-22", "cloud_pct": "1.5"})
            escritor.writerow({"patch_id": "P_rejeitado", "status": "REJEITADO_NUVEM", "rgb_png": "x.png"})
            escritor.writerow({"patch_id": "P_fora", "status": "APROVADO", "rgb_png": "../../etc/passwd"})

        recortes, origem = descobrir_recortes(self.config)

        self.assertEqual(origem, "catalogo")
        self.assertEqual([r.patch_id for r in recortes], ["P_ok"])
        self.assertEqual(recortes[0].nuvem_pct, 1.5)

    def test_importacao_nao_copia_nem_altera_arquivos_do_pipeline(self) -> None:
        png = self.criar_patch("P_x000000_y000000_8", "2026-03-22")
        geotiff = png.parent / "multiband.tif"
        antes = (png.read_bytes(), geotiff.read_bytes(), geotiff.stat().st_mtime_ns)
        recortes, origem = descobrir_recortes(self.config)

        imagens = preparar_recortes(recortes, self.raiz, origem, "Sorriso")

        self.assertEqual(imagens[0].caminho_rgb, png.relative_to(self.raiz).as_posix())
        self.assertEqual(imagens[0].safra, "2025/2026")
        self.assertEqual(imagens[0].regiao, "Sorriso")
        self.assertEqual(antes, (png.read_bytes(), geotiff.read_bytes(), geotiff.stat().st_mtime_ns))
        self.assertFalse((self.raiz / "data/laboratorio/externas").exists())

    def test_importa_externas_validando_e_copiando(self) -> None:
        externa = self.raiz / "fora" / "foto.png"
        externa.parent.mkdir()
        Image.new("RGB", (5, 7), (1, 2, 3)).save(externa)
        invalida = self.raiz / "fora" / "falsa.png"
        invalida.write_bytes(b"nao e imagem")
        texto = self.raiz / "fora" / "nota.txt"
        texto.write_text("x", encoding="utf-8")
        pasta_lab = self.raiz / "data/laboratorio"

        imagens, erros = importar_externas([externa, invalida, texto], pasta_lab, self.raiz)

        self.assertEqual(len(imagens), 1)
        self.assertEqual(len(erros), 2)
        self.assertEqual((imagens[0].largura, imagens[0].altura), (5, 7))
        copia = self.raiz / imagens[0].caminho_rgb
        self.assertEqual(copia.read_bytes(), externa.read_bytes())
        self.assertTrue(externa.exists())
        self.assertEqual(imagens[0].origem, "externa")

    def test_integridade_detecta_ausente_alterada_e_ilegivel(self) -> None:
        png = self.criar_patch("P1", "2026-03-22")
        recortes, origem = descobrir_recortes(self.config)
        imagem = preparar_recortes(recortes, self.raiz, origem)[0]
        self.assertEqual(verificar_lote([imagem], self.raiz), {"P1": "ok"})

        Image.new("RGB", (8, 8), (255, 0, 0)).save(png)
        self.assertEqual(verificar_lote([imagem], self.raiz)["P1"], "alterada")

        (png.parent / "multiband.tif").write_bytes(b"corrompido")
        Image.new("RGB", (8, 8), (0, 128, 0)).save(png)
        from dataclasses import replace
        atual = replace(imagem, sha256=sha256_arquivo(png))
        self.assertEqual(verificar_lote([atual], self.raiz)["P1"], "ilegivel")

        png.unlink()
        self.assertEqual(verificar_lote([imagem], self.raiz)["P1"], "ausente")

    def test_filtros_combinados(self) -> None:
        imagens = [
            ImagemDataset("a", "a", "1", "pasta", cena="S2_014015", tile="014015", safra="2025/2026", nuvem_pct=2),
            ImagemDataset("b", "b", "2", "pasta", tile="014016", safra="2024/2025", nuvem_pct=30,
                          estado_rotulo="rotulada"),
            ImagemDataset("c", "c", "1", "pasta", regiao="Sorriso", duplicata_de="a", integridade="alterada"),
        ]

        self.assertEqual([i.patch_id for i in filtrar_imagens(imagens, texto="014015")], ["a"])
        self.assertEqual([i.patch_id for i in filtrar_imagens(imagens, texto="sorriso")], ["c"])
        self.assertEqual([i.patch_id for i in filtrar_imagens(imagens, safra="2024/2025")], ["b"])
        self.assertEqual([i.patch_id for i in filtrar_imagens(imagens, nuvem_max_pct=10)], ["a", "c"])
        self.assertEqual([i.patch_id for i in filtrar_imagens(imagens, estado_rotulo="rotulada")], ["b"])
        self.assertEqual([i.patch_id for i in filtrar_imagens(imagens, somente_duplicatas=True)], ["c"])
        self.assertEqual([i.patch_id for i in filtrar_imagens(imagens, somente_problemas=True)], ["c"])
        with self.assertRaises(ValueError):
            filtrar_imagens(imagens, estado_rotulo="desconhecido")

    def test_miniatura_em_cache_sem_alterar_origem(self) -> None:
        png = self.criar_patch("P1", "2026-03-22")
        recortes, origem = descobrir_recortes(self.config)
        imagem = preparar_recortes(recortes, self.raiz, origem)[0]
        pasta_lab = self.raiz / "data/laboratorio"
        original = png.read_bytes()

        caminho = gerar_miniatura(imagem, self.raiz, pasta_lab, lado=4)

        self.assertEqual(caminho, caminho_miniatura(pasta_lab, imagem))
        with Image.open(caminho) as miniatura:
            self.assertLessEqual(max(miniatura.size), 4)
        self.assertEqual(png.read_bytes(), original)
        self.assertEqual(gerar_miniatura(imagem, self.raiz, pasta_lab), caminho)


class TestPastaLaboratorio(BaseLaboratorio):
    def test_pasta_dentro_da_raiz(self) -> None:
        self.assertEqual(resolver_pasta_laboratorio(self.config), (self.raiz / "data/laboratorio").resolve())

    def test_rejeita_pasta_fora_da_raiz(self) -> None:
        from dataclasses import replace
        from sentinel2_mt.configuracao import ConfiguracaoLaboratorio

        for valor in ("/tmp/fora-lab", "../fora", "."):
            config = replace(self.config, laboratorio=ConfiguracaoLaboratorio(pasta=valor))
            with self.assertRaises(ValueError):
                resolver_pasta_laboratorio(config)


class TestConfiguracaoLaboratorio(TestCase):
    def test_config_antiga_sem_secao_usa_padrao(self) -> None:
        with TemporaryDirectory() as temporario:
            raiz = Path(temporario)
            (raiz / "config").mkdir()
            caminho = raiz / "config/config.yaml"
            caminho.write_text(yaml.safe_dump(CONFIG_MINIMA), encoding="utf-8")
            self.assertEqual(ConfiguracaoProjeto.carregar(caminho, raiz=raiz).laboratorio.pasta, "data/laboratorio")

    def test_configs_de_desenvolvimento_e_pacote_expoem_laboratorio(self) -> None:
        raiz = Path(__file__).resolve().parents[1]
        dev = yaml.safe_load((raiz / "config/config.yaml").read_text(encoding="utf-8"))
        pacote = yaml.safe_load((raiz / "packaging/config.yaml").read_text(encoding="utf-8"))
        self.assertEqual(set(dev["laboratorio"]), set(pacote["laboratorio"]))
        self.assertIn(".local/share/sentinel2-mt/laboratorio", pacote["laboratorio"]["pasta"])
