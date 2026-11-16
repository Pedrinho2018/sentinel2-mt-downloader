from __future__ import annotations

from dataclasses import replace

from PIL import Image

from sentinel2_mt.laboratorio.datasets import descobrir_recortes, preparar_recortes
from sentinel2_mt.laboratorio.rotulagem import (
    importar_anotacoes,
    localizar_anotadores,
    lote_atual,
    preparar_lote,
    validar_anotacao_yolo,
)
from tests.test_laboratorio import BaseLaboratorio


class TestValidadorYolo(BaseLaboratorio):
    def test_aceita_anotacao_valida_e_arquivo_vazio(self) -> None:
        resultado = validar_anotacao_yolo("0 0.5 0.5 0.2 0.3\n1 0.1 0.1 0.2 0.2\n\n", 2)
        self.assertTrue(resultado.valido)
        self.assertEqual(resultado.caixas, 2)
        self.assertEqual(resultado.por_classe, {0: 1, 1: 1})
        self.assertTrue(validar_anotacao_yolo("", 1).valido)
        self.assertTrue(validar_anotacao_yolo("0.0 0.5 0.5 0.1 0.1", 1).valido)

    def test_mensagens_por_linha(self) -> None:
        casos = {
            "0 0.5": "esperados 5 valores",
            "a 0.5 0.5 0.1 0.1": "não é inteiro",
            "0.5 0.5 0.5 0.1 0.1": "não é inteiro",
            "3 0.5 0.5 0.1 0.1": "fora do esquema",
            "-1 0.5 0.5 0.1 0.1": "fora do esquema",
            "0 x 0.5 0.1 0.1": "números",
            "0 nan 0.5 0.1 0.1": "não finitas",
            "0 1.5 0.5 0.1 0.1": "normalizadas",
            "0 0.5 0.5 0 0.1": "maiores que zero",
            "0 0.95 0.5 0.2 0.2": "borda",
            "0 0.5 0.5 0.1 0.1\n0 0.5 0.5 0.1 0.1": "duplicada",
        }
        for texto, esperado in casos.items():
            with self.subTest(texto=texto):
                resultado = validar_anotacao_yolo(texto, 2)
                self.assertFalse(resultado.valido)
                self.assertIn(esperado, resultado.erros[0])
                self.assertTrue(resultado.erros[0].startswith("linha "))


class BaseRotulagem(BaseLaboratorio):
    def setUp(self) -> None:
        super().setUp()
        self.pasta_lab = self.raiz / "data/laboratorio"
        self.pngs = [
            self.criar_patch(f"P{i}", "2026-03-22", cor=(0, 100 + i, 0)) for i in range(3)
        ]
        recortes, origem = descobrir_recortes(self.config)
        self.imagens = preparar_recortes(recortes, self.raiz, origem)

    def projeto(self, tipo: str, classes=None) -> int:
        projeto = self.repo.criar_projeto(f"Projeto {tipo}", tipo, classes)
        self.repo.adicionar_imagens(projeto.id, self.imagens)
        return projeto.id

    def estados(self, projeto_id: int) -> dict[str, str]:
        return {i.patch_id: i.estado_rotulo for i in self.repo.listar_imagens(projeto_id)}


