from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase, skipUnless
from unittest.mock import patch

import yaml


PYSIDE_DISPONIVEL = importlib.util.find_spec("PySide6") is not None

if PYSIDE_DISPONIVEL:
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtCore, QtWidgets

    import gerar_config_gui as gui


@skipUnless(PYSIDE_DISPONIVEL, "PySide6 é uma dependência opcional da GUI")
class TestGuiQt(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        cls.mapa_widget_original = gui.MapaWidget

        class MapaFake(QtWidgets.QWidget):
            areaSelecionada = QtCore.Signal(object)
            desenhoConcluido = QtCore.Signal()

            def __init__(self, bbox_inicial, parent=None):
                super().__init__(parent)
                self.bbox = list(bbox_inicial)

            def exibir_bbox(self, bbox):
                self.bbox = list(bbox)

            def capturar_bbox(self):
                self.areaSelecionada.emit(self.bbox)

            def reativar(self):
                pass

            def ativar_desenho(self, ativo=True):
                self.desenhando = ativo
                return True

        gui.MapaWidget = MapaFake

    @classmethod
    def tearDownClass(cls) -> None:
        gui.MapaWidget = cls.mapa_widget_original

    def setUp(self) -> None:
        self.temporario = TemporaryDirectory()
        self.pasta = Path(self.temporario.name)
        gui.LOCAL_DB = self.pasta / "perfis.db"
        self.municipios_original = gui.MUNICIPIOS_IBGE
        gui.MUNICIPIOS_IBGE = self.pasta / "municipios.json"
        gui.MUNICIPIOS_IBGE.write_text(
            json.dumps(
                {
                    "municipios": [
                        {"codigo_ibge": "5107925", "nome": "Sorriso", "uf": "MT", "bbox": [-56.3, -13.1, -55.2, -12.0]},
                        {"codigo_ibge": "5103403", "nome": "Cuiabá", "uf": "MT", "bbox": [-56.3, -15.7, -55.4, -15.0]},
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.script_original = gui.SCRIPT_CLI
        self.janela = gui.MainWindow()
        self.janela.output_path.setText(str(self.pasta / "config.yaml"))
        self.janela.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        if self.janela.processo.state() != QtCore.QProcess.ProcessState.NotRunning:
            self.janela.processo.kill()
            self.janela.processo.waitForFinished(1000)
        self.janela.close()
        gui.SCRIPT_CLI = self.script_original
        gui.MUNICIPIOS_IBGE = self.municipios_original
        self.temporario.cleanup()

    def _aguardar_processo(self, segundos: float = 5) -> None:
        limite = time.monotonic() + segundos
        while (
            self.janela.processo.state() != QtCore.QProcess.ProcessState.NotRunning
            and time.monotonic() < limite
        ):
            self.app.processEvents()
            QtCore.QThread.msleep(10)
        self.app.processEvents()

    def test_navegacao_exibe_todas_as_paginas(self) -> None:
        for indice, (titulo, _) in enumerate(self.janela.PAGINAS):
            self.janela.botoes_navegacao[indice].click()
            self.app.processEvents()

            self.assertEqual(self.janela.stack.currentIndex(), indice)
            self.assertEqual(self.janela.titulo_pagina.text(), titulo)
            self.assertTrue(self.janela.stack.currentWidget().isVisible())

        self.assertEqual(self.janela.PAGINAS[-2][0], "Sobre")
        self.assertEqual(self.janela.PAGINAS[-1][0], "Preciso de ajuda")
        self.janela.botoes_navegacao[-1].click()
        self.app.processEvents()
        textos = [label.text() for label in self.janela.stack.currentWidget().findChildren(QtWidgets.QLabel)]
        self.assertIn("Problemas comuns", textos)

    def test_inicio_e_a_pagina_inicial_com_acoes_simples(self) -> None:
        self.assertEqual(self.janela.PAGINAS[0][0], "Início")
        self.assertEqual(self.janela.stack.currentIndex(), 0)
        textos = {
            self.janela.btn_inicio_regiao.text(),
            self.janela.btn_inicio_buscar.text(),
            self.janela.btn_inicio_analisar.text(),
            self.janela.btn_inicio_resultados.text(),
        }
        self.assertEqual(
            textos,
            {"Selecionar região", "Buscar imagens", "Analisar região", "Consultar resultados"},
        )

    def test_painel_sem_configuracao_nao_le_outro_yaml(self) -> None:
        self.janela._pool_painel.waitForDone(5000)
        self.app.processEvents()
        self.assertFalse(Path(self.janela.output_path.text()).exists())

        self.janela._atualizar_painel(sincrono=True)

        self.assertIn("Nenhuma configuração", self.janela.painel_proxima_acao.text())
        self.assertEqual(self.janela.painel_valores["disco"].text(), "…")

    def test_acoes_do_inicio_navegam_sem_executar(self) -> None:
        self.janela.btn_inicio_regiao.click()
        self.assertEqual(self.janela.titulo_pagina.text(), "Área e período")
        indice_area = self.janela._indice_pagina("Área e período")
        self.assertTrue(self.janela.botoes_navegacao[indice_area].isChecked())

        self.janela.btn_inicio_buscar.click()
        self.assertEqual(self.janela.titulo_pagina.text(), "Baixar imagens")

        self.janela.btn_inicio_analisar.click()
        self.assertEqual(self.janela.operacao.currentData(), "analisar")
        self.assertEqual(
            self.janela.processo.state(), QtCore.QProcess.ProcessState.NotRunning
        )

        self.janela.btn_inicio_resultados.click()
        self.assertEqual(self.janela.titulo_pagina.text(), "Análise da região")

    def test_painel_inicial_mostra_resumo_local(self) -> None:
        self.janela._salvar_configuracao(avisar=False)
        pasta_cena = self.pasta / "cenas" / "2026-01-01" / "CENA"
        pasta_cena.mkdir(parents=True)
        (pasta_cena / "B02.tif").write_bytes(b"1234")
        (pasta_cena / ".B03.tif.part").write_bytes(b"1")
        config = yaml.safe_load(Path(self.janela.output_path.text()).read_text(encoding="utf-8"))
        config["download"]["pasta"] = str(self.pasta / "cenas")
        config["dataset"]["pasta"] = str(self.pasta / "dataset")
        config["dataset"]["catalogo"] = str(self.pasta / "patches.csv")
        config["download"]["catalogo"] = str(self.pasta / "catalogo.csv")
        config["analise"]["historico"] = str(self.pasta / "historico.sqlite3")
        Path(self.janela.output_path.text()).write_text(yaml.safe_dump(config), encoding="utf-8")
        self.janela._pool_painel.waitForDone(5000)
        self.app.processEvents()

        self.janela._atualizar_painel(sincrono=True)
        self.app.processEvents()

        self.assertEqual(self.janela.painel_valores["cenas_baixadas"].text(), "1")
        self.assertIn("incompleto", self.janela.painel_detalhes["cenas_baixadas"].text())
        self.assertIn("serão retomados", self.janela.painel_avisos.text())
        self.assertNotEqual(self.janela.painel_valores["disco"].text(), "…")
        self.assertIn("validação científica", self.janela.painel_detalhes["modelos"].text())
        self.assertTrue(self.janela.btn_atualizar_painel.isEnabled())

    def test_pesquisa_municipio_sem_acento_aplica_area(self) -> None:
        self.janela.busca_area.setText("cuiaba")
        self.assertFalse(self.janela.resultados_area.isHidden())
        item = self.janela.resultados_area.item(0)
        self.assertIn("Cuiabá (MT)", item.text())

        self.janela._usar_resultado_area(item)

        self.assertEqual(self.janela.nome_regiao.text(), "Cuiabá")
        self.assertEqual(self.janela.uf.text(), "MT")
        self.assertEqual(self.janela._bbox_atual(), [-56.3, -15.7, -55.4, -15.0])
        self.assertEqual(self.janela.mapa.bbox, [-56.3, -15.7, -55.4, -15.0])
        self.assertIn("aproximado", self.janela.area_mensagem.text())
        self.assertTrue(self.janela.resultados_area.isHidden())

    def test_pesquisa_sem_lista_de_municipios_orienta_geracao(self) -> None:
        gui.MUNICIPIOS_IBGE.unlink()
        self.janela.__dict__.pop("_cache_municipios", None)

        self.janela.busca_area.setText("Sorriso")

        self.assertIn("gerar_municipios_ibge", self.janela.resultados_area.item(0).text())

    def test_importa_geojson_e_informa_retangulo_envolvente(self) -> None:
        caminho = self.pasta / "fazenda.geojson"
        caminho.write_text(
            json.dumps(
                {
                    "type": "Feature",
                    "properties": {"nome": "Fazenda Teste"},
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[[-55.9, -12.7], [-55.6, -12.7], [-55.6, -12.4], [-55.9, -12.7]]],
                    },
                }
            ),
            encoding="utf-8",
        )

        area = self.janela._importar_limite_de(caminho)

        self.assertIsNotNone(area)
        self.assertEqual(self.janela.nome_regiao.text(), "Fazenda Teste")
        self.assertEqual(self.janela._bbox_atual(), [-55.9, -12.7, -55.6, -12.4])
        self.assertIn("retângulo envolvente", self.janela.area_mensagem.text())

    def test_importacao_invalida_nao_altera_area(self) -> None:
        antes = self.janela._bbox_atual()
        caminho = self.pasta / "ponto.geojson"
        caminho.write_text('{"type": "Point", "coordinates": [-55, -12]}', encoding="utf-8")

        with patch.object(gui.QtWidgets.QMessageBox, "warning") as aviso:
            area = self.janela._importar_limite_de(caminho)

        self.assertIsNone(area)
        aviso.assert_called_once()
        self.assertEqual(self.janela._bbox_atual(), antes)

    def test_favorita_salva_aparece_na_lista_e_na_busca(self) -> None:
        self.janela.nome_regiao.setText("Talhões Norte")
        self.janela.oeste.setValue(-56.0)
        self.janela._salvar_perfil()
        self.assertIn("Talhões Norte", self.janela.favoritos_area.item(0).text())

        self.janela.nome_regiao.setText("Outra")
        self.janela.oeste.setValue(-57.0)
        self.janela._abrir_favorita(self.janela.favoritos_area.item(0))
        self.assertEqual(self.janela.nome_regiao.text(), "Talhões Norte")
        self.assertEqual(self.janela.oeste.value(), -56.0)

        self.janela.busca_area.setText("talhoes")
        self.assertIn("região favorita", self.janela.resultados_area.item(0).text())

    def test_desenhar_area_ativa_modo_e_desliga_ao_concluir(self) -> None:
        self.janela.btn_desenhar_area.setChecked(True)
        self.assertTrue(self.janela.mapa.desenhando)

        self.janela.mapa.desenhoConcluido.emit()

        self.assertFalse(self.janela.btn_desenhar_area.isChecked())

    def _cli_fake_eventos(self, linhas: list[str]) -> None:
        script = self.pasta / "cli_eventos.py"
        corpo = "import sys, json\nprint('argv=' + ' '.join(sys.argv[1:]), flush=True)\n"
        for linha in linhas:
            corpo += f"print({linha!r}, flush=True)\n"
        script.write_text(corpo, encoding="utf-8")
        gui.SCRIPT_CLI = script

    def test_assistente_busca_preenche_tabela_e_oculta_json_do_log(self) -> None:
        self._cli_fake_eventos(
            [
                '[PROGRESSO] {"tipo": "cena_encontrada", "cena": "S2_A", "data": "2026-01-01", '
                '"nuvem_metadados_pct": 5.0, "tamanho_bytes": null, "miniatura": "", '
                '"baixada": false, "preview_local": ""}',
                '[PROGRESSO] {"tipo": "cena_encontrada", "cena": "S2_B", "data": "2026-01-17", '
                '"nuvem_metadados_pct": 80.0, "tamanho_bytes": null, "miniatura": "", '
                '"baixada": true, "preview_local": ""}',
                "texto comum",
            ]
        )
        self.janela._navegar(self.janela._indice_pagina("Baixar imagens"))

        self.janela._assistente_buscar()
        self._aguardar_processo()
        self.app.processEvents()

        self.assertEqual(self.janela.assist_stack.currentIndex(), 1)
        self.assertEqual(self.janela.assist_tabela.rowCount(), 2)
        log = self.janela.log.toPlainText()
        self.assertIn("--progresso-json", log)
        self.assertIn("texto comum", log)
        self.assertNotIn("cena_encontrada", log)

        self.janela.assist_nuvem_max.setValue(50)
        self.assertEqual(self.janela.assist_tabela.rowCount(), 1)
        self.janela.assist_nuvem_max.setValue(100)
        self.janela.assist_ocultar_baixadas.setChecked(True)
        self.assertEqual(self.janela.assist_tabela.rowCount(), 1)
        self.assertEqual(self.janela.assist_tabela.item(0, 2).text(), "S2_A")

    def test_assistente_exige_cena_marcada_para_revisar(self) -> None:
        with patch.object(gui.QtWidgets.QMessageBox, "information") as aviso:
            self.janela._assistente_revisar()

        aviso.assert_called_once()
        self.assertNotEqual(self.janela.assist_stack.currentIndex(), 2)

    def test_assistente_baixa_somente_cenas_marcadas_e_mostra_progresso(self) -> None:
        self.janela._assist_cenas = [
            gui.CenaEncontrada("S2_A", "2026-01-01", None, None, "", False, ""),
            gui.CenaEncontrada("S2_B", "2026-01-17", None, None, "", False, ""),
        ]
        self.janela._assistente_renderizar_tabela()
        self.janela.assist_tabela.item(1, 0).setCheckState(QtCore.Qt.CheckState.Checked)
        self.janela._assistente_revisar()
        self.assertEqual(self.janela.assist_stack.currentIndex(), 2)
        self.assertIn("1 cena(s)", self.janela.assist_resumo.text())
        self._cli_fake_eventos(
            [
                '[PROGRESSO] {"tipo": "cena", "cena": "S2_B", "data": "2026-01-17", '
                '"indice": 1, "total": 1, "etapa": "bandas"}',
                '[PROGRESSO] {"tipo": "arquivo", "cena": "S2_B", "arquivo": "B04.tif", '
                '"recebidos": 50, "total": 100, "velocidade_bps": 10.0, "retomado": false}',
                '[PROGRESSO] {"tipo": "cena", "cena": "S2_B", "data": "2026-01-17", '
                '"indice": 1, "total": 1, "etapa": "concluida"}',
            ]
        )

        self.janela._assistente_iniciar()
        self._aguardar_processo()
        self.app.processEvents()

        log = self.janela.log.toPlainText()
        self.assertIn("--cenas S2_B", log)
        self.assertNotIn("S2_A", log.split("--cenas", 1)[1].splitlines()[0])
        self.assertEqual(self.janela.assist_progresso_geral.value(), 100)
        self.assertIn("Download concluído", self.janela.assist_mensagem.text())
        self.assertEqual(self.janela._assist_cenas[1].situacao, "Baixada")

    def test_assistente_remove_parciais_legados_com_confirmacao(self) -> None:
        self.janela._salvar_configuracao(avisar=False)
        config = yaml.safe_load(Path(self.janela.output_path.text()).read_text(encoding="utf-8"))
        config["download"]["pasta"] = str(self.pasta / "cenas")
        Path(self.janela.output_path.text()).write_text(yaml.safe_dump(config), encoding="utf-8")
        cena = self.pasta / "cenas" / "2026-01-01" / "C"
        cena.mkdir(parents=True)
        legado = cena / ".B03.tif.abcdef12.part.tif"
        legado.write_bytes(b"123")
        retomavel = cena / ".B04.tif.part"
        retomavel.write_bytes(b"1")

        self.janela._assistente_atualizar_legados(None)
        self.assertFalse(self.janela.assist_limpar_legados.isHidden())
        with patch.object(
            gui.QtWidgets.QMessageBox,
            "question",
            return_value=gui.QtWidgets.QMessageBox.StandardButton.No,
        ):
            self.janela._assistente_limpar_legados()
        self.assertTrue(legado.exists())

        with patch.object(
            gui.QtWidgets.QMessageBox,
            "question",
            return_value=gui.QtWidgets.QMessageBox.StandardButton.Yes,
        ):
            self.janela._assistente_limpar_legados()

        self.assertFalse(legado.exists())
        self.assertTrue(retomavel.exists())

    def test_datas_do_assistente_e_da_area_ficam_sincronizadas(self) -> None:
        self.janela.assist_inicio.setDate(QtCore.QDate(2026, 2, 1))
        self.assertEqual(self.janela.inicio.date(), QtCore.QDate(2026, 2, 1))
        self.janela.fim.setDate(QtCore.QDate(2026, 3, 1))
        self.assertEqual(self.janela.assist_fim.date(), QtCore.QDate(2026, 3, 1))

    def _preparar_resultado_real(self) -> Path:
        from tests.test_resultados_analise import criar_artefatos

        resultado = criar_artefatos(self.pasta)
        dados = json.loads(resultado.read_text(encoding="utf-8"))
        for item in dados["resultados"]:
            item["entrada"] = str(self.pasta / item["entrada"])
        resultado.write_text(json.dumps(dados), encoding="utf-8")
        self.janela._salvar_configuracao(avisar=False)
        config = yaml.safe_load(Path(self.janela.output_path.text()).read_text(encoding="utf-8"))
        config["dataset"]["pasta"] = str(self.pasta / "data/dataset")
        config["analise"]["pasta"] = str(self.pasta / "data/analises")
        config["analise"]["historico"] = str(self.pasta / "data/historico.sqlite3")
        Path(self.janela.output_path.text()).write_text(yaml.safe_dump(config), encoding="utf-8")
        return resultado

    def test_resultado_separa_talhoes_de_culturas_e_lista_deteccoes(self) -> None:
        self.janela._carregar_resultado_analise(self._preparar_resultado_real())

        abas = [self.janela.analise_abas.tabText(i) for i in range(self.janela.analise_abas.count())]
        self.assertEqual(abas, ["Talhões (detecção)", "Culturas (classificação)"])
        self.assertEqual(self.janela.analise_itens.count(), 2)
        self.assertEqual(self.janela.analise_deteccoes.rowCount(), 2)
        self.assertEqual(self.janela.analise_deteccoes.item(0, 1).text(), "90.0%")
        self.assertIn("2026-03-22", self.janela.analise_item_info.text())
        self.assertIn("NDVI médio 0.70", self.janela.analise_vegetacao.text())
        self.assertIn("não por talhão", self.janela.analise_vegetacao.text())
        self.assertIn("2.1.0-beta.1", self.janela.analise_info.text())
        self.assertIn("detecção de talhões", self.janela.analise_info.text())
        self.assertEqual(self.janela.analise_cultura_estado.text(), "Cultura não determinada")
        self.assertFalse(self.janela.analise_original.pixmap().isNull())
        self.assertFalse(self.janela.analise_anotada.pixmap().isNull())
        textos = " ".join(
            label.text() for label in self.janela.stack.currentWidget().findChildren(QtWidgets.QLabel)
        ).casefold()
        self.assertNotIn("hectares de", textos)

    def test_exporta_csv_do_resultado(self) -> None:
        self.janela._carregar_resultado_analise(self._preparar_resultado_real())
        destino = self.pasta / "exportado"
        with patch.object(
            gui.QtWidgets.QFileDialog, "getSaveFileName", return_value=(str(destino), "CSV (*.csv)")
        ):
            self.janela._exportar_resultado("csv")

        conteudo = (self.pasta / "exportado.csv").read_text(encoding="utf-8")
        self.assertIn("x1_px", conteudo)
        self.assertEqual(len(conteudo.strip().splitlines()), 3)

    def test_historico_filtra_e_abre_resultado(self) -> None:
        resultado = self._preparar_resultado_real()
        repositorio = gui.RepositorioHistoricoAnalises(self.pasta / "data/historico.sqlite3", self.pasta)
        repositorio.salvar({"analysis_id": "analise-1", "status": "concluida", "regiao": "Sorriso"})
        repositorio.salvar({"analysis_id": "analise-2", "status": "concluida", "regiao": "Cuiabá"})
        self.janela._recarregar_historico()
        self.assertEqual(self.janela.historico_tabela.rowCount(), 2)

        self.janela.historico_filtro.setText("sorriso")
        self.assertEqual(self.janela.historico_tabela.rowCount(), 1)
        self.janela.historico_tabela.setCurrentCell(0, 0)
        self.janela._abrir_resultado_historico()

        self.assertEqual(self.janela.titulo_pagina.text(), "Análise da região")
        self.assertEqual(self.janela._resultado_visual.caminho_json, resultado.resolve())

    def test_salva_yaml_com_os_dados_do_formulario(self) -> None:
        self.janela.nome_regiao.setText("Norte de MT")
        destino = self.janela._salvar_configuracao(avisar=False)

        self.assertEqual(destino, self.pasta / "config.yaml")
        payload = yaml.safe_load(destino.read_text(encoding="utf-8"))
        self.assertEqual(payload["area"]["nome"], "Norte de MT")
        self.assertEqual(payload["sincronizacao"]["tamanho_lote"], 100)
        self.assertEqual(payload["analise"]["modelo"], "analise/models/best.pt")
        self.assertEqual(payload["analise"]["confianca_minima"], 0.25)

    def test_bandas_e_colecao_sao_selecionaveis_por_checkbox(self) -> None:
        self.janela.bandas_checkboxes["B02"].setChecked(False)
        self.janela.bandas_checkboxes["NDVI"].setChecked(False)
        self.janela.colecoes_checkboxes["S2_L2A-1"].setChecked(True)

        dados = self.janela._coletar_dados()

        self.assertNotIn("B02", dados["bandas"])
        self.assertNotIn("NDVI", dados["bandas"])
        self.assertIn("B03", dados["bandas"])
        self.assertEqual(dados["colecao"], "S2_L2A-1")
        self.assertEqual(
            self.janela.bandas_checkboxes["B02"].accessibleDescription(),
            self.janela.DESCRICOES_BANDAS["B02"],
        )

    def test_aplica_area_selecionada_no_mapa(self) -> None:
        self.janela.mapa.bbox = [-59.5, -14.2, -57.1, -12.4]
        self.janela.mapa.capturar_bbox()
        self.app.processEvents()

        self.assertAlmostEqual(self.janela.oeste.value(), -59.5)
        self.assertAlmostEqual(self.janela.sul.value(), -14.2)
        self.assertAlmostEqual(self.janela.leste.value(), -57.1)
        self.assertAlmostEqual(self.janela.norte.value(), -12.4)

    def test_controles_numericos_usam_deslizadores_e_preservam_precision(self) -> None:
        self.assertIsInstance(self.janela.nuvem_max_pct.deslizador, QtWidgets.QSlider)
        self.assertEqual(
            self.janela.nuvem_max_pct.deslizador.orientation(),
            QtCore.Qt.Orientation.Horizontal,
        )
        self.janela._receber_bbox([-61.650006, -18.050006, -50.200006, -7.300006])

        self.assertEqual(self.janela.oeste.value(), -61.650006)
        self.assertEqual(self.janela.norte.value(), -7.300006)
        self.assertTrue(self.janela.findChildren(QtWidgets.QToolButton, "helpIcon"))
        self.assertTrue(
            all(icone.toolTip() for icone in self.janela.findChildren(QtWidgets.QToolButton, "helpIcon"))
        )
        helper = self.janela.findChildren(QtWidgets.QToolButton, "helpIcon")[0]
        helper.enterEvent(QtCore.QEvent(QtCore.QEvent.Type.Enter))
        self.assertEqual(QtWidgets.QToolTip.text(), helper.toolTip())
        helper.leaveEvent(QtCore.QEvent(QtCore.QEvent.Type.Leave))
        self.assertEqual(self.janela.chunk_mb.rotulo_valor.text(), "1 MB")
        self.assertEqual(self.janela.dataset_rgb_minimo.rotulo_valor.text(), "0")

    def test_perfis_sao_agrupados_e_carregados_corretamente(self) -> None:
        primeiro = self.janela.store.salvar(
            {
                "nome_regiao": "MT Norte",
                "uf": "MT",
                "bbox": [-58, -13, -55, -10],
                "bandas": ["B02"],
            }
        )
        segundo = self.janela.store.salvar(
            {
                "nome_regiao": "MT Sul",
                "uf": "MT",
                "bbox": [-57, -17, -54, -14],
                "bandas": ["B04"],
            }
        )
        self.janela._recarregar_perfis()

        grupo = self.janela.perfis.topLevelItem(0)
        self.assertEqual(grupo.text(0), "MT")
        self.assertEqual(grupo.childCount(), 2)
        ids = {grupo.child(i).data(0, QtCore.Qt.ItemDataRole.UserRole) for i in range(2)}
        self.assertEqual(ids, {primeiro, segundo})

        item_segundo = next(
            grupo.child(i)
            for i in range(grupo.childCount())
            if grupo.child(i).data(0, QtCore.Qt.ItemDataRole.UserRole) == segundo
        )
        self.janela.perfis.setCurrentItem(item_segundo)
        perfil = self.janela._perfil_selecionado()
        self.assertEqual(perfil["nome_regiao"], "MT Sul")

    def test_executa_cli_e_exibe_saida_no_log(self) -> None:
        script = self.pasta / "cli_fake.py"
        script.write_text(
            "import sys\nprint('operacao-fake-ok')\nprint('argumentos=' + ' '.join(sys.argv[1:]))\n",
            encoding="utf-8",
        )
        gui.SCRIPT_CLI = script
        indice = self.janela.operacao.findData("baixar")
        self.janela.operacao.setCurrentIndex(indice)
        self.janela.max_execucao.setValue(2)

        self.janela._executar()
        self._aguardar_processo()

        texto = self.janela.log.toPlainText()
        self.assertIn("operacao-fake-ok", texto)
        self.assertIn("--baixar", texto)
        self.assertIn("--max-itens 2", texto)
        self.assertIn("[Concluído]", texto)
        self.assertTrue(self.janela.btn_executar.isEnabled())
        self.assertFalse(self.janela.btn_cancelar.isEnabled())

    def test_binario_empacotado_reentra_em_modo_cli(self) -> None:
        with (
            patch.object(gui, "EMPACOTADO", True),
            patch.object(gui.sys, "executable", "/usr/bin/sentinel2-mt"),
        ):
            programa, argumentos, pasta = gui.comando_cli_empacotado(
                ["--config", "/tmp/config.yaml", "--baixar"]
            )

        self.assertEqual(programa, "/usr/bin/sentinel2-mt")
        self.assertEqual(
            argumentos,
            ["--cli", "--config", "/tmp/config.yaml", "--baixar"],
        )
        self.assertEqual(pasta, Path.home())

    def test_execucao_mantem_selecao_legivel_para_proxima_operacao(self) -> None:
        indice = self.janela.operacao.findData("baixar")
        self.janela.operacao.setCurrentIndex(indice)

        self.janela._definir_execucao(True, "Executando")

        self.assertTrue(self.janela.operacao.isEnabled())
        self.assertTrue(self.janela.max_execucao.isEnabled())
        self.assertFalse(self.janela.btn_executar.isEnabled())
        self.assertTrue(self.janela.btn_cancelar.isEnabled())
        grupo = gui.QtGui.QPalette.ColorGroup.Disabled
        papel = gui.QtGui.QPalette.ColorRole
        paleta = self.app.palette()
        self.assertEqual(
            paleta.color(grupo, papel.Text).name(),
            gui.CORES["desabilitado_texto"],
        )
        self.assertEqual(
            paleta.color(grupo, papel.Base).name(),
            gui.CORES["desabilitado_fundo"],
        )

        self.janela._definir_execucao(False, "Pronto")

    def test_carrega_resultado_da_analise_em_tela(self) -> None:
        pasta_dataset = self.pasta / "dataset"
        pasta_analises = self.pasta / "analises"
        pasta_dataset.mkdir()
        pasta_analises.mkdir()
        self.janela.pasta_analises.setText(str(pasta_analises))
        self.janela.historico_analises.setText(str(self.pasta / "historico.sqlite3"))
        self.janela._coletar_dados = lambda: {
            **gui.MainWindow._coletar_dados(self.janela),
            "pasta_dataset": str(pasta_dataset),
            "pasta_analises": str(pasta_analises),
        }
        self.assertIsNotNone(self.janela._salvar_configuracao(avisar=False))
        original = pasta_dataset / "original.png"
        anotada = pasta_analises / "anotada.png"
        imagem = gui.QtGui.QPixmap(24, 24)
        imagem.fill(gui.QtGui.QColor("green"))
        self.assertTrue(imagem.save(str(original), "PNG"))
        self.assertTrue(imagem.save(str(anotada), "PNG"))
        relatorio = pasta_analises / "relatorio.pdf"
        relatorio.write_bytes(b"%PDF-fake")
        resultado = pasta_analises / "resultado.json"
        resultado.write_text(
            json.dumps(
                {
                    "analysis_id": "a1",
                    "regiao": "Sinop",
                    "periodo_inicio": "2026-01-01",
                    "periodo_fim": "2026-03-31",
                    "scene_ids": ["CENA"],
                    "dispositivo": "cpu",
                    "imagem_original": str(original),
                    "imagem_analisada": str(anotada),
                    "caminho_relatorio": str(relatorio),
                    "estatisticas": {
                        "total_deteccoes": 2,
                        "por_classe": {"soja": 2},
                        "confianca_media_por_classe": {"soja": 0.8},
                        "confianca_minima_por_classe": {"soja": 0.7},
                        "confianca_maxima_por_classe": {"soja": 0.9},
                    },
                }
            ),
            encoding="utf-8",
        )

        self.janela._carregar_resultado_analise(resultado)

        self.assertEqual(
            self.janela.stack.currentIndex(), self.janela._indice_pagina("Análise da região")
        )
        self.assertEqual(self.janela.analise_tabela.rowCount(), 1)
        self.assertEqual(self.janela.analise_tabela.item(0, 0).text(), "soja")
        self.assertIn("Detecções: 2", self.janela.analise_info.text())
        self.assertTrue(self.janela.btn_relatorio.isEnabled())
        self.assertTrue(self.janela.btn_salvar_relatorio.isEnabled())

        destino = self.pasta / "relatorio-exportado.pdf"
        with patch.object(
            gui.QtWidgets.QFileDialog,
            "getSaveFileName",
            return_value=(str(destino), "Documento PDF (*.pdf)"),
        ):
            self.janela._salvar_relatorio_analise()
        self.assertEqual(destino.read_bytes(), b"%PDF-fake")

    def test_marcador_de_resultado_rejeita_caminho_absoluto_e_traversal(self) -> None:
        self.janela._operacao_em_execucao = "analisar"

        self.janela._processar_linha_resultado(
            "[ANALISE_RESULTADO] /tmp/resultado.json"
        )
        self.assertIsNone(self.janela._resultado_pendente)
        self.janela._processar_linha_resultado(
            "[ANALISE_RESULTADO] ../resultado.json"
        )
        self.assertIsNone(self.janela._resultado_pendente)

        self.janela._processar_linha_resultado(
            "[ANALISE_RESULTADO] data/analises/a1/resultado.json"
        )
        self.assertEqual(
            self.janela._resultado_pendente,
            gui.ROOT / "data/analises/a1/resultado.json",
        )

    def test_cancela_operacao_em_andamento(self) -> None:
        script = self.pasta / "cli_lenta.py"
        script.write_text(
            "import time\nprint('iniciada', flush=True)\ntime.sleep(10)\n",
            encoding="utf-8",
        )
        gui.SCRIPT_CLI = script
        self.janela._executar()
        limite_inicio = time.monotonic() + 2
        while (
            self.janela.processo.state() == QtCore.QProcess.ProcessState.Starting
            and time.monotonic() < limite_inicio
        ):
            self.app.processEvents()
        self.assertTrue(self.janela.btn_cancelar.isEnabled())

        self.janela._cancelar()
        self._aguardar_processo(4)

        self.assertEqual(
            self.janela.processo.state(), QtCore.QProcess.ProcessState.NotRunning
        )
        self.assertTrue(self.janela.btn_executar.isEnabled())
        self.assertFalse(self.janela.btn_cancelar.isEnabled())
