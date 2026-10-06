from __future__ import annotations

import importlib.util
import os
from unittest import TestCase, mock, skipUnless


PYSIDE_DISPONIVEL = importlib.util.find_spec("PySide6") is not None

if PYSIDE_DISPONIVEL:
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtGui, QtTest, QtWidgets

    import gerar_config_gui as gui
    MapaWidgetReal = gui.MapaWidget


@skipUnless(PYSIDE_DISPONIVEL, "PySide6 é uma dependência opcional da GUI")
class TestMapaWidget(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def test_html_tem_fallback_csp_integridade_e_estado_explicito(self) -> None:
        html = MapaWidgetReal._html()

        self.assertIn("Content-Security-Policy", html)
        self.assertIn("unpkg.com/leaflet@1.9.4", html)
        self.assertIn("cdn.jsdelivr.net/npm/leaflet@1.9.4", html)
        self.assertIn("sha384-", html)
        self.assertIn("window.mapReady = false", html)
        self.assertIn("window.mapError = ''", html)
        self.assertIn("boxZoom: false", html)

    def test_documento_so_e_iniciado_quando_widget_e_exibido(self) -> None:
        with mock.patch.object(MapaWidgetReal, "setHtml") as set_html:
            mapa = MapaWidgetReal([-61.65, -18.05, -50.2, -7.3])
            self.addCleanup(mapa.close)

            set_html.assert_not_called()
            mapa._iniciar_mapa()
            mapa._iniciar_mapa()

            set_html.assert_called_once()

    def test_estado_pronto_oculta_loading_e_emite_sinal(self) -> None:
        mapa = MapaWidgetReal([-61.65, -18.05, -50.2, -7.3])
        self.addCleanup(mapa.close)
        pronto = QtTest.QSignalSpy(mapa.mapaPronto)
        with (
            mock.patch.object(mapa, "_aplicar_bbox_pendente") as aplicar,
            mock.patch.object(mapa, "_invalidar_tamanho") as invalidar,
        ):
            mapa._receber_estado('{"ready": true, "error": ""}')

        self.assertTrue(mapa._mapa_pronto)
        self.assertTrue(mapa._estado_mapa.isHidden())
        self.assertEqual(pronto.count(), 1)
        aplicar.assert_called_once()
        invalidar.assert_called_once()

    def test_estado_de_erro_fica_visivel_e_permite_nova_tentativa(self) -> None:
        mapa = MapaWidgetReal([-61.65, -18.05, -50.2, -7.3])
        self.addCleanup(mapa.close)
        erros = QtTest.QSignalSpy(mapa.mapaErro)

        mapa._receber_estado('{"ready": false, "error": "Leaflet indisponível"}')

        self.assertTrue(mapa._erro_mapa)
        self.assertFalse(mapa._estado_mapa.isHidden())
        self.assertIn("Leaflet indisponível", mapa._estado_mapa.text())
        self.assertEqual(erros.count(), 1)

        with mock.patch.object(mapa, "_iniciar_mapa") as iniciar:
            mapa.showEvent(QtGui.QShowEvent())
        self.assertFalse(mapa._erro_mapa)
        self.assertFalse(mapa._html_iniciado)
        iniciar.assert_called_once()

    def test_selecao_desenhada_e_preservada_ao_reativar(self) -> None:
        mapa = MapaWidgetReal([-61.65, -18.05, -50.2, -7.3])
        self.addCleanup(mapa.close)
        mapa._mapa_pronto = True
        selecoes = QtTest.QSignalSpy(mapa.areaSelecionada)
        nova_bbox = [-59.5, -14.2, -57.1, -12.4]

        mapa._receber_selecao("[-59.5, -14.2, -57.1, -12.4]")

        self.assertEqual(mapa._bbox_pendente, nova_bbox)
        self.assertEqual(selecoes.count(), 1)
        with (
            mock.patch.object(mapa, "_iniciar_mapa"),
            mock.patch.object(mapa, "_invalidar_tamanho") as invalidar,
            mock.patch.object(mapa, "_aplicar_bbox_pendente") as aplicar,
        ):
            mapa.reativar()
        invalidar.assert_called_once()
        aplicar.assert_not_called()