class TestLoteDeteccao(BaseRotulagem):
    def test_prepara_lote_com_copias_classes_e_manifesto(self) -> None:
        projeto_id = self.projeto("deteccao", ["talhao", "estrada"])
        originais = [png.read_bytes() for png in self.pngs]

        lote = preparar_lote(self.repo, projeto_id, self.raiz, self.pasta_lab, limite=2)

        self.assertEqual(lote.quantidade, 2)
        self.assertEqual(sorted(p.name for p in lote.imagens.iterdir()), ["P0.png", "P1.png"])
        self.assertEqual(lote.classes.read_text(encoding="utf-8"), "talhao\nestrada\n")
        self.assertEqual((lote.rotulos / "classes.txt").read_text(encoding="utf-8"), "talhao\nestrada\n")
        self.assertEqual(lote_atual(self.pasta_lab, projeto_id).quantidade, 2)
        # Cópias, não links: os arquivos do pipeline continuam intactos e separados.
        self.assertFalse((lote.imagens / "P0.png").is_symlink())
        self.assertEqual([png.read_bytes() for png in self.pngs], originais)

    def test_lote_recusa_projeto_de_classificacao_e_ignora_duplicatas(self) -> None:
        classificacao = self.projeto("classificacao")
        with self.assertRaisesRegex(ValueError, "apenas em projetos de detecção"):
            preparar_lote(self.repo, classificacao, self.raiz, self.pasta_lab)

        projeto = self.repo.criar_projeto("Dup", "deteccao")
        duplicada = replace(self.imagens[1], patch_id="P9", sha256=self.imagens[0].sha256)
        self.repo.adicionar_imagens(projeto.id, [self.imagens[0], duplicada])
        lote = preparar_lote(self.repo, projeto.id, self.raiz, self.pasta_lab)
        self.assertEqual(lote.quantidade, 1)

    def test_importa_valida_e_marca_estados(self) -> None:
        projeto_id = self.projeto("deteccao", ["talhao"])
        lote = preparar_lote(self.repo, projeto_id, self.raiz, self.pasta_lab)
        (lote.rotulos / "P0.txt").write_text("0 0.5 0.5 0.2 0.2\n0 0.2 0.2 0.1 0.1\n", encoding="utf-8")
        (lote.rotulos / "P1.txt").write_text("1 0.5 0.5 0.2 0.2\n", encoding="utf-8")

        resumo = importar_anotacoes(self.repo, projeto_id, self.pasta_lab)

        self.assertEqual((resumo.rotuladas, resumo.com_erros, resumo.sem_anotacao), (1, 1, 1))
        self.assertIn("fora do esquema", resumo.erros_por_imagem["P1"][0])
        self.assertEqual(self.estados(projeto_id), {"P0": "rotulada", "P1": "revisar", "P2": "pendente"})
        anotacoes = self.repo.anotacoes_deteccao(projeto_id)
        self.assertEqual(anotacoes["P0"]["caixas"], 2)
        self.assertEqual(anotacoes["P0"]["versao_esquema"], 1)

    def test_correcao_reexporta_anotacao_e_reimporta(self) -> None:
        projeto_id = self.projeto("deteccao", ["talhao"])
        lote = preparar_lote(self.repo, projeto_id, self.raiz, self.pasta_lab)
        (lote.rotulos / "P0.txt").write_text("0 0.95 0.5 0.2 0.2\n", encoding="utf-8")
        importar_anotacoes(self.repo, projeto_id, self.pasta_lab)
        self.assertEqual(self.estados(projeto_id)["P0"], "revisar")

        lote = preparar_lote(self.repo, projeto_id, self.raiz, self.pasta_lab)
        self.assertEqual((lote.rotulos / "P0.txt").read_text(encoding="utf-8"), "0 0.95 0.5 0.2 0.2\n")
        (lote.rotulos / "P0.txt").write_text("0 0.85 0.5 0.2 0.2\n", encoding="utf-8")
        importar_anotacoes(self.repo, projeto_id, self.pasta_lab)

        self.assertEqual(self.estados(projeto_id)["P0"], "rotulada")

    def test_avisa_classes_alteradas_e_ignora_imagem_modificada(self) -> None:
        projeto_id = self.projeto("deteccao", ["talhao"])
        lote = preparar_lote(self.repo, projeto_id, self.raiz, self.pasta_lab)
        (lote.rotulos / "classes.txt").write_text("talhao\nnova\n", encoding="utf-8")
        (lote.rotulos / "P0.txt").write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
        Image.new("RGB", (8, 8), (255, 255, 255)).save(lote.imagens / "P0.png")

        resumo = importar_anotacoes(self.repo, projeto_id, self.pasta_lab)

        self.assertIn("nova", resumo.avisos[0])
        self.assertEqual(resumo.imagem_alterada, 1)
        self.assertEqual(self.estados(projeto_id)["P0"], "pendente")

    def test_lote_de_esquema_antigo_continua_importando_na_versao_dele(self) -> None:
        projeto_id = self.projeto("deteccao", ["talhao"])
        lote = preparar_lote(self.repo, projeto_id, self.raiz, self.pasta_lab)
        self.repo.nova_versao_esquema(projeto_id, ["talhao", "pivo"])
        (lote.rotulos / "P0.txt").write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")

        importar_anotacoes(self.repo, projeto_id, self.pasta_lab)

        self.assertEqual(self.repo.anotacoes_deteccao(projeto_id)["P0"]["versao_esquema"], 1)

    def test_sem_lote_gera_erro_claro(self) -> None:
        projeto_id = self.projeto("deteccao")
        with self.assertRaisesRegex(ValueError, "Nenhum lote"):
            importar_anotacoes(self.repo, projeto_id, self.pasta_lab)


