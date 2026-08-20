from unittest import TestCase

from sentinel2_mt.gui_theme import CORES, PARES_CONTRASTE, contrastes, folha_estilos


class TestTemaGui(TestCase):
    def test_todos_os_textos_atendem_wcag_aa(self) -> None:
        resultados = contrastes()
        falhas = {
            nome: round(resultados[nome], 2)
            for nome, (_, _, minimo) in PARES_CONTRASTE.items()
            if resultados[nome] < minimo
        }

        self.assertEqual(falhas, {})

    def test_estilo_define_cor_de_campos_e_popups(self) -> None:
        estilo = folha_estilos()

        self.assertIn(f"color: {CORES['texto']}", estilo)
        self.assertIn("QComboBox QAbstractItemView", estilo)
        self.assertIn("QCalendarWidget QAbstractItemView", estilo)
        self.assertIn("QHeaderView::section", estilo)
        self.assertIn("QToolTip", estilo)
