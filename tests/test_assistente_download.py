from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from sentinel2_mt.assistente_download import (
    CenaEncontrada,
    EstadoDownload,
    estimar_tamanho,
    filtrar_cenas,
    ler_evento,
    media_tamanho_cena_local,
)
from sentinel2_mt.gui_support import montar_argumentos_operacao


def cena(nome: str, *, nuvem=None, tamanho=None, baixada=False) -> CenaEncontrada:
    return CenaEncontrada(nome, "2026-01-01", nuvem, tamanho, "", baixada, "")


class TestEventos(TestCase):
    def test_le_somente_linhas_marcadas_e_validas(self) -> None:
        self.assertEqual(ler_evento('[PROGRESSO] {"tipo": "cena"}'), {"tipo": "cena"})
        self.assertIsNone(ler_evento('{"tipo": "cena"}'))
        self.assertIsNone(ler_evento("[PROGRESSO] não é json"))
        self.assertIsNone(ler_evento('[PROGRESSO] {"sem_tipo": 1}'))

    def test_cena_encontrada_de_evento(self) -> None:
        item = CenaEncontrada.de_evento(
            {"cena": "A", "data": "2026-01-01", "nuvem_metadados_pct": 3, "tamanho_bytes": 0, "baixada": True}
        )
        self.assertEqual(item.nuvem_pct, 3.0)
        self.assertIsNone(item.tamanho_bytes)
        self.assertEqual(item.situacao, "Já baixada")


class TestFiltros(TestCase):
    def test_nuvem_desconhecida_nao_e_escondida(self) -> None:
        cenas = [cena("A", nuvem=5), cena("B", nuvem=60), cena("C")]
        self.assertEqual([c.cena for c in filtrar_cenas(cenas, 20, False)], ["A", "C"])
        self.assertEqual(len(filtrar_cenas(cenas, None, False)), 3)

    def test_oculta_baixadas(self) -> None:
        cenas = [cena("A", baixada=True), cena("B")]
        self.assertEqual([c.cena for c in filtrar_cenas(cenas, None, True)], ["B"])


class TestEstimativa(TestCase):
    def test_usa_tamanho_do_catalogo_quando_completo(self) -> None:
        texto = estimar_tamanho([cena("A", tamanho=1024**2), cena("B", tamanho=1024**2)], 0, 0)
        self.assertIn("2,0 MB", texto)
        self.assertIn("catálogo", texto)

    def test_usa_media_local_e_cita_a_origem(self) -> None:
        texto = estimar_tamanho([cena("A"), cena("B"), cena("C", baixada=True)], 1024**3, 3)
        self.assertIn("2,0 GB", texto)
        self.assertIn("média de 3", texto)

    def test_sem_base_nao_inventa_numero(self) -> None:
        texto = estimar_tamanho([cena("A")], 0, 0)
        self.assertNotIn("B", texto.replace("baixadas", ""))
        self.assertIn("não informado", texto)

    def test_media_local_ignora_parciais_e_cenas_sem_bandas(self) -> None:
        with TemporaryDirectory() as temporario:
            pasta = Path(temporario)
            a = pasta / "2026-01-01" / "A"
            (a / "qualidade").mkdir(parents=True)
            (a / "B02.tif").write_bytes(b"x" * 100)
            (a / "qualidade" / "SCL.tif").write_bytes(b"x" * 20)
            (a / ".B03.tif.part").write_bytes(b"x" * 999)
            b = pasta / "2026-01-01" / "B" / "qualidade"
            b.mkdir(parents=True)
            (b / "SCL.tif").write_bytes(b"x" * 50)

            self.assertEqual(media_tamanho_cena_local(pasta), (120, 1))


class TestEstadoDownload(TestCase):
    def test_mensagens_simples_e_percentuais(self) -> None:
        estado = EstadoDownload()
        self.assertEqual(estado.mensagem(), "Preparando o download…")
        estado.aplicar({"tipo": "cena", "cena": "A", "indice": 1, "total": 2, "etapa": "qualidade"})
        self.assertIn("Verificando nuvens da cena 1 de 2", estado.mensagem())
        estado.aplicar({"tipo": "cena", "cena": "A", "indice": 1, "total": 2, "etapa": "bandas"})
        estado.aplicar(
            {"tipo": "arquivo", "arquivo": "B04.tif", "recebidos": 512 * 1024,
             "total": 1024 * 1024, "velocidade_bps": 2048, "retomado": True}
        )
        self.assertEqual(estado.percentual_arquivo, 50)
        self.assertIn("Retomando B04.tif da cena 1 de 2: 512,0 KB de 1,0 MB a 2,0 KB/s", estado.mensagem())
        estado.aplicar({"tipo": "cena", "cena": "A", "indice": 1, "total": 2, "etapa": "concluida"})
        estado.aplicar({"tipo": "cena", "cena": "B", "indice": 2, "total": 2, "etapa": "descartada"})
        self.assertEqual(estado.percentual_geral, 100)
        self.assertEqual(estado.situacoes["B"], "Descartada: nuvens acima do limite")

    def test_resumo_final_de_cancelamento_menciona_retomada(self) -> None:
        estado = EstadoDownload(total_cenas=2, cenas_concluidas=1)
        self.assertIn("retomados", estado.resumo_final(-1, True))
        self.assertIn("concluído", estado.resumo_final(0, False))
        self.assertIn("problemas", estado.resumo_final(2, False))


class TestArgumentosAssistente(TestCase):
    def test_monta_cenas_e_progresso(self) -> None:
        argumentos = montar_argumentos_operacao(
            "baixar", "/tmp/c.yaml", max_itens=0, cenas=["S2_A", "S2-16D_V2_014015_20260423"],
            progresso_json=True,
        )
        self.assertIn("--cenas", argumentos)
        self.assertEqual(argumentos[argumentos.index("--cenas") + 1], "S2_A,S2-16D_V2_014015_20260423")
        self.assertIn("--progresso-json", argumentos)

    def test_rejeita_id_que_pareca_opcao_ou_tenha_virgula(self) -> None:
        for invalido in ("--baixar", "A,B", "../x", ""):
            with self.assertRaises(ValueError):
                montar_argumentos_operacao("baixar", "/tmp/c.yaml", cenas=[invalido])