class TestClassificacao(BaseRotulagem):
    def test_rotula_e_limpa(self) -> None:
        projeto_id = self.projeto("classificacao")

        self.assertEqual(self.repo.rotular_classificacao(projeto_id, ["P0", "P1"], "soja"), 2)
        self.repo.rotular_classificacao(projeto_id, ["P1"], "milho")

        rotulos = self.repo.rotulos_classificacao(projeto_id)
        self.assertEqual(rotulos["P0"], ("soja", 1))
        self.assertEqual(rotulos["P1"], ("milho", 1))
        self.assertEqual(self.estados(projeto_id)["P1"], "rotulada")

        self.repo.limpar_rotulos(projeto_id, ["P1"])
        self.assertNotIn("P1", self.repo.rotulos_classificacao(projeto_id))
        self.assertEqual(self.estados(projeto_id)["P1"], "pendente")

    def test_recusa_classe_fora_do_esquema_imagem_alheia_e_tipo_errado(self) -> None:
        projeto_id = self.projeto("classificacao")
        deteccao = self.projeto("deteccao")
        with self.assertRaisesRegex(ValueError, "não existe no esquema"):
            self.repo.rotular_classificacao(projeto_id, ["P0"], "cafe")
        with self.assertRaisesRegex(ValueError, "não pertence"):
            self.repo.rotular_classificacao(projeto_id, ["X"], "soja")
        with self.assertRaisesRegex(ValueError, "detecção"):
            self.repo.rotular_classificacao(deteccao, ["P0"], "talhao")
        with self.assertRaisesRegex(ValueError, "classificação"):
            self.repo.registrar_anotacao_deteccao(projeto_id, "P0", 1, "", 0, [], "x")


class TestRevalidacaoEsquema(BaseRotulagem):
    def test_classificacao_com_classe_removida_vai_para_revisar(self) -> None:
        projeto_id = self.projeto("classificacao")
        self.repo.rotular_classificacao(projeto_id, ["P0"], "algodao")
        self.repo.rotular_classificacao(projeto_id, ["P1"], "soja")

        self.repo.nova_versao_esquema(projeto_id, ["soja", "milho", "outras"])

        estados = self.estados(projeto_id)
        self.assertEqual((estados["P0"], estados["P1"]), ("revisar", "rotulada"))

    def test_deteccao_so_e_invalidada_quando_ids_mudam(self) -> None:
        projeto_id = self.projeto("deteccao", ["talhao", "pivo"])
        self.repo.registrar_anotacao_deteccao(projeto_id, "P0", 1, "0 0.5 0.5 0.1 0.1", 1, [], "h")

        self.repo.nova_versao_esquema(projeto_id, ["talhao", "pivo", "estrada"])
        self.assertEqual(self.estados(projeto_id)["P0"], "rotulada")

        self.repo.nova_versao_esquema(projeto_id, ["pivo", "talhao", "estrada"])
        self.assertEqual(self.estados(projeto_id)["P0"], "revisar")


class TestAnotadores(BaseRotulagem):
    def test_localiza_no_path_sem_instalar(self) -> None:
        projeto_id = self.projeto("deteccao")
        lote = preparar_lote(self.repo, projeto_id, self.raiz, self.pasta_lab)
        caminhos = {"labelImg": "/opt/bin/labelImg", "xanylabeling": "/opt/bin/xanylabeling"}

        anotadores = localizar_anotadores(lote, which=caminhos.get)

        self.assertEqual([a.nome for a in anotadores], ["LabelImg", "X-AnyLabeling"])
        self.assertEqual(
            anotadores[0].argumentos, (str(lote.imagens), str(lote.classes), str(lote.rotulos))
        )
        self.assertIn("YOLO", anotadores[0].instrucao)
        self.assertEqual(localizar_anotadores(lote, which=lambda _nome: None), [])
