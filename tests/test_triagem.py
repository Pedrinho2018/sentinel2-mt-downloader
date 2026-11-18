"""Triagem, arquivamento lógico reversível e preservação de lotes/rótulos."""

from __future__ import annotations

import sqlite3

from sentinel2_mt.laboratorio.projetos import RepositorioLaboratorio
from sentinel2_mt.laboratorio.rotulagem import (
    LoteComPendencias,
    importar_anotacoes,
    preparar_lote,
)
from tests.test_rotulagem import BaseRotulagem

YOLO = "0 0.5 0.5 0.2 0.2\n"


class BaseTriagem(BaseRotulagem):
    def setUp(self) -> None:
        super().setUp()
        self.pid = self.projeto("deteccao")
        lote = preparar_lote(self.repo, self.pid, self.raiz, self.pasta_lab, patch_ids=["P0"])
        (lote.rotulos / "P0.txt").write_text(YOLO, encoding="utf-8")
        importar_anotacoes(self.repo, self.pid, self.pasta_lab)
        self.assertEqual(self.estados(self.pid)["P0"], "rotulada")

    def imagem(self, patch_id: str):
        return {i.patch_id: i for i in self.repo.listar_imagens(self.pid)}[patch_id]


class TestArquivamento(BaseTriagem):
    def test_arquivar_e_restaurar_sem_apagar_arquivos(self) -> None:
        arquivo = self.raiz / self.imagem("P1").caminho_rgb
        conteudo = arquivo.read_bytes()

        self.assertEqual(self.repo.arquivar_imagens(self.pid, ["P1"], "floresta predominante"), 1)
        self.assertEqual(self.repo.arquivar_imagens(self.pid, ["P1"], "de novo"), 0)  # idempotente
        ativas = {i.patch_id for i in self.repo.listar_imagens(self.pid, incluir_arquivadas=False)}
        self.assertEqual(ativas, {"P0", "P2"})
        self.assertEqual(self.imagem("P1").motivo_arquivamento, "floresta predominante")
        self.assertEqual(arquivo.read_bytes(), conteudo)
        contagens = self.repo.contagens(self.pid)
        self.assertEqual((contagens["total"], contagens["ativas"], contagens["arquivadas"]), (3, 2, 1))
        self.assertEqual(contagens["pendentes"], 1)  # P1 arquivada não conta como pendente

        self.assertEqual(self.repo.restaurar_imagens(self.pid, ["P1"], "reavaliada"), 1)
        self.assertEqual(self.imagem("P1").arquivada, 0)
        acoes = [h["acao"] for h in self.repo.historico_imagens(self.pid, "P1")]
        self.assertEqual(acoes, ["arquivar", "restaurar"])

    def test_imagem_rotulada_nunca_e_arquivada_nem_removida(self) -> None:
        with self.assertRaisesRegex(ValueError, "rotulada"):
            self.repo.arquivar_imagens(self.pid, ["P1", "P0"], "lote")
        # Transação: P1 também não foi arquivada.
        self.assertEqual(self.imagem("P1").arquivada, 0)
        with self.assertRaisesRegex(ValueError, "rotulada"):
            self.repo.remover_imagens(self.pid, ["P0"])
        self.assertIn("P0", self.repo.anotacoes_deteccao(self.pid))

    def test_motivo_obrigatorio_e_imagem_inexistente(self) -> None:
        with self.assertRaises(ValueError):
            self.repo.arquivar_imagens(self.pid, ["P1"], "  ")
        with self.assertRaisesRegex(ValueError, "não pertence"):
            self.repo.arquivar_imagens(self.pid, ["X"], "motivo")


class TestRetirarRotulada(BaseTriagem):
    def test_retira_mantendo_anotacao_e_restaura_estado(self) -> None:
        anotacao = self.repo.anotacoes_deteccao(self.pid)["P0"]
        self.assertEqual(self.repo.retirar_rotuladas(self.pid, ["P0"], "caixas cobrindo vários talhões"), 1)
        imagem = self.imagem("P0")
        self.assertEqual((imagem.arquivada, imagem.estado_rotulo, imagem.triagem), (1, "revisar", "revisar"))
        self.assertEqual(self.repo.anotacoes_deteccao(self.pid)["P0"], anotacao)  # nada apagado
        self.assertNotIn("P0", {i.patch_id for i in self.repo.listar_imagens(self.pid, incluir_arquivadas=False)})
        self.assertEqual(self.repo.restaurar_imagens(self.pid, ["P0"]), 1)
        self.assertEqual((self.imagem("P0").arquivada, self.imagem("P0").estado_rotulo), (0, "rotulada"))
        with self.assertRaises(ValueError):
            self.repo.retirar_rotuladas(self.pid, ["P0"], " ")

    def test_importacao_e_troca_de_lote_ignoram_retirada(self) -> None:
        lote = self.pasta_lab / "projetos" / str(self.pid) / "rotulagem" / "lote-atual"
        anotacao = self.repo.anotacoes_deteccao(self.pid)["P0"]
        self.repo.retirar_rotuladas(self.pid, ["P0"], "errada")
        (lote / "labels" / "P0.txt").write_text("0 0.5 0.5 0.9 0.9\n", encoding="utf-8")  # editada depois
        resumo = importar_anotacoes(self.repo, self.pid, self.pasta_lab)
        self.assertTrue(any("retirada" in a for a in resumo.avisos))
        self.assertEqual(self.repo.anotacoes_deteccao(self.pid)["P0"], anotacao)
        self.assertEqual(self.imagem("P0").estado_rotulo, "revisar")
        preparar_lote(self.repo, self.pid, self.raiz, self.pasta_lab, patch_ids=["P1"])  # não bloqueia


class TestTriagem(BaseTriagem):
    def test_regras_da_triagem(self) -> None:
        self.repo.registrar_triagem(self.pid, {
            "P0": ("protegida", "rotulada"), "P1": ("descartar", "floresta"), "P2": ("aprovada", ""),
        })
        self.assertEqual(self.imagem("P1").triagem, "descartar")
        self.assertEqual(self.imagem("P1").triagem_motivo, "floresta")
        for decisoes, erro in (
            ({"P0": ("descartar", "x")}, "protegida"),
            ({"P0": ("aprovada", "")}, "protegida"),
            ({"P1": ("protegida", "x")}, "reservado"),
            ({"P1": ("descartar", "")}, "motivo"),
            ({"P1": ("lixo", "x")}, "inválida"),
        ):
            with self.subTest(decisoes=decisoes), self.assertRaisesRegex(ValueError, erro):
                self.repo.registrar_triagem(self.pid, decisoes)
        self.repo.registrar_triagem(self.pid, {"P0": ("revisar", "caixas incompletas")})
        self.assertEqual(self.imagem("P0").triagem, "revisar")
        self.assertIn("P0", self.repo.anotacoes_deteccao(self.pid))

    def test_lote_ignora_arquivadas_e_descartadas(self) -> None:
        self.repo.registrar_triagem(self.pid, {"P1": ("descartar", "floresta")})
        lote = preparar_lote(self.repo, self.pid, self.raiz, self.pasta_lab)
        self.assertNotIn("P1.png", {p.name for p in lote.imagens.iterdir()})
        self.repo.arquivar_imagens(self.pid, ["P2"], "pastagem")
        with self.assertRaisesRegex(ValueError, "Nenhuma imagem"):
            preparar_lote(self.repo, self.pid, self.raiz, self.pasta_lab, patch_ids=["P2"])


class TestPreservacaoDoLote(BaseTriagem):
    def test_lote_anterior_vai_para_lotes_anteriores(self) -> None:
        anterior = self.pasta_lab / "projetos" / str(self.pid) / "rotulagem" / "lote-atual"
        txt = (anterior / "labels" / "P0.txt").read_text(encoding="utf-8")
        preparar_lote(self.repo, self.pid, self.raiz, self.pasta_lab, patch_ids=["P1"])
        guardados = list((anterior.parent / "lotes-anteriores").iterdir())
        self.assertEqual(len(guardados), 1)
        self.assertEqual((guardados[0] / "labels" / "P0.txt").read_text(encoding="utf-8"), txt)
        self.assertTrue((guardados[0] / "lote.json").is_file())

    def test_recusa_trocar_lote_com_anotacao_nao_importada_ou_alterada(self) -> None:
        lote = self.pasta_lab / "projetos" / str(self.pid) / "rotulagem" / "lote-atual"
        (lote / "labels" / "P0.txt").write_text(YOLO + "0 0.2 0.2 0.1 0.1\n", encoding="utf-8")
        with self.assertRaises(LoteComPendencias):
            preparar_lote(self.repo, self.pid, self.raiz, self.pasta_lab, patch_ids=["P1"])
        self.assertTrue((lote / "labels" / "P0.txt").is_file())  # nada foi movido nem apagado
        importar_anotacoes(self.repo, self.pid, self.pasta_lab)
        preparar_lote(self.repo, self.pid, self.raiz, self.pasta_lab, patch_ids=["P1"])


class TestMigracao(BaseRotulagem):
    def test_banco_antigo_ganha_colunas_sem_perder_dados(self) -> None:
        caminho = self.raiz / "antigo.sqlite3"
        with sqlite3.connect(caminho) as conexao:
            conexao.executescript("""
                CREATE TABLE projetos (id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT NOT NULL UNIQUE,
                    tipo TEXT NOT NULL, descricao TEXT NOT NULL DEFAULT '', criado_em TEXT NOT NULL);
                CREATE TABLE imagens (projeto_id INTEGER NOT NULL, patch_id TEXT NOT NULL, caminho_rgb TEXT NOT NULL,
                    caminho_geotiff TEXT NOT NULL DEFAULT '', sha256 TEXT NOT NULL, origem TEXT NOT NULL,
                    cena TEXT NOT NULL DEFAULT '', tile TEXT NOT NULL DEFAULT '', data TEXT NOT NULL DEFAULT '',
                    safra TEXT NOT NULL DEFAULT '', regiao TEXT NOT NULL DEFAULT '', colecao TEXT NOT NULL DEFAULT '',
                    nuvem_pct REAL, largura INTEGER NOT NULL DEFAULT 0, altura INTEGER NOT NULL DEFAULT 0,
                    estado_rotulo TEXT NOT NULL DEFAULT 'pendente', integridade TEXT NOT NULL DEFAULT 'nao_verificada',
                    duplicata_de TEXT NOT NULL DEFAULT '', adicionada_em TEXT NOT NULL, PRIMARY KEY (projeto_id, patch_id));
                INSERT INTO projetos VALUES (1, 'p', 'deteccao', '', 'x');
                INSERT INTO imagens (projeto_id, patch_id, caminho_rgb, sha256, origem, estado_rotulo, adicionada_em)
                    VALUES (1, 'A', 'a.png', 'h', 'pasta', 'rotulada', 'x');
            """)
        repo = RepositorioLaboratorio(caminho)
        imagem = repo.listar_imagens(1)[0]
        self.assertEqual((imagem.patch_id, imagem.estado_rotulo, imagem.arquivada, imagem.triagem), ("A", "rotulada", 0, ""))
        RepositorioLaboratorio(caminho)  # migração idempotente
