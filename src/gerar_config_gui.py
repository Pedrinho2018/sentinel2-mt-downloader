from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml
from PySide6 import QtCore, QtGui, QtNetwork, QtWidgets
from PySide6.QtWebEngineWidgets import QWebEngineView

from sentinel2_mt.config_builder import GeradorConfiguracao, gerar_config, salvar_config as persistir_config
from sentinel2_mt.configuracao import ConfiguracaoProjeto
from sentinel2_mt.analise.historico import RepositorioHistoricoAnalises
from sentinel2_mt.analise.resultados import (
    AVISO_AREA,
    AVISO_CULTURA,
    aviso_classes_modelo,
    ItemResultado,
    ResultadoVisualizacao,
    carregar_resultado,
    exportar_csv,
    exportar_json,
    filtrar_historico,
    indicadores_vegetacao,
    modelo_classificacao_aprovado,
)
from sentinel2_mt.gui_support import (
    LocalConfigStore,
    montar_argumentos_operacao,
    normalizar_bbox,
)
from sentinel2_mt.gui_theme import CORES, folha_estilos
from gui_rotulagem import PainelRotulagem
from sentinel2_mt.painel import ResumoPainel, coletar_resumo_painel, formatar_bytes
from sentinel2_mt.assistente_download import (
    CenaEncontrada,
    EstadoDownload,
    estimar_tamanho,
    filtrar_cenas,
    ler_evento,
    media_tamanho_cena_local,
)
from sentinel2_mt.http import listar_parciais_legados, remover_parciais_legados
from sentinel2_mt.laboratorio.datasets import (
    caminho_miniatura,
    descobrir_recortes,
    filtrar_imagens,
    gerar_miniatura,
    importar_externas,
    preparar_recortes,
    resolver_pasta_laboratorio,
    resumo_safras,
    verificar_lote,
)
from sentinel2_mt.laboratorio.projetos import (
    ROTULOS_TIPO,
    TIPOS_PROJETO,
    ImagemDataset,
    RepositorioLaboratorio,
)
from sentinel2_mt.area import (
    AreaImportada,
    Municipio,
    buscar_municipios,
    carregar_municipios,
    importar_area,
    normalizar_texto,
)


EMPACOTADO = bool(getattr(sys, "frozen", False))
if EMPACOTADO:
    ROOT = Path.home()
    XDG_CONFIG_HOME = Path(
        os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    ).expanduser()
    CONFIG_USUARIO = XDG_CONFIG_HOME / "sentinel2-mt"
    DEFAULT_CONFIG = CONFIG_USUARIO / "config.yaml"
    LOCAL_DB = CONFIG_USUARIO / "configuracoes_local.db"
    SCRIPT_CLI = Path(sys.executable)
else:
    ROOT = Path(__file__).resolve().parents[1]
    DEFAULT_CONFIG = ROOT / "config" / "config.yaml"
    LOCAL_DB = ROOT / "config" / "configuracoes_local.db"
    SCRIPT_CLI = ROOT / "src" / "baixar_inpe_mt.py"

GUI_ASSETS = Path(getattr(sys, "_MEIPASS", ROOT)) / "assets"
FUNDO_GUI = GUI_ASSETS / "mato-grosso-background.png"
MUNICIPIOS_IBGE = GUI_ASSETS / "municipios_ibge.json"

# Compatibilidade com integrações que importavam esta função do módulo da GUI.
bbox_para_yaml = normalizar_bbox


ESTILO = folha_estilos()


def comando_cli_empacotado(argumentos: list[str]) -> tuple[str, list[str], Path]:
    """Monta a operação da GUI para código-fonte ou executável congelado."""
    if EMPACOTADO:
        return sys.executable, ["--cli", *argumentos], Path.home()
    return sys.executable, ["-u", str(SCRIPT_CLI), *argumentos], ROOT


def botao(texto: str, tipo: str = "secondaryButton") -> QtWidgets.QPushButton:
    componente = QtWidgets.QPushButton(texto)
    componente.setObjectName(tipo)
    componente.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
    return componente


class BotaoAjuda(QtWidgets.QToolButton):
    """Helper contextual que mostra a explicação imediatamente ao passar o mouse."""

    def __init__(self, ajuda: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._ajuda = ajuda

    def enterEvent(self, evento: QtCore.QEvent) -> None:
        QtWidgets.QToolTip.showText(
            self.mapToGlobal(self.rect().bottomLeft()), self._ajuda, self
        )

    def leaveEvent(self, evento: QtCore.QEvent) -> None:
        QtWidgets.QToolTip.hideText()


def rotulo_com_ajuda(texto: str, ajuda: str) -> QtWidgets.QWidget:
    """Cria um rótulo compacto com ajuda contextual acessível."""
    conteiner = QtWidgets.QWidget()
    layout = QtWidgets.QHBoxLayout(conteiner)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(5)
    rotulo = QtWidgets.QLabel(texto)
    icone = BotaoAjuda(ajuda)
    icone.setObjectName("helpIcon")
    icone.setText("i")
    icone.setToolTip(ajuda)
    icone.setStatusTip(ajuda)
    icone.setAccessibleName(f"Ajuda: {texto}")
    icone.setAccessibleDescription(ajuda)
    icone.setCursor(QtCore.Qt.CursorShape.WhatsThisCursor)
    icone.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
    layout.addWidget(rotulo)
    layout.addWidget(icone)
    layout.addStretch()
    return conteiner


def configurar_aplicacao(app: QtWidgets.QApplication) -> None:
    """Neutraliza temas do sistema e garante a mesma legibilidade em todo desktop."""
    if app.property("sentinel2TemaAplicado"):
        return
    app.setStyle("Fusion")
    paleta = QtGui.QPalette()
    papel = QtGui.QPalette.ColorRole
    paleta.setColor(papel.Window, QtGui.QColor(CORES["fundo"]))
    paleta.setColor(papel.WindowText, QtGui.QColor(CORES["texto"]))
    paleta.setColor(papel.Base, QtGui.QColor(CORES["superficie_campo"]))
    paleta.setColor(papel.AlternateBase, QtGui.QColor(CORES["secundario_fundo"]))
    paleta.setColor(papel.ToolTipBase, QtGui.QColor(CORES["superficie"]))
    paleta.setColor(papel.ToolTipText, QtGui.QColor(CORES["texto"]))
    paleta.setColor(papel.Text, QtGui.QColor(CORES["texto"]))
    paleta.setColor(papel.Button, QtGui.QColor(CORES["secundario_fundo"]))
    paleta.setColor(papel.ButtonText, QtGui.QColor(CORES["secundario_texto"]))
    paleta.setColor(papel.Highlight, QtGui.QColor(CORES["destaque_claro"]))
    paleta.setColor(papel.HighlightedText, QtGui.QColor(CORES["destaque_texto"]))
    paleta.setColor(papel.Link, QtGui.QColor(CORES["destaque"]))
    paleta.setColor(papel.PlaceholderText, QtGui.QColor(CORES["texto_suave"]))
    desabilitado = QtGui.QPalette.ColorGroup.Disabled
    paleta.setColor(
        desabilitado, papel.WindowText, QtGui.QColor(CORES["desabilitado_texto"])
    )
    paleta.setColor(desabilitado, papel.Text, QtGui.QColor(CORES["desabilitado_texto"]))
    paleta.setColor(
        desabilitado, papel.ButtonText, QtGui.QColor(CORES["desabilitado_texto"])
    )
    paleta.setColor(
        desabilitado, papel.Base, QtGui.QColor(CORES["desabilitado_fundo"])
    )
    paleta.setColor(
        desabilitado, papel.Button, QtGui.QColor(CORES["desabilitado_fundo"])
    )
    app.setPalette(paleta)
    app.setProperty("sentinel2TemaAplicado", True)


class Cartao(QtWidgets.QFrame):
    def __init__(self, titulo: str, ajuda: str = "") -> None:
        super().__init__()
        self.setObjectName("card")
        self.layout_principal = QtWidgets.QVBoxLayout(self)
        self.layout_principal.setContentsMargins(18, 16, 18, 18)
        self.layout_principal.setSpacing(10)
        rotulo = QtWidgets.QLabel(titulo)
        rotulo.setObjectName("cardTitle")
        self.layout_principal.addWidget(rotulo)
        if ajuda:
            descricao = QtWidgets.QLabel(ajuda)
            descricao.setObjectName("cardHelp")
            descricao.setWordWrap(True)
            self.layout_principal.addWidget(descricao)


class ControleNumerico(QtWidgets.QWidget):
    """Controle numérico por deslizador, com o valor legível ao lado do trilho."""

    valueChanged = QtCore.Signal(object)

    def __init__(
        self,
        minimo: float,
        maximo: float,
        valor: float,
        sufixo: str = "",
        *,
        casas_decimais: int = 0,
    ) -> None:
        super().__init__()
        self._minimo = minimo
        self._maximo = maximo
        self._casas_decimais = casas_decimais
        self._escala = 10**casas_decimais
        self._sufixo = sufixo
        self._texto_valor_especial = ""

        self.deslizador = QtWidgets.QSlider(QtCore.Qt.Orientation.Horizontal)
        self.deslizador.setObjectName("numericSlider")
        self.deslizador.setRange(
            round(minimo * self._escala), round(maximo * self._escala)
        )
        self.deslizador.setSingleStep(1)
        self.deslizador.setPageStep(max(1, (self.deslizador.maximum() - self.deslizador.minimum()) // 20))
        self.deslizador.setTickPosition(QtWidgets.QSlider.TickPosition.TicksBelow)
        self.deslizador.setTickInterval(
            max(1, (self.deslizador.maximum() - self.deslizador.minimum()) // 5)
        )
        self.deslizador.setToolTip("Arraste para ajustar o valor; use as setas para ajustes finos.")

        self.rotulo_valor = QtWidgets.QLabel()
        self.rotulo_valor.setObjectName("numericSliderValue")
        self.rotulo_valor.setAlignment(
            QtCore.Qt.AlignmentFlag.AlignRight | QtCore.Qt.AlignmentFlag.AlignVCenter
        )
        self.rotulo_valor.setMinimumWidth(82)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        layout.addWidget(self.deslizador, 1)
        layout.addWidget(self.rotulo_valor)
        self.deslizador.valueChanged.connect(self._atualizar_rotulo)
        self.setValue(valor)

    def _atualizar_rotulo(self, _posicao: int) -> None:
        valor = self.value()
        if self._texto_valor_especial and valor == self._minimo:
            texto = self._texto_valor_especial
        elif self._casas_decimais:
            texto = f"{valor:.{self._casas_decimais}f}{self._sufixo}"
        else:
            texto = f"{int(valor)}{self._sufixo}"
        self.rotulo_valor.setText(texto)
        self.deslizador.setAccessibleName(texto)
        self.valueChanged.emit(valor)

    def value(self) -> int | float:
        valor = self.deslizador.value() / self._escala
        return round(valor, self._casas_decimais) if self._casas_decimais else int(valor)

    def setValue(self, valor: int | float) -> None:
        posicao = round(float(valor) * self._escala)
        mudou = posicao != self.deslizador.value()
        self.deslizador.setValue(posicao)
        if not mudou:
            self._atualizar_rotulo(posicao)

    def setSpecialValueText(self, texto: str) -> None:
        self._texto_valor_especial = texto
        self._atualizar_rotulo(self.deslizador.value())

    def setEnabled(self, ativo: bool) -> None:
        super().setEnabled(ativo)
        self.deslizador.setEnabled(ativo)


class MapaWidget(QWebEngineView):
    areaSelecionada = QtCore.Signal(object)
    mapaPronto = QtCore.Signal()
    mapaErro = QtCore.Signal(str)
    desenhoConcluido = QtCore.Signal()

    def __init__(self, bbox_inicial: list[float], parent=None) -> None:
        super().__init__(parent)
        self._bbox_pendente = normalizar_bbox(bbox_inicial)
        self._html_iniciado = False
        self._mapa_pronto = False
        self._erro_mapa = False
        self._tentativas_estado = 0
        self.setMinimumHeight(460)
        self.setStyleSheet("background: #dfe9e5;")

        self._estado_mapa = QtWidgets.QLabel("Carregando mapa...", self)
        self._estado_mapa.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self._estado_mapa.setStyleSheet(
            "background: #dfe9e5; color: #31554a; font-size: 14px; font-weight: 600;"
        )
        self._estado_mapa.show()

        self._timer_estado = QtCore.QTimer(self)
        self._timer_estado.setInterval(250)
        self._timer_estado.timeout.connect(self._verificar_estado)
        self.loadFinished.connect(self._ao_carregar)

        # Verifica a seleção apenas enquanto o modo de desenho está ativo.
        self._versao_selecao = 0
        self._timer_desenho = QtCore.QTimer(self)
        self._timer_desenho.setInterval(300)
        self._timer_desenho.timeout.connect(self._verificar_desenho)

    def showEvent(self, evento: QtGui.QShowEvent) -> None:
        super().showEvent(evento)
        if self._erro_mapa:
            self._html_iniciado = False
            self._erro_mapa = False
        self._iniciar_mapa()
        if self._mapa_pronto:
            QtCore.QTimer.singleShot(0, self.reativar)
            QtCore.QTimer.singleShot(150, self.reativar)

    def resizeEvent(self, evento: QtGui.QResizeEvent) -> None:
        super().resizeEvent(evento)
        self._estado_mapa.setGeometry(self.rect())

    def _iniciar_mapa(self) -> None:
        if self._html_iniciado:
            return
        self._html_iniciado = True
        self._erro_mapa = False
        self._tentativas_estado = 0
        self._estado_mapa.setText("Carregando mapa...")
        self._estado_mapa.show()
        self._estado_mapa.raise_()
        self.setHtml(self._html(), QtCore.QUrl("https://mapa.local/"))

    def _ao_carregar(self, carregou: bool) -> None:
        if not carregou:
            self._mostrar_erro("Não foi possível carregar o documento do mapa.")
            return
        self._timer_estado.start()
        self._verificar_estado()

    def _verificar_estado(self) -> None:
        if not self._html_iniciado or self._mapa_pronto:
            self._timer_estado.stop()
            return
        self._tentativas_estado += 1
        self.page().runJavaScript(
            "JSON.stringify({ready: window.mapReady === true, error: window.mapError || ''})",
            self._receber_estado,
        )

    def _receber_estado(self, valor: str | None) -> None:
        try:
            estado = json.loads(valor) if valor else {}
        except (TypeError, ValueError):
            estado = {}
        if estado.get("ready"):
            self._timer_estado.stop()
            self._mapa_pronto = True
            self._estado_mapa.hide()
            self._aplicar_bbox_pendente()
            self._invalidar_tamanho()
            self.mapaPronto.emit()
            return
        if estado.get("error"):
            self._mostrar_erro(str(estado["error"]))
            return
        if self._tentativas_estado >= 40:
            self._mostrar_erro(
                "O mapa não respondeu. Verifique a conexão e tente abrir esta página novamente."
            )

    def _mostrar_erro(self, mensagem: str) -> None:
        self._timer_estado.stop()
        self._erro_mapa = True
        self._estado_mapa.setText(mensagem)
        self._estado_mapa.show()
        self._estado_mapa.raise_()
        self.mapaErro.emit(mensagem)

    def _invalidar_tamanho(self) -> None:
        if self._mapa_pronto:
            self.page().runJavaScript("window.invalidateMapSize && window.invalidateMapSize();")

    def reativar(self) -> None:
        self._iniciar_mapa()
        if not self._mapa_pronto:
            return
        self._invalidar_tamanho()

    def exibir_bbox(self, bbox: list[float]) -> None:
        self._bbox_pendente = normalizar_bbox(bbox)
        if self._mapa_pronto:
            self._aplicar_bbox_pendente()

    def _aplicar_bbox_pendente(self) -> None:
        self.page().runJavaScript(
            f"window.setSelection && window.setSelection({json.dumps(self._bbox_pendente)});"
        )

    def ativar_desenho(self, ativo: bool = True) -> bool:
        """Liga o desenho com clique e arraste, sem exigir a tecla Shift."""
        if not self._mapa_pronto:
            return False
        self.page().runJavaScript(
            f"window.setDrawMode && window.setDrawMode({'true' if ativo else 'false'});"
        )
        if ativo:
            self._timer_desenho.start()
        else:
            self._timer_desenho.stop()
        return True

    def _verificar_desenho(self) -> None:
        self.page().runJavaScript("window.selectionVersion || 0", self._receber_versao_selecao)

    def _receber_versao_selecao(self, valor: object) -> None:
        try:
            versao = int(valor or 0)
        except (TypeError, ValueError):
            return
        if versao > self._versao_selecao:
            self._versao_selecao = versao
            self._timer_desenho.stop()
            self.capturar_bbox()
            self.desenhoConcluido.emit()

    def capturar_bbox(self) -> None:
        if not self._mapa_pronto:
            self.areaSelecionada.emit(None)
            return
        self.page().runJavaScript(
            "JSON.stringify(window.currentSelection)", self._receber_selecao
        )

    def _receber_selecao(self, valor: str | None) -> None:
        try:
            bbox = json.loads(valor) if valor else None
            bbox = normalizar_bbox(bbox) if isinstance(bbox, list) else None
        except (TypeError, ValueError):
            bbox = None
        if bbox is not None:
            self._bbox_pendente = bbox
        self.areaSelecionada.emit(bbox)

    @staticmethod
    def _html() -> str:
        return """
        <!doctype html><html><head><meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <meta http-equiv="Content-Security-Policy" content="default-src 'none';
          script-src 'nonce-sentinel2-map' https://unpkg.com https://cdn.jsdelivr.net;
          style-src 'unsafe-inline' https://unpkg.com https://cdn.jsdelivr.net;
          img-src data: https://*.tile.openstreetmap.org;" />
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
          integrity="sha384-sHL9NAb7lN7rfvG5lfHpm643Xkcjzp4jFvuavGOndn6pjVqS6ny56CAt3nsEVT4H"
          crossorigin="anonymous" />
        <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css"
          integrity="sha384-sHL9NAb7lN7rfvG5lfHpm643Xkcjzp4jFvuavGOndn6pjVqS6ny56CAt3nsEVT4H"
          crossorigin="anonymous" />
        <style>
          html, body, #map { margin: 0; width: 100%; height: 100%; background: #dfe9e5; }
          .hint { position: absolute; z-index: 900; top: 12px; left: 50%; transform: translateX(-50%);
            background: rgba(16,43,36,.92); color: white; border-radius: 8px; padding: 8px 12px;
            font: 12px sans-serif; box-shadow: 0 3px 12px rgba(0,0,0,.18); }
        </style></head><body><div id="map"></div>
        <div class="hint" id="hint">Use "Desenhar área" ou segure Shift e arraste para selecionar</div>
        <script nonce="sentinel2-map">
          window.currentSelection = null;
          window.selectionVersion = 0;
          window.drawMode = false;
          window.mapReady = false;
          window.mapError = '';
          let map = null;
          let start = null;
          let rectangle = null;

          window.setSelection = function(bbox, ajustarVisao = true) {
            if (!map || !bbox || bbox.length !== 4) return;
            window.currentSelection = bbox;
            const bounds = [[bbox[1], bbox[0]], [bbox[3], bbox[2]]];
            if (rectangle) map.removeLayer(rectangle);
            rectangle = L.rectangle(bounds, {color: '#168a58', weight: 2, fillOpacity: .16}).addTo(map);
            if (ajustarVisao) {
              map.fitBounds(bounds, {padding: [24, 24], maxZoom: 10, animate: false});
            }
            setTimeout(() => map.invalidateSize(), 100);
          };

          window.setDrawMode = function(ativo) {
            window.drawMode = !!ativo;
            const dica = document.getElementById('hint');
            if (dica) {
              dica.textContent = window.drawMode
                ? 'Clique e arraste no mapa para desenhar a área'
                : 'Use "Desenhar área" ou segure Shift e arraste para selecionar';
            }
            if (map) map.getContainer().style.cursor = window.drawMode ? 'crosshair' : '';
          };

          window.invalidateMapSize = function() {
            if (map) map.invalidateSize();
          };

          function iniciarMapa() {
            try {
              map = L.map('map', {worldCopyJump: true, boxZoom: false}).setView([-15.5, -55.0], 5);
              L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                attribution: '&copy; OpenStreetMap contributors'
              }).addTo(map);
              map.on('mousedown', function(event) {
                if (!event.originalEvent.shiftKey && !window.drawMode) return;
                start = event.latlng;
                map.dragging.disable();
              });
              map.on('mousemove', function(event) {
                if (!start) return;
                window.setSelection([
                  Math.min(start.lng, event.latlng.lng), Math.min(start.lat, event.latlng.lat),
                  Math.max(start.lng, event.latlng.lng), Math.max(start.lat, event.latlng.lat)
                ], false);
              });
              map.on('mouseup', function(event) {
                if (!start) return;
                window.setSelection([
                  Math.min(start.lng, event.latlng.lng), Math.min(start.lat, event.latlng.lat),
                  Math.max(start.lng, event.latlng.lng), Math.max(start.lat, event.latlng.lat)
                ], false);
                start = null;
                map.dragging.enable();
                window.selectionVersion += 1;
                window.setDrawMode(false);
              });
              window.addEventListener('resize', window.invalidateMapSize);
              window.mapReady = true;
            } catch (erro) {
              window.mapError = 'Falha ao inicializar o mapa: ' + erro.message;
            }
          }

          function carregarLeaflet(indice) {
            const fontes = [
              'https://unpkg.com/leaflet@1.9.4/dist/leaflet.js',
              'https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js'
            ];
            if (indice >= fontes.length) {
              window.mapError = 'Não foi possível carregar a biblioteca do mapa.';
              return;
            }
            const script = document.createElement('script');
            script.src = fontes[indice];
            script.crossOrigin = 'anonymous';
            script.integrity = 'sha384-cxOPjt7s7Iz04uaHJceBmS+qpjv2JkIHNVcuOrM+YHwZOmJGBXI00mdUXEq65HTH';
            script.onload = iniciarMapa;
            script.onerror = () => carregarLeaflet(indice + 1);
            document.head.appendChild(script);
          }

          carregarLeaflet(0);
        </script></body></html>
        """


ALTURA_IMAGEM_RESULTADO = 230


class _PonteTarefa(QtCore.QObject):
    """Progresso e conclusão de tarefas do Laboratório executadas em segundo plano."""

    progresso = QtCore.Signal(int, int)
    concluido = QtCore.Signal(object)


class _PontePainel(QtCore.QObject):
    """Entrega o resumo calculado em segundo plano à thread da interface."""

    concluido = QtCore.Signal(object)


class MainWindow(QtWidgets.QMainWindow):
    DESCRICOES_BANDAS = {
        "B02": "Azul; destaca água e auxilia na composição RGB.",
        "B03": "Verde; útil para vegetação e composição RGB.",
        "B04": "Vermelho; usado na composição RGB e no NDVI.",
        "B05": "Red edge 1; sensível ao vigor da vegetação.",
        "B06": "Red edge 2; auxilia na análise de clorofila.",
        "B07": "Red edge 3; acompanha mudanças na vegetação.",
        "B08": "Infravermelho próximo; usado no NDVI e na composição falsa-cor.",
        "B8A": "Infravermelho próximo estreito; red edge de referência.",
        "B11": "Infravermelho de ondas curtas 1; informa umidade e estrutura.",
        "B12": "Infravermelho de ondas curtas 2; auxilia em umidade e queimadas.",
        "NDVI": "Índice de vegetação por diferença normalizada.",
        "EVI": "Índice de vegetação aprimorado, com menor saturação em áreas densas.",
    }
    COLECOES_DISPONIVEIS = ("S2-16D-2", "S2_L2A-1")
    PAGINAS = (
        ("Início", "Resumo do seu trabalho e próximo passo recomendado"),
        ("Operações", "Execute e acompanhe as operações"),
        ("Área e período", "Escolha a região no mapa"),
        ("Baixar imagens", "Escolha o período, as cenas e acompanhe o download"),
        ("Dados e qualidade", "Ajuste STAC, bandas e filtros"),
        ("Google Drive", "Configure OAuth e sincronização"),
        ("Análise da região", "Resultados agrícolas: talhões detectados e culturas"),
        ("Histórico", "Acesse análises e relatórios locais"),
        ("Laboratório de IA", "Datasets, classes e preparação para treinar modelos"),
        ("Configuração", "Revise YAML e perfis locais"),
        ("Sobre", "Conheça o aplicativo e seu fluxo de trabalho"),
        ("Preciso de ajuda", "Encontre orientações para resolver problemas"),
    )

    def __init__(self) -> None:
        super().__init__()
        configurar_aplicacao(QtWidgets.QApplication.instance())
        self.setWindowTitle("Sentinel-2 MT • Central de Operações")
        self.resize(1480, 920)
        self.setMinimumSize(1120, 720)
        self.setStyleSheet(ESTILO)

        self.store = LocalConfigStore(LOCAL_DB)
        self.processo = QtCore.QProcess(self)
        self.processo.setProcessChannelMode(QtCore.QProcess.ProcessChannelMode.MergedChannels)
        self.processo.readyReadStandardOutput.connect(self._ler_saida)
        self.processo.finished.connect(self._processo_finalizado)
        self.processo.errorOccurred.connect(self._erro_processo)
        self._saida_pendente = ""
        self._ultimo_resultado: dict[str, Any] | None = None
        self._operacao_em_execucao = ""
        self._resultado_pendente: Path | None = None
        self._assist_cenas: list[CenaEncontrada] = []
        self._assist_estado = EstadoDownload()
        self._assist_cancelado = False
        self._assist_rede = QtNetwork.QNetworkAccessManager(self)
        self._assist_resposta_miniatura: QtNetwork.QNetworkReply | None = None
        self._lab_pool = QtCore.QThreadPool(self)
        self._lab_pool.setMaxThreadCount(1)
        self._lab_ocupado = False
        self._lab_ponte = _PonteTarefa(self)
        self._lab_ponte.progresso.connect(self._lab_progresso)
        self._lab_ponte.concluido.connect(self._lab_tarefa_concluida)
        self._lab_ao_concluir = None
        self._pool_painel = QtCore.QThreadPool(self)
        self._pool_painel.setMaxThreadCount(1)
        self._painel_em_coleta = False
        self._ponte_painel = _PontePainel(self)
        self._ponte_painel.concluido.connect(self._aplicar_resumo_painel)

        self._criar_campos()
        self._montar_janela()
        self._conectar_eventos()
        self._atualizar_preview_yaml()
        self._recarregar_perfis()
        self._atualizar_operacao()
        QtCore.QTimer.singleShot(0, self._atualizar_painel)

    def _indice_pagina(self, titulo: str) -> int:
        """Localiza páginas pelo título para não depender da ordem da navegação."""
        for indice, (nome, _) in enumerate(self.PAGINAS):
            if nome == titulo:
                return indice
        raise KeyError(titulo)

    def _criar_campos(self) -> None:
        self.nome_regiao = QtWidgets.QLineEdit("Mato Grosso")
        self.uf = QtWidgets.QLineEdit("MT")
        self.uf.setMaxLength(2)
        self.oeste = self._coordenada(-180, 180, -61.65)
        self.sul = self._coordenada(-90, 90, -18.05)
        self.leste = self._coordenada(-180, 180, -50.20)
        self.norte = self._coordenada(-90, 90, -7.30)

        self.inicio = QtWidgets.QDateEdit(QtCore.QDate(2025, 9, 1))
        self.fim = QtWidgets.QDateEdit(QtCore.QDate(2026, 4, 30))
        for campo in (self.inicio, self.fim):
            campo.setCalendarPopup(True)
            campo.setDisplayFormat("dd/MM/yyyy")

        self.colecao = QtWidgets.QLineEdit("S2-16D-2")
        self.colecao.setPlaceholderText("ID de uma coleção personalizada")
        self.colecoes = QtWidgets.QWidget()
        layout_colecoes = QtWidgets.QVBoxLayout(self.colecoes)
        layout_colecoes.setContentsMargins(0, 0, 0, 0)
        layout_colecoes.setSpacing(2)
        grupo_colecoes = QtWidgets.QButtonGroup(self)
        grupo_colecoes.setExclusive(True)
        self.colecoes_checkboxes: dict[str, QtWidgets.QCheckBox] = {}
        for nome in self.COLECOES_DISPONIVEIS:
            caixa = QtWidgets.QCheckBox(nome)
            caixa.setToolTip("Seleciona esta coleção STAC para a consulta.")
            grupo_colecoes.addButton(caixa)
            layout_colecoes.addWidget(caixa)
            self.colecoes_checkboxes[nome] = caixa
            caixa.toggled.connect(lambda marcado, valor=nome: self._colecao_marcada(valor, marcado))
        self.colecoes_checkboxes["S2-16D-2"].setChecked(True)
        layout_colecoes.addWidget(self.colecao)
        self.colecao.textChanged.connect(self._colecao_personalizada_alterada)

        self.bandas_checkboxes: dict[str, QtWidgets.QCheckBox] = {}
        self.bandas_selecao = QtWidgets.QWidget()
        layout_bandas = QtWidgets.QGridLayout(self.bandas_selecao)
        layout_bandas.setContentsMargins(0, 0, 0, 0)
        layout_bandas.setHorizontalSpacing(16)
        layout_bandas.setVerticalSpacing(5)
        for indice, (nome, descricao) in enumerate(self.DESCRICOES_BANDAS.items()):
            caixa = QtWidgets.QCheckBox(nome)
            caixa.setChecked(True)
            caixa.setToolTip(descricao)
            caixa.setAccessibleDescription(descricao)
            texto = QtWidgets.QLabel(descricao)
            texto.setWordWrap(True)
            texto.setStyleSheet("color: #68758a; font-size: 11px;")
            linha = indice // 2
            coluna = (indice % 2) * 2
            layout_bandas.addWidget(caixa, linha, coluna)
            layout_bandas.addWidget(texto, linha, coluna + 1)
            self.bandas_checkboxes[nome] = caixa
        self.stac_url = QtWidgets.QLineEdit("https://data.inpe.br/bdc/stac/v1/")
        self.filtrar_nuvens = QtWidgets.QCheckBox("Descartar cenas com nuvens/sombra")
        self.filtrar_nuvens.setChecked(True)
        self.manter_scl = QtWidgets.QCheckBox("Manter arquivo SCL")
        self.manter_scl.setChecked(True)
        self.gerar_rgb = QtWidgets.QCheckBox("Gerar preview RGB")
        self.gerar_rgb.setChecked(True)
        self.nuvem_max_pct = self._inteiro(0, 100, 40, "%")
        self.tamanho_max_px = self._inteiro(100, 5000, 1600, " px")
        self.qualidade_jpeg = self._inteiro(1, 100, 92, "%")
        self.gerar_dataset = QtWidgets.QCheckBox("Gerar dataset após o download")
        self.patch_tamanho_px = QtWidgets.QComboBox()
        self.patch_tamanho_px.addItem("512 px", 512)
        self.patch_tamanho_px.addItem("256 px", 256)
        self.patch_stride_px = self._inteiro(1, 4096, 512, " px")
        self.patch_nuvem_max_pct = self._inteiro(0, 100, 10, "%")
        self.dados_validos_min_pct = self._inteiro(0, 100, 90, "%")
        self.dataset_rgb_minimo = self._inteiro(-10000, 10000, 0)
        self.dataset_rgb_maximo = self._inteiro(-10000, 30000, 2000)

        self.modelo_ia = QtWidgets.QLineEdit("analise/models/best.pt")
        self.modelo_sha256 = QtWidgets.QLineEdit()
        self.modelo_sha256.setPlaceholderText("Opcional: SHA-256 do modelo aprovado")
        self.confianca_minima = self._inteiro(0, 100, 25, "%")
        self.iou_maximo = self._inteiro(0, 100, 45, "%")
        self.tamanho_inferencia_px = self._inteiro(128, 2048, 640, " px")
        self.pasta_analises = QtWidgets.QLineEdit("data/analises")
        self.historico_analises = QtWidgets.QLineEdit("data/historico-analises.sqlite3")
        self.gerar_relatorio = QtWidgets.QCheckBox("Gerar relatório PDF automaticamente")
        self.gerar_relatorio.setChecked(True)
        self.max_imagens_analise = self._inteiro(0, 10000, 1000)
        self.max_imagens_analise.setSpecialValueText("Todas")

        self.pasta_download = QtWidgets.QLineEdit("data/sentinel2")
        self.catalogo = QtWidgets.QLineEdit("catalogo/catalogo_imagens.csv")
        self.output_path = QtWidgets.QLineEdit(str(DEFAULT_CONFIG))
        self.timeout_segundos = self._inteiro(30, 600, 120, " s")
        self.chunk_mb = self._inteiro(1, 50, 1, " MB")
        self.max_itens_teste = self._inteiro(0, 1000, 5)
        self.max_candidatos_teste = self._inteiro(1, 1000, 40)

        self.oauth_json = QtWidgets.QLineEdit("${GOOGLE_OAUTH_JSON:-}")
        self.oauth_json.setPlaceholderText("Selecione o client_secret_*.json")
        self.token_json = QtWidgets.QLineEdit("${GOOGLE_TOKEN_JSON:-config/google-token.json}")
        self.pasta_remota = QtWidgets.QLineEdit("sentinel2-mt")
        self.pasta_id = QtWidgets.QLineEdit("${GOOGLE_PASTA_ID:-root}")
        self.tamanho_lote = self._inteiro(1, 1000, 100, " arquivos")

        self.operacao = QtWidgets.QComboBox()
        self.operacao.addItem("Catalogar sem baixar", "catalogar")
        self.operacao.addItem("Baixar imagens aprovadas", "baixar")
        self.operacao.addItem("Gerar dataset das cenas locais", "dataset")
        self.operacao.addItem("Analisar região com IA", "analisar")
        self.operacao.addItem("Sincronizar com Google Drive", "sincronizar")
        self.max_execucao = self._inteiro(0, 1000, 5)
        self.max_execucao.setSpecialValueText("Todas")

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setObjectName("log")
        self.log.setReadOnly(True)
        self.log.document().setMaximumBlockCount(4000)
        self.yaml_preview = QtWidgets.QPlainTextEdit()
        self.yaml_preview.setReadOnly(True)
        self.perfis = QtWidgets.QTreeWidget()
        self.perfis.setHeaderLabel("Presets por região / UF")
        self.perfis.setAlternatingRowColors(True)
        self.perfis.setExpandsOnDoubleClick(True)
        self.imagem_preview = QtWidgets.QLabel("Nenhum preview selecionado")
        self.imagem_preview.setObjectName("previewImage")
        self.imagem_preview.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.imagem_preview.setMinimumHeight(210)
        self.analise_info = QtWidgets.QLabel("Execute 'Analisar região com IA' para ver os resultados.")
        self.analise_info.setWordWrap(True)
        self.analise_tabela = QtWidgets.QTableWidget(0, 5)
        self.analise_tabela.setHorizontalHeaderLabels(
            ["Classe", "Detecções", "Confiança média", "Mínima", "Máxima"]
        )
        self.analise_tabela.horizontalHeader().setStretchLastSection(True)
        self.analise_original = QtWidgets.QLabel("Imagem RGB original")
        self.analise_anotada = QtWidgets.QLabel("Imagem com detecções")
        for imagem in (self.analise_original, self.analise_anotada):
            imagem.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            imagem.setMinimumHeight(240)
        self.historico_tabela = QtWidgets.QTableWidget(0, 5)
        self.historico_tabela.setHorizontalHeaderLabels(
            ["Data", "Região", "Período", "Detecções", "Relatório"]
        )
        self.historico_tabela.horizontalHeader().setStretchLastSection(True)

    @staticmethod
    def _inteiro(minimo: int, maximo: int, valor: int, sufixo: str = "") -> ControleNumerico:
        return ControleNumerico(minimo, maximo, valor, sufixo)

    @staticmethod
    def _coordenada(minimo: float, maximo: float, valor: float) -> ControleNumerico:
        # Mantém a precisão de seis casas já suportada pela configuração e pelo mapa.
        return ControleNumerico(minimo, maximo, valor, "°", casas_decimais=6)

    def _montar_janela(self) -> None:
        raiz = QtWidgets.QWidget()
        raiz.setObjectName("root")
        if FUNDO_GUI.is_file():
            caminho_fundo = FUNDO_GUI.as_posix().replace("'", "\\'")
            raiz.setStyleSheet(
                f"QWidget#root {{ border-image: url('{caminho_fundo}') 0 0 0 0 stretch stretch; }}"
            )
        estrutura = QtWidgets.QHBoxLayout(raiz)
        estrutura.setContentsMargins(0, 0, 0, 0)
        estrutura.setSpacing(0)
        estrutura.addWidget(self._criar_sidebar())

        area = QtWidgets.QWidget()
        layout_area = QtWidgets.QVBoxLayout(area)
        layout_area.setContentsMargins(28, 20, 28, 18)
        layout_area.setSpacing(16)
        layout_area.addLayout(self._criar_cabecalho())

        self.stack = QtWidgets.QStackedWidget()
        self.stack.addWidget(self._pagina_inicio())
        self.stack.addWidget(self._pagina_visao_geral())
        self.stack.addWidget(self._pagina_area())
        self.stack.addWidget(self._pagina_download())
        self.stack.addWidget(self._pagina_dados())
        self.stack.addWidget(self._pagina_drive())
        self.stack.addWidget(self._pagina_analise())
        self.stack.addWidget(self._pagina_historico())
        self.stack.addWidget(self._pagina_laboratorio())
        self.stack.addWidget(self._pagina_config())
        self.stack.addWidget(self._pagina_sobre())
        self.stack.addWidget(self._pagina_ajuda())
        layout_area.addWidget(self.stack, 1)
        layout_area.addWidget(self._barra_acoes())
        estrutura.addWidget(area, 1)
        self.setCentralWidget(raiz)

    def _criar_sidebar(self) -> QtWidgets.QFrame:
        painel = QtWidgets.QFrame()
        painel.setObjectName("sidebar")
        painel.setFixedWidth(230)
        layout = QtWidgets.QVBoxLayout(painel)
        layout.setContentsMargins(18, 22, 18, 20)
        layout.setSpacing(8)

        marca = QtWidgets.QHBoxLayout()
        simbolo = QtWidgets.QLabel("S2")
        simbolo.setObjectName("brandMark")
        simbolo.setFixedSize(40, 40)
        simbolo.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        textos = QtWidgets.QVBoxLayout()
        titulo = QtWidgets.QLabel("Sentinel-2 MT")
        titulo.setObjectName("brandTitle")
        subtitulo = QtWidgets.QLabel("Downloader & Sync")
        subtitulo.setObjectName("brandSub")
        textos.addWidget(titulo)
        textos.addWidget(subtitulo)
        marca.addWidget(simbolo)
        marca.addLayout(textos)
        layout.addLayout(marca)
        layout.addSpacing(24)

        self.grupo_navegacao = QtWidgets.QButtonGroup(self)
        self.grupo_navegacao.setExclusive(True)
        self.botoes_navegacao: list[QtWidgets.QPushButton] = []
        for indice, (nome, _) in enumerate(self.PAGINAS):
            item = botao(nome, "navButton")
            item.setCheckable(True)
            item.clicked.connect(lambda _=False, i=indice: self._navegar(i))
            self.grupo_navegacao.addButton(item, indice)
            self.botoes_navegacao.append(item)
            layout.addWidget(item)
        self.botoes_navegacao[0].setChecked(True)
        layout.addStretch()
        versao = QtWidgets.QLabel("API STAC INPE\nGoogle Drive OAuth")
        versao.setObjectName("brandSub")
        layout.addWidget(versao)
        return painel

    def _criar_cabecalho(self) -> QtWidgets.QHBoxLayout:
        layout = QtWidgets.QHBoxLayout()
        textos = QtWidgets.QVBoxLayout()
        self.titulo_pagina = QtWidgets.QLabel(self.PAGINAS[0][0])
        self.titulo_pagina.setObjectName("pageTitle")
        self.subtitulo_pagina = QtWidgets.QLabel(self.PAGINAS[0][1])
        self.subtitulo_pagina.setObjectName("pageSubtitle")
        textos.addWidget(self.titulo_pagina)
        textos.addWidget(self.subtitulo_pagina)
        layout.addLayout(textos)
        layout.addStretch()
        self.status = QtWidgets.QLabel("Pronto")
        self.status.setObjectName("statusChip")
        layout.addWidget(self.status)
        return layout

    INDICADORES_PAINEL = (
        ("regioes", "Regiões salvas"),
        ("cenas_catalogadas", "Imagens encontradas"),
        ("cenas_baixadas", "Imagens baixadas"),
        ("patches", "Recortes prontos para análise"),
        ("ultima_analise", "Última análise"),
        ("modelos", "Modelos instalados"),
        ("disco", "Espaço ocupado"),
        ("tarefa", "Tarefa atual"),
    )

    def _pagina_inicio(self) -> QtWidgets.QWidget:
        pagina = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(pagina)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        acoes = Cartao(
            "O que você quer fazer?",
            "Escolha uma etapa. Nada é baixado ou analisado sem você confirmar na tela seguinte.",
        )
        linha_acoes = QtWidgets.QHBoxLayout()
        self.btn_inicio_regiao = botao("Selecionar região", "primaryButton")
        self.btn_inicio_buscar = botao("Buscar imagens")
        self.btn_inicio_analisar = botao("Analisar região")
        self.btn_inicio_resultados = botao("Consultar resultados")
        self.btn_inicio_regiao.clicked.connect(
            lambda: self._navegar(self._indice_pagina("Área e período"))
        )
        self.btn_inicio_buscar.clicked.connect(
            lambda: self._navegar(self._indice_pagina("Baixar imagens"))
        )
        self.btn_inicio_analisar.clicked.connect(lambda: self._preparar_operacao("analisar"))
        self.btn_inicio_resultados.clicked.connect(
            lambda: self._navegar(self._indice_pagina("Análise da região"))
        )
        for item in (
            self.btn_inicio_regiao,
            self.btn_inicio_buscar,
            self.btn_inicio_analisar,
            self.btn_inicio_resultados,
        ):
            item.setMinimumHeight(44)
            linha_acoes.addWidget(item)
        acoes.layout_principal.addLayout(linha_acoes)
        layout.addWidget(acoes)

        indicadores = QtWidgets.QGridLayout()
        indicadores.setHorizontalSpacing(14)
        indicadores.setVerticalSpacing(14)
        self.painel_valores: dict[str, QtWidgets.QLabel] = {}
        self.painel_detalhes: dict[str, QtWidgets.QLabel] = {}
        for posicao, (chave, titulo) in enumerate(self.INDICADORES_PAINEL):
            cartao = Cartao(titulo)
            valor = QtWidgets.QLabel("…")
            valor.setObjectName("statValue")
            valor.setAccessibleName(titulo)
            detalhe = QtWidgets.QLabel("")
            detalhe.setObjectName("cardHelp")
            detalhe.setWordWrap(True)
            cartao.layout_principal.addWidget(valor)
            cartao.layout_principal.addWidget(detalhe)
            cartao.layout_principal.addStretch()
            self.painel_valores[chave] = valor
            self.painel_detalhes[chave] = detalhe
            indicadores.addWidget(cartao, posicao // 4, posicao % 4)
        layout.addLayout(indicadores)

        orientacao = Cartao("Próximo passo recomendado")
        self.painel_proxima_acao = QtWidgets.QLabel("Calculando o resumo local…")
        self.painel_proxima_acao.setWordWrap(True)
        self.painel_avisos = QtWidgets.QLabel("")
        self.painel_avisos.setObjectName("cardHelp")
        self.painel_avisos.setWordWrap(True)
        self.btn_atualizar_painel = botao("Atualizar resumo")
        self.btn_atualizar_painel.clicked.connect(self._atualizar_painel)
        orientacao.layout_principal.addWidget(self.painel_proxima_acao)
        orientacao.layout_principal.addWidget(self.painel_avisos)
        orientacao.layout_principal.addWidget(
            self.btn_atualizar_painel, 0, QtCore.Qt.AlignmentFlag.AlignLeft
        )
        layout.addWidget(orientacao)
        layout.addStretch()
        return pagina

    def _preparar_operacao(self, operacao: str) -> None:
        """Seleciona a operação e leva o usuário à tela onde ele confirma a execução."""
        indice = self.operacao.findData(operacao)
        if indice >= 0:
            self.operacao.setCurrentIndex(indice)
        self._navegar(self._indice_pagina("Operações"))

    def _config_painel(self) -> ConfiguracaoProjeto | None:
        # Usa somente o arquivo escolhido na GUI, sem recorrer a outro YAML.
        caminho = Path(self.output_path.text().strip() or DEFAULT_CONFIG).expanduser()
        if not caminho.is_file():
            return None
        return ConfiguracaoProjeto.carregar(caminho, raiz=ROOT)

    def _atualizar_painel(self, sincrono: bool = False) -> None:
        """Recalcula o resumo em segundo plano para não bloquear a interface."""
        if self._painel_em_coleta:
            return
        try:
            config = self._config_painel()
            regioes = len(self.store.listar())
        except (OSError, TypeError, ValueError, KeyError, yaml.YAMLError) as erro:
            self.painel_proxima_acao.setText(f"Não foi possível ler a configuração: {erro}")
            return
        if config is None:
            self.painel_proxima_acao.setText(
                "Nenhuma configuração encontrada. Selecione uma região e salve a configuração."
            )
            return
        self._painel_em_coleta = True
        self.btn_atualizar_painel.setEnabled(False)
        ponte = self._ponte_painel

        def coletar() -> None:
            try:
                resultado: object = coletar_resumo_painel(config, regioes_salvas=regioes)
            except Exception as erro:  # o painel nunca deve derrubar a GUI
                resultado = erro
            ponte.concluido.emit(resultado)

        if sincrono:
            coletar()
        else:
            self._pool_painel.start(coletar)

    def _aplicar_resumo_painel(self, resultado: object) -> None:
        self._painel_em_coleta = False
        self.btn_atualizar_painel.setEnabled(True)
        if not isinstance(resultado, ResumoPainel):
            self.painel_proxima_acao.setText(f"Não foi possível calcular o resumo: {resultado}")
            return
        self._ultimo_resumo_painel = resultado
        valores = self.painel_valores
        detalhes = self.painel_detalhes
        valores["regioes"].setText(str(resultado.regioes_salvas))
        detalhes["regioes"].setText("Perfis locais de área e período.")
        valores["cenas_catalogadas"].setText(str(resultado.cenas_catalogadas))
        detalhes["cenas_catalogadas"].setText("Cenas registradas no catálogo de busca.")
        valores["cenas_baixadas"].setText(str(resultado.cenas_baixadas))
        detalhes["cenas_baixadas"].setText(
            f"{resultado.downloads_parciais} download(s) incompleto(s) a retomar; "
            f"{resultado.parciais_legados} temporário(s) antigo(s)."
            if resultado.downloads_parciais or resultado.parciais_legados
            else "Cenas com bandas salvas no computador."
        )
        valores["patches"].setText(str(resultado.patches_aprovados_catalogo))
        detalhes["patches"].setText(
            f"{resultado.patches_em_disco} recorte(s) no disco; "
            + (
                "catálogo encontrado."
                if resultado.catalogo_patches_existe
                else "catálogo de recortes ausente."
            )
        )
        ultima = resultado.ultima_analise
        if ultima is None:
            valores["ultima_analise"].setText("Nenhuma")
            detalhes["ultima_analise"].setText("Ainda não há análises no histórico local.")
        else:
            valores["ultima_analise"].setText(ultima.criado_em[:10] or "—")
            detalhes["ultima_analise"].setText(
                f"{ultima.regiao or 'Região sem nome'} • {ultima.total_deteccoes} detecção(ões)"
                f" • modelo {ultima.modelo_versao or 'não informado'}"
            )
        conferidos = sum(1 for modelo in resultado.modelos if modelo.integridade_conferida)
        valores["modelos"].setText(str(len(resultado.modelos)))
        ativo = next((modelo for modelo in resultado.modelos if modelo.ativo), None)
        if ativo is None:
            detalhes["modelos"].setText("Nenhum modelo ativo encontrado.")
        else:
            tarefa = {"detection": "detecção por caixas", "classify": "classificação"}.get(
                ativo.tarefa, ativo.tarefa or "tarefa não informada"
            )
            integridade = {
                "conferida": "integridade SHA-256 conferida",
                "divergente": "SHA-256 DIVERGENTE do manifesto",
                "sem_manifesto": "sem manifesto SHA-256",
            }[ativo.integridade]
            detalhes["modelos"].setText(
                f"Ativo: {ativo.versao or ativo.arquivo} ({tarefa}); {integridade}. "
                f"{conferidos} com integridade conferida. Integridade não equivale a "
                "validação científica."
            )
        valores["disco"].setText(formatar_bytes(resultado.bytes_total))
        partes = resultado.bytes_por_area
        detalhes["disco"].setText(
            f"Imagens {formatar_bytes(partes.get('imagens', 0))} • "
            f"recortes {formatar_bytes(partes.get('dataset', 0))} • "
            f"análises {formatar_bytes(partes.get('analises', 0))}"
        )
        self._atualizar_tarefa_painel()
        self.painel_proxima_acao.setText(resultado.proxima_acao)
        self.painel_avisos.setText(
            "\n".join(f"⚠ {aviso}" for aviso in resultado.avisos)
            if resultado.avisos
            else "Nenhum problema encontrado nos arquivos locais."
        )

    def _atualizar_tarefa_painel(self) -> None:
        if not hasattr(self, "painel_valores"):
            return
        executando = self.processo.state() != QtCore.QProcess.ProcessState.NotRunning
        self.painel_valores["tarefa"].setText("Em andamento" if executando else "Nenhuma")
        self.painel_detalhes["tarefa"].setText(
            self.operacao.currentText() if executando else f"Último estado: {self.status.text()}"
        )

    def _pagina_visao_geral(self) -> QtWidgets.QWidget:
        pagina = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(pagina)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        topo = QtWidgets.QHBoxLayout()
        operacao = Cartao("Nova operação", "A configuração é salva automaticamente antes da execução.")
        form = QtWidgets.QFormLayout()
        form.addRow(rotulo_com_ajuda("Operação", "Define o que será executado: consultar cenas, baixar arquivos, gerar patches, analisar com IA ou sincronizar com o Drive."), self.operacao)
        form.addRow(
            rotulo_com_ajuda(
                "Máximo de cenas", "Limita quantas cenas a operação processará. Se estiver em 'Todas', todas as cenas que passarem pelos filtros serão consideradas."
            ),
            self.max_execucao,
        )
        self.resumo_operacao = QtWidgets.QLabel()
        self.resumo_operacao.setWordWrap(True)
        self.resumo_operacao.setObjectName("cardHelp")
        operacao.layout_principal.addLayout(form)
        operacao.layout_principal.addWidget(self.resumo_operacao)
        topo.addWidget(operacao, 2)

        destinos = Cartao("Arquivos locais", "Abra rapidamente a pasta de imagens ou um preview RGB.")
        linha = QtWidgets.QHBoxLayout()
        abrir_pasta = botao("Abrir pasta de imagens")
        abrir_pasta.clicked.connect(self._abrir_pasta_imagens)
        abrir_preview = botao("Visualizar preview")
        abrir_preview.clicked.connect(self._abrir_imagem_preview)
        linha.addWidget(abrir_pasta)
        linha.addWidget(abrir_preview)
        destinos.layout_principal.addLayout(linha)
        topo.addWidget(destinos, 2)
        layout.addLayout(topo)

        divisor = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        log_card = Cartao("Saída da operação", "Acompanhe downloads, lotes e autenticação em tempo real.")
        log_card.layout_principal.addWidget(self.log, 1)
        divisor.addWidget(log_card)
        preview_card = Cartao("Preview da cena")
        preview_card.layout_principal.addWidget(self.imagem_preview, 1)
        divisor.addWidget(preview_card)
        divisor.setSizes([760, 390])
        layout.addWidget(divisor, 1)
        return pagina

    def _pagina_area(self) -> QtWidgets.QWidget:
        pagina = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(pagina)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        bbox_inicial = [self.oeste.value(), self.sul.value(), self.leste.value(), self.norte.value()]
        self.mapa = MapaWidget(bbox_inicial, self)
        mapa_card = Cartao(
            "Área de interesse",
            "Pesquise um município, desenhe no mapa ou importe um limite. "
            "A busca de imagens usa o retângulo mostrado em verde.",
        )
        busca = QtWidgets.QHBoxLayout()
        self.busca_area = QtWidgets.QLineEdit()
        self.busca_area.setPlaceholderText("Pesquisar município ou região favorita (ex.: Sorriso)")
        self.busca_area.setClearButtonEnabled(True)
        self.busca_area.textChanged.connect(self._pesquisar_area)
        busca.addWidget(self.busca_area, 1)
        self.btn_desenhar_area = botao("Desenhar área")
        self.btn_desenhar_area.setCheckable(True)
        self.btn_desenhar_area.toggled.connect(self._alternar_desenho)
        self.btn_importar_area = botao("Importar limite…")
        self.btn_importar_area.setToolTip("GeoJSON (.geojson, .json) ou Shapefile (.shp com .prj)")
        self.btn_importar_area.clicked.connect(self._importar_limite)
        busca.addWidget(self.btn_desenhar_area)
        busca.addWidget(self.btn_importar_area)
        mapa_card.layout_principal.addLayout(busca)
        self.resultados_area = QtWidgets.QListWidget()
        self.resultados_area.setMaximumHeight(150)
        self.resultados_area.hide()
        self.resultados_area.itemActivated.connect(self._usar_resultado_area)
        self.resultados_area.itemClicked.connect(self._usar_resultado_area)
        mapa_card.layout_principal.addWidget(self.resultados_area)
        mapa_card.layout_principal.addWidget(self.mapa, 1)
        self.area_mensagem = QtWidgets.QLabel("")
        self.area_mensagem.setObjectName("cardHelp")
        self.area_mensagem.setWordWrap(True)
        mapa_card.layout_principal.addWidget(self.area_mensagem)
        aplicar = botao("Aplicar seleção do mapa", "primaryButton")
        aplicar.clicked.connect(self.mapa.capturar_bbox)
        mapa_card.layout_principal.addWidget(aplicar)
        layout.addWidget(mapa_card, 3)

        detalhes = Cartao("Região e período", "As coordenadas usam a ordem oeste, sul, leste, norte.")
        form = QtWidgets.QFormLayout()
        form.addRow(rotulo_com_ajuda("Nome", "Nome exibido nos perfis, no histórico e nos resultados desta região."), self.nome_regiao)
        form.addRow(rotulo_com_ajuda("UF", "Sigla de duas letras do estado. É usada para agrupar os perfis locais."), self.uf)
        form.addRow(rotulo_com_ajuda("Data inicial", "Primeiro dia incluído na busca de cenas. O formato salvo é AAAA-MM-DD."), self.inicio)
        form.addRow(rotulo_com_ajuda("Data final", "Último dia incluído na busca de cenas. O formato salvo é AAAA-MM-DD."), self.fim)
        form.addRow(rotulo_com_ajuda("Oeste", "Longitude do lado esquerdo da área. A caixa final é salva como oeste, sul, leste, norte."), self.oeste)
        form.addRow(rotulo_com_ajuda("Sul", "Latitude do lado inferior da área. Use valores negativos para latitudes ao sul do Equador."), self.sul)
        form.addRow(rotulo_com_ajuda("Leste", "Longitude do lado direito da área. Deve ser maior que Oeste."), self.leste)
        form.addRow(rotulo_com_ajuda("Norte", "Latitude do lado superior da área. Deve ser maior que Sul."), self.norte)
        detalhes.layout_principal.addLayout(form)
        salvar_perfil = botao("Salvar como região favorita")
        salvar_perfil.clicked.connect(self._salvar_perfil)
        detalhes.layout_principal.addWidget(salvar_perfil)
        rotulo_favoritas = QtWidgets.QLabel("Regiões favoritas")
        rotulo_favoritas.setObjectName("cardTitle")
        detalhes.layout_principal.addWidget(rotulo_favoritas)
        self.favoritos_area = QtWidgets.QListWidget()
        self.favoritos_area.setToolTip("Clique duas vezes para abrir a região.")
        self.favoritos_area.itemDoubleClicked.connect(self._abrir_favorita)
        detalhes.layout_principal.addWidget(self.favoritos_area, 1)
        layout.addWidget(detalhes, 1)
        return pagina

    # ------------------------------------------------------------ Área de interesse

    def _municipios(self) -> list[Municipio]:
        if not hasattr(self, "_cache_municipios"):
            try:
                self._cache_municipios = carregar_municipios(MUNICIPIOS_IBGE)
            except (OSError, ValueError) as erro:
                self.statusBar().showMessage(f"Lista de municípios inválida: {erro}", 6000)
                self._cache_municipios = []
        return self._cache_municipios

    def _pesquisar_area(self, termo: str) -> None:
        self.resultados_area.clear()
        alvo = normalizar_texto(termo)
        if len(alvo) < 2:
            self.resultados_area.hide()
            return
        for perfil in self.store.listar():
            if alvo in normalizar_texto(str(perfil["nome_regiao"])):
                item = QtWidgets.QListWidgetItem(
                    f"★ {perfil['nome_regiao']} ({perfil['uf'] or '—'}) — região favorita"
                )
                item.setData(QtCore.Qt.ItemDataRole.UserRole, ("favorita", int(perfil["id"])))
                self.resultados_area.addItem(item)
        municipios = self._municipios()
        for municipio in buscar_municipios(municipios, termo, limite=10):
            item = QtWidgets.QListWidgetItem(f"{municipio.nome} ({municipio.uf}) — município")
            item.setData(QtCore.Qt.ItemDataRole.UserRole, ("municipio", municipio))
            self.resultados_area.addItem(item)
        if self.resultados_area.count() == 0:
            texto = (
                "Nenhum resultado encontrado."
                if municipios
                else "Lista de municípios não instalada; gere-a com tools/gerar_municipios_ibge.py."
            )
            vazio = QtWidgets.QListWidgetItem(texto)
            vazio.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
            self.resultados_area.addItem(vazio)
        self.resultados_area.show()

    def _usar_resultado_area(self, item: QtWidgets.QListWidgetItem) -> None:
        dados = item.data(QtCore.Qt.ItemDataRole.UserRole)
        if not dados:
            return
        tipo, valor = dados
        if tipo == "favorita":
            self._abrir_favorita_por_id(int(valor))
        elif tipo == "municipio":
            self._aplicar_area(
                valor.nome,
                valor.uf,
                list(valor.bbox),
                "Município do IBGE (retângulo aproximado). Ajuste a área se precisar.",
            )
        self.resultados_area.hide()

    def _aplicar_area(self, nome: str, uf: str, bbox: list[float], mensagem: str) -> None:
        bbox = normalizar_bbox(bbox)
        self.nome_regiao.setText(nome)
        if uf:
            self.uf.setText(uf[:2].upper())
        self.oeste.setValue(bbox[0])
        self.sul.setValue(bbox[1])
        self.leste.setValue(bbox[2])
        self.norte.setValue(bbox[3])
        self.mapa.exibir_bbox(bbox)
        self.area_mensagem.setText(mensagem)
        self._atualizar_preview_yaml()

    def _alternar_desenho(self, ativo: bool) -> None:
        if ativo and not self.mapa.ativar_desenho(True):
            self.btn_desenhar_area.setChecked(False)
            self.area_mensagem.setText(
                "O mapa ainda não está pronto. Aguarde o carregamento ou edite as coordenadas."
            )
            return
        if not ativo:
            self.mapa.ativar_desenho(False)
            return
        self.area_mensagem.setText("Clique e arraste no mapa para desenhar a área.")

    def _desenho_concluido(self) -> None:
        self.btn_desenhar_area.blockSignals(True)
        self.btn_desenhar_area.setChecked(False)
        self.btn_desenhar_area.blockSignals(False)

    def _importar_limite(self) -> None:
        caminho, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Importar limite da área",
            str(Path.home()),
            "Limites geográficos (*.geojson *.json *.shp)",
        )
        if caminho:
            self._importar_limite_de(Path(caminho))

    def _importar_limite_de(self, caminho: Path) -> AreaImportada | None:
        try:
            area = importar_area(caminho)
        except (OSError, ValueError) as erro:
            QtWidgets.QMessageBox.warning(self, "Não foi possível importar o limite", str(erro))
            return None
        mensagem = f"Limite importado de {area.origem} ({area.crs_origem})."
        if area.avisos:
            mensagem += " " + " ".join(area.avisos)
        self._aplicar_area(area.nome, self.uf.text(), list(area.bbox), mensagem)
        return area

    def _recarregar_favoritas(self) -> None:
        if not hasattr(self, "favoritos_area"):
            return
        self.favoritos_area.clear()
        perfis = self.store.listar()
        for perfil in perfis:
            item = QtWidgets.QListWidgetItem(f"{perfil['nome_regiao']} ({perfil['uf'] or '—'})")
            item.setData(QtCore.Qt.ItemDataRole.UserRole, int(perfil["id"]))
            self.favoritos_area.addItem(item)
        if not perfis:
            vazio = QtWidgets.QListWidgetItem("Nenhuma região salva ainda.")
            vazio.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
            self.favoritos_area.addItem(vazio)

    def _abrir_favorita(self, item: QtWidgets.QListWidgetItem) -> None:
        perfil_id = item.data(QtCore.Qt.ItemDataRole.UserRole)
        if perfil_id is not None:
            self._abrir_favorita_por_id(int(perfil_id))

    def _abrir_favorita_por_id(self, perfil_id: int) -> None:
        perfil = self.store.carregar(perfil_id)
        if perfil is None:
            return
        self._aplicar_dados(json.loads(perfil["payload"]))
        self.area_mensagem.setText(f"Região favorita '{perfil['nome_regiao']}' aberta.")

    PASSOS_ASSISTENTE = ("1. Região e período", "2. Escolher cenas", "3. Baixar")

    def _pagina_download(self) -> QtWidgets.QWidget:
        pagina = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(pagina)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        passos = QtWidgets.QHBoxLayout()
        self.assist_rotulos_passos: list[QtWidgets.QLabel] = []
        for nome in self.PASSOS_ASSISTENTE:
            rotulo = QtWidgets.QLabel(nome)
            rotulo.setObjectName("stepLabel")
            rotulo.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
            passos.addWidget(rotulo, 1)
            self.assist_rotulos_passos.append(rotulo)
        layout.addLayout(passos)

        self.assist_stack = QtWidgets.QStackedWidget()
        self.assist_stack.addWidget(self._assistente_passo_periodo())
        self.assist_stack.addWidget(self._assistente_passo_cenas())
        self.assist_stack.addWidget(self._assistente_passo_baixar())
        layout.addWidget(self.assist_stack, 1)

        self._assist_timer_tabela = QtCore.QTimer(self)
        self._assist_timer_tabela.setSingleShot(True)
        self._assist_timer_tabela.setInterval(200)
        self._assist_timer_tabela.timeout.connect(self._assistente_renderizar_tabela)
        self._assistente_ir_para(0)
        return pagina

    def _assistente_passo_periodo(self) -> QtWidgets.QWidget:
        cartao = Cartao(
            "Região e período",
            "Confira a região e escolha o período. Nada é baixado nesta etapa.",
        )
        self.assist_regiao = QtWidgets.QLabel()
        self.assist_regiao.setWordWrap(True)
        alterar = botao("Alterar região no mapa")
        alterar.clicked.connect(lambda: self._navegar(self._indice_pagina("Área e período")))
        linha_regiao = QtWidgets.QHBoxLayout()
        linha_regiao.addWidget(self.assist_regiao, 1)
        linha_regiao.addWidget(alterar)
        cartao.layout_principal.addLayout(linha_regiao)

        self.assist_preset = QtWidgets.QComboBox()
        for texto, chave in (
            ("Escolher datas", ""),
            ("Últimos 30 dias", "30"),
            ("Últimos 90 dias", "90"),
            ("Safra de verão (setembro a abril)", "safra"),
        ):
            self.assist_preset.addItem(texto, chave)
        self.assist_preset.currentIndexChanged.connect(self._assistente_aplicar_preset)
        self.assist_inicio = QtWidgets.QDateEdit(self.inicio.date())
        self.assist_fim = QtWidgets.QDateEdit(self.fim.date())
        for campo, original in ((self.assist_inicio, self.inicio), (self.assist_fim, self.fim)):
            campo.setCalendarPopup(True)
            campo.setDisplayFormat("dd/MM/yyyy")
            # Mantém as datas do assistente e da página de área sempre iguais.
            campo.dateChanged.connect(original.setDate)
            original.dateChanged.connect(campo.setDate)
            original.dateChanged.connect(lambda _data: self._assistente_atualizar_regiao())
        form = QtWidgets.QFormLayout()
        form.addRow("Período rápido", self.assist_preset)
        form.addRow("De", self.assist_inicio)
        form.addRow("Até", self.assist_fim)
        cartao.layout_principal.addLayout(form)
        cartao.layout_principal.addStretch()

        self.assist_buscar = botao("Buscar imagens disponíveis", "primaryButton")
        self.assist_buscar.clicked.connect(self._assistente_buscar)
        cartao.layout_principal.addWidget(self.assist_buscar, 0, QtCore.Qt.AlignmentFlag.AlignRight)
        self._assistente_atualizar_regiao()
        return cartao

    def _assistente_passo_cenas(self) -> QtWidgets.QWidget:
        cartao = Cartao(
            "Escolher cenas",
            "Marque as cenas que deseja baixar. Clique em uma linha para visualizá-la.",
        )
        filtros = QtWidgets.QHBoxLayout()
        self.assist_nuvem_max = QtWidgets.QSpinBox()
        self.assist_nuvem_max.setRange(0, 100)
        self.assist_nuvem_max.setValue(100)
        self.assist_nuvem_max.setSuffix(" %")
        self.assist_nuvem_max.valueChanged.connect(self._assistente_renderizar_tabela)
        self.assist_ocultar_baixadas = QtWidgets.QCheckBox("Ocultar cenas já baixadas")
        self.assist_ocultar_baixadas.toggled.connect(self._assistente_renderizar_tabela)
        self.assist_contagem = QtWidgets.QLabel("")
        self.assist_contagem.setObjectName("cardHelp")
        filtros.addWidget(QtWidgets.QLabel("Nuvens até"))
        filtros.addWidget(self.assist_nuvem_max)
        filtros.addWidget(self.assist_ocultar_baixadas)
        filtros.addStretch()
        filtros.addWidget(self.assist_contagem)
        cartao.layout_principal.addLayout(filtros)

        divisor = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.assist_tabela = QtWidgets.QTableWidget(0, 6)
        self.assist_tabela.setHorizontalHeaderLabels(
            ["Baixar", "Data", "Cena", "Nuvens*", "Tamanho", "Situação"]
        )
        self.assist_tabela.horizontalHeader().setStretchLastSection(True)
        self.assist_tabela.setSelectionBehavior(
            QtWidgets.QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.assist_tabela.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.assist_tabela.itemChanged.connect(self._assistente_item_alterado)
        self.assist_tabela.currentCellChanged.connect(
            lambda linha, *_: self._assistente_visualizar(linha)
        )
        divisor.addWidget(self.assist_tabela)
        self.assist_preview = QtWidgets.QLabel("Selecione uma cena para visualizar")
        self.assist_preview.setObjectName("previewImage")
        self.assist_preview.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.assist_preview.setMinimumWidth(260)
        self.assist_preview.setWordWrap(True)
        divisor.addWidget(self.assist_preview)
        divisor.setSizes([700, 320])
        cartao.layout_principal.addWidget(divisor, 1)

        nota = QtWidgets.QLabel(
            "* Nuvens segundo os metadados do catálogo, quando informadas. Durante o "
            "download, a camada de qualidade (SCL) é avaliada e pode descartar a cena."
        )
        nota.setObjectName("cardHelp")
        nota.setWordWrap(True)
        cartao.layout_principal.addWidget(nota)

        acoes = QtWidgets.QHBoxLayout()
        voltar = botao("Voltar")
        voltar.clicked.connect(lambda: self._assistente_ir_para(0))
        todas = botao("Marcar todas")
        todas.clicked.connect(lambda: self._assistente_marcar_todas(True))
        nenhuma = botao("Desmarcar todas")
        nenhuma.clicked.connect(lambda: self._assistente_marcar_todas(False))
        avancadas = botao("Opções avançadas")
        avancadas.setToolTip("Bandas, filtros de qualidade e geração de dataset")
        avancadas.clicked.connect(lambda: self._navegar(self._indice_pagina("Dados e qualidade")))
        self.assist_revisar = botao("Revisar download", "primaryButton")
        self.assist_revisar.clicked.connect(self._assistente_revisar)
        for item in (voltar, todas, nenhuma, avancadas):
            acoes.addWidget(item)
        acoes.addStretch()
        acoes.addWidget(self.assist_revisar)
        cartao.layout_principal.addLayout(acoes)
        return cartao

    def _assistente_passo_baixar(self) -> QtWidgets.QWidget:
        cartao = Cartao(
            "Baixar",
            "Revise e inicie. Você pode cancelar a qualquer momento; o que foi "
            "interrompido é retomado na próxima vez.",
        )
        self.assist_resumo = QtWidgets.QLabel("")
        self.assist_resumo.setWordWrap(True)
        self.assist_estimativa = QtWidgets.QLabel("")
        self.assist_estimativa.setObjectName("cardHelp")
        self.assist_estimativa.setWordWrap(True)
        cartao.layout_principal.addWidget(self.assist_resumo)
        cartao.layout_principal.addWidget(self.assist_estimativa)

        cartao.layout_principal.addWidget(QtWidgets.QLabel("Progresso geral (cenas)"))
        self.assist_progresso_geral = QtWidgets.QProgressBar()
        self.assist_progresso_geral.setRange(0, 100)
        self.assist_progresso_geral.setTextVisible(False)
        cartao.layout_principal.addWidget(self.assist_progresso_geral)
        cartao.layout_principal.addWidget(QtWidgets.QLabel("Arquivo atual"))
        self.assist_progresso_arquivo = QtWidgets.QProgressBar()
        self.assist_progresso_arquivo.setRange(0, 100)
        self.assist_progresso_arquivo.setTextVisible(False)
        cartao.layout_principal.addWidget(self.assist_progresso_arquivo)
        self.assist_mensagem = QtWidgets.QLabel("Pronto para iniciar.")
        self.assist_mensagem.setWordWrap(True)
        cartao.layout_principal.addWidget(self.assist_mensagem)

        self.assist_legados = QtWidgets.QLabel("")
        self.assist_legados.setObjectName("cardHelp")
        self.assist_legados.setWordWrap(True)
        self.assist_limpar_legados = botao("Remover temporários antigos")
        self.assist_limpar_legados.clicked.connect(self._assistente_limpar_legados)
        linha_legados = QtWidgets.QHBoxLayout()
        linha_legados.addWidget(self.assist_legados, 1)
        linha_legados.addWidget(self.assist_limpar_legados)
        cartao.layout_principal.addLayout(linha_legados)
        cartao.layout_principal.addStretch()

        acoes = QtWidgets.QHBoxLayout()
        voltar = botao("Voltar")
        voltar.clicked.connect(lambda: self._assistente_ir_para(1))
        self.assist_cancelar = botao("Cancelar download", "dangerButton")
        self.assist_cancelar.setEnabled(False)
        self.assist_cancelar.clicked.connect(self._assistente_cancelar)
        self.assist_iniciar = botao("Iniciar download", "primaryButton")
        self.assist_iniciar.clicked.connect(self._assistente_iniciar)
        acoes.addWidget(voltar)
        acoes.addStretch()
        acoes.addWidget(self.assist_cancelar)
        acoes.addWidget(self.assist_iniciar)
        cartao.layout_principal.addLayout(acoes)
        return cartao

    # ------------------------------------------------------------ Assistente: navegação

    def _assistente_ir_para(self, passo: int) -> None:
        self.assist_stack.setCurrentIndex(passo)
        for indice, rotulo in enumerate(self.assist_rotulos_passos):
            nome = "stepLabelActive" if indice == passo else (
                "stepLabelDone" if indice < passo else "stepLabel"
            )
            rotulo.setObjectName(nome)
            rotulo.style().unpolish(rotulo)
            rotulo.style().polish(rotulo)
        if passo == 0:
            self._assistente_atualizar_regiao()

    def _assistente_atualizar_regiao(self) -> None:
        if not hasattr(self, "assist_regiao"):
            return
        oeste, sul, leste, norte = self._bbox_atual()
        self.assist_regiao.setText(
            f"<b>{self.nome_regiao.text() or 'Região sem nome'}</b> "
            f"({self.uf.text() or '—'})<br>Área: oeste {oeste:.3f}°, sul {sul:.3f}°, "
            f"leste {leste:.3f}°, norte {norte:.3f}°"
        )

    def _assistente_aplicar_preset(self) -> None:
        chave = self.assist_preset.currentData()
        hoje = QtCore.QDate.currentDate()
        if chave in {"30", "90"}:
            self.assist_inicio.setDate(hoje.addDays(-int(chave)))
            self.assist_fim.setDate(hoje)
        elif chave == "safra":
            ano = hoje.year() if hoje.month() >= 9 else hoje.year() - 1
            self.assist_inicio.setDate(QtCore.QDate(ano, 9, 1))
            self.assist_fim.setDate(QtCore.QDate(ano + 1, 4, 30))

    def _assistente_config(self) -> ConfiguracaoProjeto | None:
        caminho = self._salvar_configuracao(avisar=False)
        if caminho is None:
            return None
        try:
            return ConfiguracaoProjeto.carregar(Path(caminho), raiz=ROOT)
        except (OSError, TypeError, ValueError, KeyError) as erro:
            QtWidgets.QMessageBox.warning(self, "Configuração inválida", str(erro))
            return None

    def _assistente_ocupado(self) -> bool:
        if self.processo.state() != QtCore.QProcess.ProcessState.NotRunning:
            QtWidgets.QMessageBox.information(
                self, "Operação em andamento", "Aguarde a operação atual terminar ou cancele-a."
            )
            return True
        return False

    # ------------------------------------------------------------ Assistente: busca

    def _assistente_buscar(self) -> None:
        if self._assistente_ocupado():
            return
        if self.assist_inicio.date() > self.assist_fim.date():
            QtWidgets.QMessageBox.warning(
                self, "Período inválido", "A data inicial deve ser anterior à data final."
            )
            return
        config = self._salvar_configuracao(avisar=False)
        if config is None:
            return
        argumentos = montar_argumentos_operacao(
            "catalogar",
            config,
            inicio=self.assist_inicio.date().toString(QtCore.Qt.DateFormat.ISODate),
            fim=self.assist_fim.date().toString(QtCore.Qt.DateFormat.ISODate),
            max_itens=0,
            progresso_json=True,
        )
        self._assist_cenas = []
        self._assistente_renderizar_tabela()
        self.assist_contagem.setText("Buscando cenas no catálogo do INPE…")
        self._assistente_ir_para(1)
        self._iniciar_processo(argumentos, "assist_busca")

    def _receber_evento_assistente(self, evento: dict) -> None:
        tipo = evento.get("tipo")
        if tipo == "cena_encontrada":
            self._assist_cenas.append(CenaEncontrada.de_evento(evento))
            self.assist_contagem.setText(f"{len(self._assist_cenas)} cena(s) encontrada(s)…")
            self._assist_timer_tabela.start()
            return
        if tipo not in {"cena", "arquivo"}:
            return
        self._assist_estado.aplicar(evento)
        estado = self._assist_estado
        self.assist_progresso_geral.setValue(estado.percentual_geral)
        if estado.total_arquivo:
            self.assist_progresso_arquivo.setRange(0, 100)
            self.assist_progresso_arquivo.setValue(estado.percentual_arquivo)
        elif estado.arquivo:
            self.assist_progresso_arquivo.setRange(0, 0)
        self.assist_mensagem.setText(estado.mensagem())
        if tipo == "cena":
            for cena in self._assist_cenas:
                if cena.cena in estado.situacoes:
                    cena.situacao = estado.situacoes[cena.cena]
                    cena.baixada = cena.baixada or cena.situacao == "Baixada"
            self._assist_timer_tabela.start()

    def _assistente_renderizar_tabela(self) -> None:
        if not hasattr(self, "assist_tabela"):
            return
        visiveis = filtrar_cenas(
            self._assist_cenas,
            None if self.assist_nuvem_max.value() >= 100 else float(self.assist_nuvem_max.value()),
            self.assist_ocultar_baixadas.isChecked(),
        )
        self._assist_visiveis = visiveis
        tabela = self.assist_tabela
        tabela.blockSignals(True)
        tabela.setRowCount(len(visiveis))
        for linha, cena in enumerate(visiveis):
            marcar = QtWidgets.QTableWidgetItem()
            marcar.setFlags(
                QtCore.Qt.ItemFlag.ItemIsUserCheckable | QtCore.Qt.ItemFlag.ItemIsEnabled
                | QtCore.Qt.ItemFlag.ItemIsSelectable
            )
            marcar.setCheckState(
                QtCore.Qt.CheckState.Checked if cena.selecionada else QtCore.Qt.CheckState.Unchecked
            )
            tabela.setItem(linha, 0, marcar)
            valores = (
                cena.data,
                cena.cena,
                "—" if cena.nuvem_pct is None else f"{cena.nuvem_pct:.0f}%",
                "—" if cena.tamanho_bytes is None else formatar_bytes(cena.tamanho_bytes),
                cena.situacao,
            )
            for coluna, valor in enumerate(valores, start=1):
                tabela.setItem(linha, coluna, QtWidgets.QTableWidgetItem(valor))
        tabela.blockSignals(False)
        tabela.resizeColumnsToContents()
        if self.processo.state() == QtCore.QProcess.ProcessState.NotRunning or not (
            self._operacao_em_execucao == "assist_busca"
        ):
            selecionadas = sum(1 for cena in self._assist_cenas if cena.selecionada)
            ocultas = len(self._assist_cenas) - len(visiveis)
            self.assist_contagem.setText(
                f"{len(self._assist_cenas)} cena(s) encontrada(s), {ocultas} oculta(s) pelos "
                f"filtros, {selecionadas} marcada(s)."
            )

    def _assistente_item_alterado(self, item: QtWidgets.QTableWidgetItem) -> None:
        if item.column() != 0:
            return
        visiveis = getattr(self, "_assist_visiveis", [])
        if 0 <= item.row() < len(visiveis):
            visiveis[item.row()].selecionada = item.checkState() == QtCore.Qt.CheckState.Checked
            self._assistente_renderizar_tabela()

    def _assistente_marcar_todas(self, marcar: bool) -> None:
        for cena in getattr(self, "_assist_visiveis", []):
            cena.selecionada = marcar
        self._assistente_renderizar_tabela()

    def _assistente_visualizar(self, linha: int) -> None:
        visiveis = getattr(self, "_assist_visiveis", [])
        if not 0 <= linha < len(visiveis):
            return
        cena = visiveis[linha]
        if self._assist_resposta_miniatura is not None:
            self._assist_resposta_miniatura.abort()
            self._assist_resposta_miniatura = None
        if cena.preview_local:
            caminho = (ROOT / cena.preview_local).resolve()
            try:
                caminho.relative_to(ROOT.resolve())
            except ValueError:
                caminho = Path()
            pixmap = QtGui.QPixmap(str(caminho)) if caminho.is_file() else QtGui.QPixmap()
            if not pixmap.isNull():
                self._assistente_mostrar_pixmap(pixmap, f"{cena.data} • prévia local")
                return
        url = QtCore.QUrl(cena.miniatura)
        host_stac = QtCore.QUrl(self.stac_url.text().strip()).host()
        if cena.miniatura and url.scheme() == "https" and url.host() == host_stac:
            self.assist_preview.setText("Carregando miniatura…")
            requisicao = QtNetwork.QNetworkRequest(url)
            requisicao.setTransferTimeout(15000)
            resposta = self._assist_rede.get(requisicao)
            self._assist_resposta_miniatura = resposta
            resposta.finished.connect(lambda r=resposta, c=cena: self._assistente_miniatura(r, c))
            return
        self.assist_preview.setText(
            f"{cena.data}\n{cena.cena}\n\nSem visualização disponível antes do download."
        )

    def _assistente_miniatura(self, resposta: QtNetwork.QNetworkReply, cena: CenaEncontrada) -> None:
        if resposta is not self._assist_resposta_miniatura:
            resposta.deleteLater()
            return
        self._assist_resposta_miniatura = None
        dados = (
            bytes(resposta.read(5 * 1024 * 1024))
            if resposta.error() == QtNetwork.QNetworkReply.NetworkError.NoError
            else b""
        )
        resposta.deleteLater()
        pixmap = QtGui.QPixmap()
        if dados and pixmap.loadFromData(dados):
            self._assistente_mostrar_pixmap(pixmap, f"{cena.data} • miniatura do catálogo")
        else:
            self.assist_preview.setText(f"{cena.data}\nMiniatura indisponível.")

    def _assistente_mostrar_pixmap(self, pixmap: QtGui.QPixmap, legenda: str) -> None:
        self.assist_preview.setPixmap(
            pixmap.scaled(
                self.assist_preview.size() - QtCore.QSize(16, 16),
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.assist_preview.setToolTip(legenda)

    # ------------------------------------------------------------ Assistente: download

    def _assistente_selecionadas(self) -> list[CenaEncontrada]:
        return [cena for cena in self._assist_cenas if cena.selecionada]

    def _assistente_revisar(self) -> None:
        selecionadas = self._assistente_selecionadas()
        if not selecionadas:
            QtWidgets.QMessageBox.information(
                self, "Nenhuma cena marcada", "Marque ao menos uma cena para baixar."
            )
            return
        datas = sorted({cena.data for cena in selecionadas})
        ja = sum(1 for cena in selecionadas if cena.baixada)
        self.assist_resumo.setText(
            f"<b>{len(selecionadas)} cena(s)</b> de {self.nome_regiao.text() or 'região sem nome'}, "
            f"datas de {datas[0]} a {datas[-1]}. "
            + (f"{ja} já está(ão) no computador e será(ão) apenas conferida(s). " if ja else "")
            + f"Bandas: {', '.join(self._coletar_dados().get('bandas', [])) or 'nenhuma'}."
        )
        config = self._assistente_config()
        media, amostras = (0, 0)
        if config is not None:
            media, amostras = media_tamanho_cena_local(config.caminho(config.download.pasta))
        self.assist_estimativa.setText(estimar_tamanho(selecionadas, media, amostras))
        self.assist_mensagem.setText("Pronto para iniciar.")
        self.assist_progresso_geral.setValue(0)
        self.assist_progresso_arquivo.setRange(0, 100)
        self.assist_progresso_arquivo.setValue(0)
        self._assistente_atualizar_legados(config)
        self._assistente_ir_para(2)

    def _assistente_iniciar(self) -> None:
        if self._assistente_ocupado():
            return
        selecionadas = self._assistente_selecionadas()
        if not selecionadas:
            self._assistente_ir_para(1)
            return
        config = self._salvar_configuracao(avisar=False)
        if config is None:
            return
        try:
            argumentos = montar_argumentos_operacao(
                "baixar",
                config,
                inicio=self.assist_inicio.date().toString(QtCore.Qt.DateFormat.ISODate),
                fim=self.assist_fim.date().toString(QtCore.Qt.DateFormat.ISODate),
                max_itens=0,
                cenas=[cena.cena for cena in selecionadas],
                progresso_json=True,
            )
        except ValueError as erro:
            QtWidgets.QMessageBox.warning(self, "Não foi possível baixar", str(erro))
            return
        self._assist_estado = EstadoDownload(total_cenas=len(selecionadas))
        self._assist_cancelado = False
        self.assist_mensagem.setText("Preparando o download…")
        self._iniciar_processo(argumentos, "assist_download")

    def _assistente_cancelar(self) -> None:
        if self.processo.state() == QtCore.QProcess.ProcessState.NotRunning:
            return
        self._assist_cancelado = True
        self.assist_mensagem.setText("Cancelando com segurança…")
        self._cancelar()

    def _assistente_processo_finalizado(self, codigo: int) -> None:
        if not hasattr(self, "assist_stack"):
            return
        if self._operacao_em_execucao == "assist_busca":
            self._assistente_renderizar_tabela()
            if codigo != 0 and not self._assist_cenas:
                self.assist_contagem.setText(
                    "A busca falhou. Verifique a conexão e o registro em Operações."
                )
            elif not self._assist_cenas:
                self.assist_contagem.setText(
                    "Nenhuma cena encontrada. Tente um período maior ou outra região."
                )
        elif self._operacao_em_execucao == "assist_download":
            self.assist_mensagem.setText(
                self._assist_estado.resumo_final(codigo, self._assist_cancelado)
            )
            self.assist_progresso_arquivo.setRange(0, 100)
            self._assistente_renderizar_tabela()
            self._assistente_atualizar_legados(None)

    def _assistente_atualizar_legados(self, config: ConfiguracaoProjeto | None) -> None:
        if config is None:
            try:
                config = self._config_painel()
            except (OSError, TypeError, ValueError, KeyError, yaml.YAMLError):
                config = None
        legados = (
            listar_parciais_legados(config.caminho(config.download.pasta)) if config else []
        )
        self._assist_pasta_download = config.caminho(config.download.pasta) if config else None
        if legados:
            tamanho = sum(caminho.stat().st_size for caminho in legados if caminho.is_file())
            self.assist_legados.setText(
                f"{len(legados)} arquivo(s) temporário(s) de versões anteriores "
                f"({formatar_bytes(tamanho)}) não podem ser retomados."
            )
        else:
            self.assist_legados.setText("")
        self.assist_limpar_legados.setVisible(bool(legados))

    def _assistente_limpar_legados(self) -> None:
        pasta = getattr(self, "_assist_pasta_download", None)
        if pasta is None or self._assistente_ocupado():
            return
        legados = listar_parciais_legados(pasta)
        if not legados:
            return
        resposta = QtWidgets.QMessageBox.question(
            self,
            "Remover temporários antigos",
            f"Remover {len(legados)} arquivo(s) temporário(s) incompleto(s)? "
            "Imagens completas não são afetadas.",
        )
        if resposta != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        removidos, liberados = remover_parciais_legados(pasta)
        self.assist_legados.setText(
            f"{removidos} arquivo(s) removido(s); {formatar_bytes(liberados)} liberados."
        )
        self.assist_limpar_legados.setVisible(False)
        self._atualizar_painel()

    def _pagina_dados(self) -> QtWidgets.QWidget:
        pagina = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(pagina)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        fonte = Cartao("Fonte STAC", "Catálogo público do INPE/Brazil Data Cube.")
        form_fonte = QtWidgets.QFormLayout()
        form_fonte.addRow(rotulo_com_ajuda("URL STAC", "Endereço do catálogo que fornece metadados e links dos assets Sentinel-2."), self.stac_url)
        form_fonte.addRow(rotulo_com_ajuda("Coleção", "Escolha uma coleção STAC. A caixa marcada define a coleção consultada; o campo inferior aceita um ID personalizado."), self.colecoes)
        form_fonte.addRow(rotulo_com_ajuda("Bandas", "Marque as bandas e índices que serão procurados e baixados. B02, B03 e B04 são necessárias para previews e análise RGB."), self.bandas_selecao)
        form_fonte.addRow(rotulo_com_ajuda("Pasta de download", "Raiz dos rasters originais e dos arquivos auxiliares baixados; não é a pasta do dataset."), self.pasta_download)
        form_fonte.addRow(rotulo_com_ajuda("Catálogo CSV", "Registro local das cenas, bandas, URLs, arquivos baixados e status de processamento."), self.catalogo)
        form_fonte.addRow(rotulo_com_ajuda("Timeout", "Tempo máximo, em segundos, aguardado por cada resposta HTTP."), self.timeout_segundos)
        form_fonte.addRow(rotulo_com_ajuda("Chunk", "Quantidade de megabytes lida por bloco durante um download. Blocos maiores podem usar mais memória."), self.chunk_mb)
        form_fonte.addRow(rotulo_com_ajuda("Cenas padrão", "Quantidade máxima de cenas usada quando a operação não informa outro limite. Zero significa processar todas."), self.max_itens_teste)
        form_fonte.addRow(rotulo_com_ajuda("Candidatos máximos", "Limita quantas cenas serão avaliadas antes dos filtros de nuvem e qualidade."), self.max_candidatos_teste)
        fonte.layout_principal.addLayout(form_fonte)
        fonte.layout_principal.addStretch()
        layout.addWidget(fonte, 1)

        qualidade = Cartao("Qualidade e visualização", "Filtre antes de baixar as bandas científicas maiores.")
        form_qualidade = QtWidgets.QFormLayout()
        form_qualidade.addRow(rotulo_com_ajuda("Filtro de nuvens", "Ative para rejeitar cenas cuja classificação SCL indique nuvens ou sombra acima do limite abaixo."), self.filtrar_nuvens)
        form_qualidade.addRow(rotulo_com_ajuda("Limite de nuvens", "Percentual máximo de nuvens e sombras permitido em uma cena. Valores menores deixam o filtro mais rigoroso."), self.nuvem_max_pct)
        form_qualidade.addRow(rotulo_com_ajuda("Manter SCL", "Guarda o raster de classificação usado para medir nuvens e pixels válidos."), self.manter_scl)
        form_qualidade.addRow(rotulo_com_ajuda("Gerar preview RGB", "Gera uma imagem JPEG para visualização usando B04 vermelho, B03 verde e B02 azul."), self.gerar_rgb)
        form_qualidade.addRow(rotulo_com_ajuda("Tamanho do preview", "Define a maior dimensão, em pixels, do JPEG de visualização. Não muda o raster científico."), self.tamanho_max_px)
        form_qualidade.addRow(rotulo_com_ajuda("Qualidade JPEG", "Controla a compressão do preview. Valores maiores preservam mais detalhes e geram arquivos maiores."), self.qualidade_jpeg)
        form_qualidade.addRow(rotulo_com_ajuda("Gerar dataset", "Recorta as cenas em patches georreferenciados e, quando possível, gera o RGB PNG de cada patch."), self.gerar_dataset)
        form_qualidade.addRow(rotulo_com_ajuda("Tamanho do patch", "Lado do recorte em pixels. O tamanho do arquivo não representa aumento da resolução espacial."), self.patch_tamanho_px)
        form_qualidade.addRow(rotulo_com_ajuda("Stride do patch", "Distância entre o início de dois recortes. Menor que o tamanho do patch cria sobreposição."), self.patch_stride_px)
        form_qualidade.addRow(rotulo_com_ajuda("Nuvem máxima por patch", "Percentual máximo de pixels classificados como nuvem ou sombra dentro de cada patch."), self.patch_nuvem_max_pct)
        form_qualidade.addRow(rotulo_com_ajuda("Dados válidos mínimos", "Percentual mínimo de pixels válidos exigido para manter um patch no dataset."), self.dados_validos_min_pct)
        form_qualidade.addRow(rotulo_com_ajuda("RGB dataset mínimo", "Valor científico inferior usado no recorte para converter as bandas em RGB PNG."), self.dataset_rgb_minimo)
        form_qualidade.addRow(rotulo_com_ajuda("RGB dataset máximo", "Valor científico superior usado no recorte para converter as bandas em RGB PNG."), self.dataset_rgb_maximo)
        form_qualidade.addRow(rotulo_com_ajuda("Modelo agrícola", "Arquivo .pt local usado pela análise. O modelo precisa ter sido treinado para imagens Sentinel-2 RGB."), self.modelo_ia)
        form_qualidade.addRow(rotulo_com_ajuda("SHA-256 do modelo", "Verificação opcional de integridade. O hash deve corresponder exatamente ao arquivo do modelo."), self.modelo_sha256)
        form_qualidade.addRow(rotulo_com_ajuda("Confiança mínima", "Só aceita detecções com confiança igual ou maior que o valor. Reduzir demais pode gerar falsos positivos."), self.confianca_minima)
        form_qualidade.addRow(rotulo_com_ajuda("IoU máximo", "Controla quanto duas caixas podem se sobrepor antes de uma ser removida como duplicata."), self.iou_maximo)
        form_qualidade.addRow(rotulo_com_ajuda("Tamanho da inferência", "Tamanho de entrada usado pelo modelo. Não altera o tamanho nem a resolução do patch original."), self.tamanho_inferencia_px)
        form_qualidade.addRow(rotulo_com_ajuda("Pasta das análises", "Local dos overlays, resultado.json, relatórios PDF e demais artefatos de cada análise."), self.pasta_analises)
        form_qualidade.addRow(rotulo_com_ajuda("Histórico local", "Arquivo SQLite que registra metadados e caminhos das análises, sem armazenar os rasters."), self.historico_analises)
        form_qualidade.addRow(rotulo_com_ajuda("Gerar relatório", "Cria um PDF com resumo, métricas, gráfico de classes e imagens da análise."), self.gerar_relatorio)
        form_qualidade.addRow(rotulo_com_ajuda("Imagens por análise", "Limita quantos patches aprovados serão enviados ao modelo. Zero significa processar todos."), self.max_imagens_analise)
        qualidade.layout_principal.addLayout(form_qualidade)
        qualidade.layout_principal.addStretch()
        layout.addWidget(qualidade, 1)
        return pagina

    def _pagina_drive(self) -> QtWidgets.QWidget:
        pagina = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(pagina)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        credenciais = Cartao(
            "OAuth do Google",
            "Escolha o JSON de aplicativo para computador. O token será criado e reutilizado automaticamente.",
        )
        escolher = botao("Selecionar JSON OAuth")
        escolher.clicked.connect(self._selecionar_oauth)
        linha_oauth = QtWidgets.QHBoxLayout()
        linha_oauth.addWidget(self.oauth_json, 1)
        linha_oauth.addWidget(escolher)
        form = QtWidgets.QFormLayout()
        form.addRow(rotulo_com_ajuda("Credencial OAuth", "Arquivo JSON do aplicativo Google para iniciar o login. Nunca compartilhe esse arquivo."), linha_oauth)
        form.addRow(rotulo_com_ajuda("Token local", "Arquivo protegido que permite reutilizar a autorização sem novo login. Ele não deve ser versionado."), self.token_json)
        credenciais.layout_principal.addLayout(form)
        layout.addWidget(credenciais)

        destino = Cartao("Destino e lotes", "A hierarquia local de datas e cenas é preservada no Drive.")
        form_destino = QtWidgets.QFormLayout()
        form_destino.addRow(rotulo_com_ajuda("Nome da pasta remota", "Nome da pasta principal usada para organizar os arquivos sincronizados."), self.pasta_remota)
        form_destino.addRow(rotulo_com_ajuda("ID da pasta pai", "ID da pasta do Drive dentro da qual a pasta remota será criada ou procurada."), self.pasta_id)
        form_destino.addRow(rotulo_com_ajuda("Tamanho do lote", "Quantidade de arquivos enviada por rodada. Lotes menores facilitam acompanhar falhas e retomadas."), self.tamanho_lote)
        destino.layout_principal.addLayout(form_destino)
        layout.addWidget(destino)

        nota = QtWidgets.QLabel(
            "Privacidade: a aplicação solicita o escopo drive.file, limitado aos arquivos "
            "criados ou abertos pelo próprio aplicativo. Se o projeto OAuth estiver em modo "
            "de teste, a conta Google usada no login precisa estar cadastrada como usuário de "
            "teste pelo proprietário do JSON. Credenciais não são salvas nos perfis de região."
        )
        nota.setWordWrap(True)
        nota.setObjectName("pageSubtitle")
        layout.addWidget(nota)
        layout.addStretch()
        return pagina

    def _pagina_config(self) -> QtWidgets.QWidget:
        pagina = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(pagina)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        config = Cartao("config.yaml", "Revise o conteúdo antes de salvar ou executar.")
        caminho_linha = QtWidgets.QHBoxLayout()
        caminho_linha.addWidget(self.output_path, 1)
        escolher = botao("Escolher arquivo")
        escolher.clicked.connect(self._selecionar_config)
        caminho_linha.addWidget(escolher)
        config.layout_principal.addLayout(caminho_linha)
        config.layout_principal.addWidget(self.yaml_preview, 1)
        atualizar = botao("Atualizar prévia")
        atualizar.clicked.connect(self._atualizar_preview_yaml)
        config.layout_principal.addWidget(atualizar)
        layout.addWidget(config, 2)

        perfis = Cartao("Perfis de região", "Salvos apenas neste computador em um banco SQLite local.")
        perfis.layout_principal.addWidget(self.perfis, 1)
        linha = QtWidgets.QHBoxLayout()
        carregar = botao("Carregar")
        carregar.clicked.connect(self._carregar_perfil)
        excluir = botao("Excluir", "dangerButton")
        excluir.clicked.connect(self._excluir_perfil)
        linha.addWidget(carregar)
        linha.addWidget(excluir)
        perfis.layout_principal.addLayout(linha)
        layout.addWidget(perfis, 1)
        return pagina

    def _pagina_analise(self) -> QtWidgets.QWidget:
        pagina = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(pagina)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        resumo = Cartao("Resultado da análise")
        topo = QtWidgets.QHBoxLayout()
        topo.addWidget(self.analise_info, 1)
        botoes = QtWidgets.QVBoxLayout()
        self.btn_exportar_csv = botao("Exportar CSV")
        self.btn_exportar_csv.setToolTip("Uma linha por detecção, com caixas em pixels")
        self.btn_exportar_csv.clicked.connect(lambda: self._exportar_resultado("csv"))
        self.btn_exportar_json = botao("Exportar JSON")
        self.btn_exportar_json.clicked.connect(lambda: self._exportar_resultado("json"))
        self.btn_relatorio = botao("Abrir relatório PDF", "primaryButton")
        self.btn_relatorio.clicked.connect(self._abrir_relatorio_analise)
        self.btn_salvar_relatorio = botao("Salvar PDF")
        self.btn_salvar_relatorio.clicked.connect(self._salvar_relatorio_analise)
        for item in (
            self.btn_relatorio, self.btn_salvar_relatorio, self.btn_exportar_csv, self.btn_exportar_json
        ):
            item.setEnabled(False)
            botoes.addWidget(item)
        topo.addLayout(botoes)
        resumo.layout_principal.addLayout(topo)
        layout.addWidget(resumo)

        self.analise_abas = QtWidgets.QTabWidget()
        self.analise_abas.addTab(self._aba_talhoes(), "Talhões (detecção)")
        self.analise_abas.addTab(self._aba_culturas(), "Culturas (classificação)")
        layout.addWidget(self.analise_abas, 1)
        return pagina

    def _aba_talhoes(self) -> QtWidgets.QWidget:
        aba = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(aba)
        layout.setContentsMargins(8, 8, 8, 8)

        esquerda = QtWidgets.QVBoxLayout()
        rotulo = QtWidgets.QLabel("Recortes analisados")
        rotulo.setObjectName("cardTitle")
        esquerda.addWidget(rotulo)
        self.analise_itens = QtWidgets.QListWidget()
        self.analise_itens.currentRowChanged.connect(self._exibir_item_resultado)
        esquerda.addWidget(self.analise_itens, 2)
        rotulo_classes = QtWidgets.QLabel("Resumo por classe")
        rotulo_classes.setObjectName("cardTitle")
        esquerda.addWidget(rotulo_classes)
        esquerda.addWidget(self.analise_tabela, 1)
        aviso = QtWidgets.QLabel(AVISO_AREA)
        aviso.setObjectName("cardHelp")
        aviso.setWordWrap(True)
        esquerda.addWidget(aviso)
        self.analise_aviso_modelo = QtWidgets.QLabel("")
        self.analise_aviso_modelo.setWordWrap(True)
        self.analise_aviso_modelo.setStyleSheet(f"color: {CORES['perigo_texto']};")
        esquerda.addWidget(self.analise_aviso_modelo)
        painel_esquerdo = QtWidgets.QWidget()
        painel_esquerdo.setLayout(esquerda)

        direita = QtWidgets.QVBoxLayout()
        imagens = QtWidgets.QHBoxLayout()
        for titulo, rotulo_imagem in (
            ("Imagem RGB original", self.analise_original),
            ("Resultado da IA sobreposto", self.analise_anotada),
        ):
            coluna = QtWidgets.QVBoxLayout()
            legenda = QtWidgets.QLabel(titulo)
            legenda.setObjectName("cardHelp")
            coluna.addWidget(legenda)
            # A imagem não dita o tamanho do layout; ela é ajustada ao espaço disponível.
            rotulo_imagem.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed
            )
            rotulo_imagem.setMinimumHeight(0)
            rotulo_imagem.setFixedHeight(ALTURA_IMAGEM_RESULTADO)
            coluna.addWidget(rotulo_imagem)
            imagens.addLayout(coluna)
        direita.addLayout(imagens)
        self.analise_item_info = QtWidgets.QLabel("")
        self.analise_item_info.setWordWrap(True)
        direita.addWidget(self.analise_item_info)
        self.analise_deteccoes = QtWidgets.QTableWidget(0, 3)
        self.analise_deteccoes.setHorizontalHeaderLabels(
            ["Classe do modelo", "Confiança", "Caixa (pixels x1, y1, x2, y2)"]
        )
        self.analise_deteccoes.horizontalHeader().setStretchLastSection(True)
        self.analise_deteccoes.horizontalHeader().setSectionResizeMode(
            0, QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        self.analise_deteccoes.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.analise_deteccoes.setMinimumHeight(140)
        direita.addWidget(self.analise_deteccoes, 2)
        self.analise_vegetacao = QtWidgets.QLabel("")
        self.analise_vegetacao.setObjectName("cardHelp")
        self.analise_vegetacao.setWordWrap(True)
        direita.addWidget(self.analise_vegetacao)
        conteudo_direito = QtWidgets.QWidget()
        conteudo_direito.setLayout(direita)
        # Rolagem evita sobreposição quando a janela é baixa.
        painel_direito = QtWidgets.QScrollArea()
        painel_direito.setWidgetResizable(True)
        painel_direito.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        painel_direito.setWidget(conteudo_direito)

        divisor = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        divisor.addWidget(painel_esquerdo)
        divisor.addWidget(painel_direito)
        divisor.setSizes([360, 820])
        layout.addWidget(divisor)
        return aba

    def _aba_culturas(self) -> QtWidgets.QWidget:
        aba = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(aba)
        layout.setContentsMargins(16, 16, 16, 16)
        self.analise_cultura_estado = QtWidgets.QLabel("Cultura não determinada")
        self.analise_cultura_estado.setObjectName("statValue")
        self.analise_cultura_texto = QtWidgets.QLabel(AVISO_CULTURA.split(". ", 1)[-1])
        self.analise_cultura_texto.setWordWrap(True)
        explicacao = QtWidgets.QLabel(
            "Detecção de talhões responde onde há talhões na imagem. Classificação de "
            "culturas responde qual cultura ocupa cada talhão e exige outro modelo, "
            "treinado e avaliado em regiões e safras independentes. Quando um modelo de "
            "classificação for aprovado no Laboratório de IA, os resultados aparecerão aqui."
        )
        explicacao.setObjectName("cardHelp")
        explicacao.setWordWrap(True)
        layout.addWidget(self.analise_cultura_estado)
        layout.addWidget(self.analise_cultura_texto)
        layout.addWidget(explicacao)
        layout.addStretch()
        return aba

    def _pagina_historico(self) -> QtWidgets.QWidget:
        pagina = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(pagina)
        layout.setContentsMargins(0, 0, 0, 0)
        cartao = Cartao(
            "Histórico local",
            "O banco guarda somente metadados e caminhos; GeoTIFFs permanecem fora do SQLite.",
        )
        filtros = QtWidgets.QHBoxLayout()
        self.historico_filtro = QtWidgets.QLineEdit()
        self.historico_filtro.setPlaceholderText("Filtrar por região, cena, modelo ou ID")
        self.historico_filtro.setClearButtonEnabled(True)
        self.historico_filtro.textChanged.connect(self._aplicar_filtro_historico)
        self.historico_periodo = QtWidgets.QComboBox()
        for texto, dias in (("Todas as datas", 0), ("Últimos 30 dias", 30), ("Últimos 12 meses", 365)):
            self.historico_periodo.addItem(texto, dias)
        self.historico_periodo.currentIndexChanged.connect(self._aplicar_filtro_historico)
        filtros.addWidget(self.historico_filtro, 1)
        filtros.addWidget(self.historico_periodo)
        cartao.layout_principal.addLayout(filtros)
        cartao.layout_principal.addWidget(self.historico_tabela, 1)
        acoes = QtWidgets.QHBoxLayout()
        atualizar = botao("Atualizar histórico")
        atualizar.clicked.connect(self._recarregar_historico)
        self.btn_abrir_historico = botao("Abrir resultado", "primaryButton")
        self.btn_abrir_historico.clicked.connect(self._abrir_resultado_historico)
        acoes.addWidget(atualizar)
        acoes.addStretch()
        acoes.addWidget(self.btn_abrir_historico)
        cartao.layout_principal.addLayout(acoes)
        layout.addWidget(cartao)
        return pagina

    # ------------------------------------------------------------ Laboratório de IA

    def _pagina_laboratorio(self) -> QtWidgets.QWidget:
        pagina = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(pagina)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        projeto = Cartao(
            "Projeto",
            "Cada projeto tem um tipo fixo e um esquema de classes versionado. Detecção usa "
            "caixas (talhões); classificação usa uma classe por imagem (culturas).",
        )
        linha = QtWidgets.QHBoxLayout()
        self.lab_projeto = QtWidgets.QComboBox()
        self.lab_projeto.setMinimumWidth(260)
        self.lab_projeto.currentIndexChanged.connect(self._lab_projeto_alterado)
        novo = botao("Novo projeto…", "primaryButton")
        novo.clicked.connect(self._lab_dialogo_novo_projeto)
        self.lab_btn_classes = botao("Editar classes…")
        self.lab_btn_classes.clicked.connect(self._lab_dialogo_classes)
        linha.addWidget(self.lab_projeto, 1)
        linha.addWidget(novo)
        linha.addWidget(self.lab_btn_classes)
        projeto.layout_principal.addLayout(linha)
        self.lab_info_projeto = QtWidgets.QLabel("Crie um projeto para começar.")
        self.lab_info_projeto.setWordWrap(True)
        projeto.layout_principal.addWidget(self.lab_info_projeto)
        acoes = QtWidgets.QHBoxLayout()
        self.lab_btn_importar = botao("Importar recortes do sistema")
        self.lab_btn_importar.setToolTip(
            "Adiciona os recortes RGB já gerados, sem copiar nem alterar os GeoTIFFs"
        )
        self.lab_btn_importar.clicked.connect(self._lab_importar_sistema)
        self.lab_btn_externas = botao("Importar imagens externas…")
        self.lab_btn_externas.clicked.connect(self._lab_importar_externas_dialogo)
        self.lab_btn_integridade = botao("Verificar integridade")
        self.lab_btn_integridade.clicked.connect(self._lab_verificar_integridade)
        for item in (self.lab_btn_importar, self.lab_btn_externas, self.lab_btn_integridade):
            acoes.addWidget(item)
        acoes.addStretch()
        self.lab_progresso = QtWidgets.QProgressBar()
        self.lab_progresso.setTextVisible(False)
        self.lab_progresso.setFixedWidth(180)
        self.lab_progresso.setRange(0, 1)
        acoes.addWidget(self.lab_progresso)
        projeto.layout_principal.addLayout(acoes)
        self.lab_status = QtWidgets.QLabel("")
        self.lab_status.setObjectName("cardHelp")
        self.lab_status.setWordWrap(True)
        projeto.layout_principal.addWidget(self.lab_status)
        layout.addWidget(projeto)

        rotulagem = Cartao("Rotulagem")
        self.lab_rotulagem = PainelRotulagem(
            self._lab_contexto, self._lab_projeto_id, self._lab_selecionadas, self
        )
        self.lab_rotulagem.rotulosAlterados.connect(lambda _tipo: self._lab_projeto_alterado())
        rotulagem.layout_principal.addWidget(self.lab_rotulagem)
        layout.addWidget(rotulagem)

        imagens = Cartao("Imagens do dataset")
        self.lab_contagens = QtWidgets.QLabel("")
        imagens.layout_principal.addWidget(self.lab_contagens)
        filtros = QtWidgets.QHBoxLayout()
        self.lab_filtro_texto = QtWidgets.QLineEdit()
        self.lab_filtro_texto.setPlaceholderText("Região, cena, grade ou data")
        self.lab_filtro_texto.setClearButtonEnabled(True)
        self.lab_filtro_safra = QtWidgets.QComboBox()
        self.lab_filtro_nuvem = QtWidgets.QSpinBox()
        self.lab_filtro_nuvem.setRange(0, 100)
        self.lab_filtro_nuvem.setValue(100)
        self.lab_filtro_nuvem.setSuffix(" % nuvens")
        self.lab_filtro_rotulo = QtWidgets.QComboBox()
        for texto, valor in (
            ("Todas", ""), ("Pendentes", "pendente"), ("Rotuladas", "rotulada"), ("Revisar", "revisar")
        ):
            self.lab_filtro_rotulo.addItem(texto, valor)
        self.lab_filtro_duplicatas = QtWidgets.QCheckBox("Só duplicatas")
        self.lab_filtro_problemas = QtWidgets.QCheckBox("Só com problema")
        self.lab_filtro_situacao = QtWidgets.QComboBox()
        self.lab_filtro_situacao.setToolTip("Triagem de adequação; arquivadas ficam fora dos lotes")
        for texto, valor in (
            ("Ativas", "ativas"), ("Aprovadas (agrícolas)", "aprovada"), ("Triagem: revisar", "revisar"),
            ("Protegidas (rotuladas)", "protegida"), ("Arquivadas", "arquivadas"), ("Todas", "todas"),
        ):
            self.lab_filtro_situacao.addItem(texto, valor)
        for widget in (
            self.lab_filtro_texto, self.lab_filtro_situacao, self.lab_filtro_safra, self.lab_filtro_nuvem,
            self.lab_filtro_rotulo, self.lab_filtro_duplicatas, self.lab_filtro_problemas,
        ):
            filtros.addWidget(widget, 2 if widget is self.lab_filtro_texto else 0)
        self.lab_filtro_texto.textChanged.connect(self._lab_renderizar)
        self.lab_filtro_safra.currentIndexChanged.connect(self._lab_renderizar)
        self.lab_filtro_nuvem.valueChanged.connect(self._lab_renderizar)
        self.lab_filtro_rotulo.currentIndexChanged.connect(self._lab_renderizar)
        self.lab_filtro_duplicatas.toggled.connect(self._lab_renderizar)
        self.lab_filtro_problemas.toggled.connect(self._lab_renderizar)
        self.lab_filtro_situacao.currentIndexChanged.connect(self._lab_renderizar)
        imagens.layout_principal.addLayout(filtros)
        triagem = QtWidgets.QHBoxLayout()
        self.lab_btn_aprovar = botao("Aprovar")
        self.lab_btn_aprovar.setToolTip("Marca as selecionadas como adequadas (talhões agrícolas visíveis)")
        self.lab_btn_aprovar.clicked.connect(lambda: self._lab_triagem("aprovada"))
        self.lab_btn_inadequada = botao("Marcar inadequada e arquivar…")
        self.lab_btn_inadequada.setToolTip(
            "Arquivamento lógico e reversível: nada é apagado; imagens rotuladas são recusadas"
        )
        self.lab_btn_inadequada.clicked.connect(self._lab_arquivar_selecionadas)
        self.lab_btn_restaurar = botao("Restaurar")
        self.lab_btn_restaurar.setToolTip("Devolve imagens arquivadas à lista ativa")
        self.lab_btn_restaurar.clicked.connect(self._lab_restaurar_selecionadas)
        for item in (self.lab_btn_aprovar, self.lab_btn_inadequada, self.lab_btn_restaurar):
            triagem.addWidget(item)
        triagem.addStretch()
        imagens.layout_principal.addLayout(triagem)

        divisor = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        self.lab_grade = QtWidgets.QListWidget()
        self.lab_grade.setViewMode(QtWidgets.QListView.ViewMode.IconMode)
        self.lab_grade.setIconSize(QtCore.QSize(112, 112))
        self.lab_grade.setGridSize(QtCore.QSize(132, 172))
        self.lab_grade.setResizeMode(QtWidgets.QListView.ResizeMode.Adjust)
        self.lab_grade.setMovement(QtWidgets.QListView.Movement.Static)
        self.lab_grade.setUniformItemSizes(True)
        self.lab_grade.setWordWrap(True)
        self.lab_grade.setSelectionMode(QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.lab_grade.currentRowChanged.connect(self._lab_mostrar_detalhe)
        divisor.addWidget(self.lab_grade)
        self.lab_detalhe = QtWidgets.QLabel("Selecione uma imagem.")
        self.lab_detalhe.setWordWrap(True)
        self.lab_detalhe.setAlignment(QtCore.Qt.AlignmentFlag.AlignTop)
        self.lab_detalhe.setTextInteractionFlags(QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)
        rolagem_detalhe = QtWidgets.QScrollArea()
        rolagem_detalhe.setWidgetResizable(True)
        rolagem_detalhe.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        rolagem_detalhe.setMinimumWidth(280)
        rolagem_detalhe.setWidget(self.lab_detalhe)
        divisor.addWidget(rolagem_detalhe)
        divisor.setSizes([820, 300])
        imagens.layout_principal.addWidget(divisor, 1)
        layout.addWidget(imagens, 1)
        self._lab_imagens: list[ImagemDataset] = []
        self._lab_visiveis: list[ImagemDataset] = []
        self._lab_rotulos: dict[str, tuple[str, int]] = {}
        self._lab_anotacoes: dict[str, dict] = {}
        return pagina

    def _lab_selecionadas(self) -> list[str]:
        linhas = sorted({indice.row() for indice in self.lab_grade.selectedIndexes()})
        return [self._lab_visiveis[linha].patch_id for linha in linhas if linha < len(self._lab_visiveis)]

    def _lab_contexto(
        self, criar: bool = True
    ) -> tuple[ConfiguracaoProjeto, Path, RepositorioLaboratorio] | None:
        """Abre o laboratório. Com `criar=False`, não grava configuração nem cria o banco."""
        try:
            config = self._config_painel()
            if config is None and criar:
                caminho = self._salvar_configuracao(avisar=False)
                config = ConfiguracaoProjeto.carregar(Path(caminho), raiz=ROOT) if caminho else None
            if config is None:
                return None
            pasta = resolver_pasta_laboratorio(config)
            banco = pasta / "laboratorio.sqlite3"
            if not criar and not banco.is_file():
                # Só visualizar a página não deve criar pastas ou bancos vazios.
                return None
            return config, pasta, RepositorioLaboratorio(banco)
        except (OSError, TypeError, ValueError, KeyError, yaml.YAMLError) as erro:
            self.lab_status.setText(f"Laboratório indisponível: {erro}")
            return None

    def _lab_projeto_id(self) -> int | None:
        valor = self.lab_projeto.currentData()
        return int(valor) if valor is not None else None

    def _lab_recarregar_projetos(self, selecionar: int | None = None) -> None:
        contexto = self._lab_contexto(criar=False)
        atual = selecionar if selecionar is not None else self._lab_projeto_id()
        self.lab_projeto.blockSignals(True)
        self.lab_projeto.clear()
        if contexto is not None:
            for projeto in contexto[2].listar_projetos():
                self.lab_projeto.addItem(f"{projeto.nome} — {ROTULOS_TIPO[projeto.tipo]}", projeto.id)
        if atual is not None:
            indice = self.lab_projeto.findData(atual)
            if indice >= 0:
                self.lab_projeto.setCurrentIndex(indice)
        self.lab_projeto.blockSignals(False)
        self._lab_projeto_alterado()

    def _lab_projeto_alterado(self) -> None:
        projeto_id = self._lab_projeto_id()
        tem_projeto = projeto_id is not None
        for item in (
            self.lab_btn_classes, self.lab_btn_importar, self.lab_btn_externas, self.lab_btn_integridade,
            self.lab_btn_aprovar, self.lab_btn_inadequada, self.lab_btn_restaurar,
        ):
            item.setEnabled(tem_projeto and not self._lab_ocupado)
        contexto = self._lab_contexto() if tem_projeto else None
        if contexto is None or projeto_id is None:
            self._lab_imagens = []
            self._lab_rotulos = {}
            self._lab_anotacoes = {}
            self.lab_info_projeto.setText("Crie um projeto para começar.")
            self._lab_renderizar()
            self.lab_rotulagem.atualizar()
            return
        repo = contexto[2]
        self._lab_pasta_cache = contexto[1]
        projeto = repo.obter_projeto(projeto_id)
        esquema = repo.esquema_atual(projeto_id)
        formato = (
            "anotações YOLO com caixas (.txt por imagem)"
            if projeto.tipo == "deteccao"
            else "uma classe por imagem, sem caixas"
        )
        self.lab_info_projeto.setText(
            f"<b>{projeto.nome}</b> • {ROTULOS_TIPO[projeto.tipo]} • rótulos: {formato}<br>"
            f"Esquema de classes v{esquema.versao}: {', '.join(esquema.classes)}"
        )
        self._lab_imagens = repo.listar_imagens(projeto_id)
        self._lab_rotulos = repo.rotulos_classificacao(projeto_id)
        self._lab_anotacoes = repo.anotacoes_deteccao(projeto_id)
        safra_atual = self.lab_filtro_safra.currentData()
        self.lab_filtro_safra.blockSignals(True)
        self.lab_filtro_safra.clear()
        self.lab_filtro_safra.addItem("Todas as safras", "")
        for safra in resumo_safras(self._lab_imagens):
            self.lab_filtro_safra.addItem(f"Safra {safra}", safra)
        indice = self.lab_filtro_safra.findData(safra_atual)
        self.lab_filtro_safra.setCurrentIndex(max(0, indice))
        self.lab_filtro_safra.blockSignals(False)
        contagens = repo.contagens(projeto_id)
        self.lab_contagens.setText(
            f"<b>{contagens['ativas']}</b> ativa(s) de {contagens['total']} • "
            f"{contagens['aprovadas']} aprovada(s) • {contagens['arquivadas']} arquivada(s) • "
            f"{contagens['pendentes']} pendente(s) • "
            f"{contagens['rotuladas']} rotulada(s) • {contagens['revisar']} a revisar • "
            f"{contagens['duplicatas']} duplicata(s) • {contagens['problemas']} com problema • "
            f"{contagens['nao_verificadas']} não verificada(s)"
        )
        self._lab_renderizar()
        self.lab_rotulagem.atualizar()

    def _lab_renderizar(self) -> None:
        if not hasattr(self, "lab_grade"):
            return
        nuvem = self.lab_filtro_nuvem.value()
        self._lab_visiveis = filtrar_imagens(
            self._lab_imagens,
            texto=self.lab_filtro_texto.text(),
            safra=str(self.lab_filtro_safra.currentData() or ""),
            nuvem_max_pct=None if nuvem >= 100 else float(nuvem),
            estado_rotulo=str(self.lab_filtro_rotulo.currentData() or ""),
            somente_duplicatas=self.lab_filtro_duplicatas.isChecked(),
            somente_problemas=self.lab_filtro_problemas.isChecked(),
        )
        situacao = self.lab_filtro_situacao.currentData() or "ativas"
        if situacao == "arquivadas":
            self._lab_visiveis = [i for i in self._lab_visiveis if i.arquivada]
        elif situacao != "todas":
            self._lab_visiveis = [i for i in self._lab_visiveis if not i.arquivada]
            if situacao != "ativas":
                self._lab_visiveis = [i for i in self._lab_visiveis if i.triagem == situacao]
        contexto_pasta = getattr(self, "_lab_pasta_cache", None)
        self.lab_grade.clear()
        for imagem in self._lab_visiveis[:3000]:
            legenda = imagem.tile or imagem.patch_id[-10:]
            if imagem.patch_id in self._lab_rotulos:
                legenda = self._lab_rotulos[imagem.patch_id][0]
            elif imagem.patch_id in self._lab_anotacoes:
                legenda = f"{self._lab_anotacoes[imagem.patch_id]['caixas']} caixa(s)"
            if imagem.estado_rotulo == "revisar":
                legenda = "⚠ revisar"
            if imagem.arquivada:
                legenda = "arquivada"
            elif imagem.triagem == "revisar" and imagem.estado_rotulo == "pendente":
                legenda = "triagem: revisar"
            item = QtWidgets.QListWidgetItem(f"{imagem.data or 'externa'}\n{legenda}")
            if contexto_pasta is not None:
                miniatura = caminho_miniatura(contexto_pasta, imagem)
                if miniatura.is_file():
                    item.setIcon(QtGui.QIcon(str(miniatura)))
            marcas = []
            if imagem.duplicata_de:
                marcas.append("duplicata de " + imagem.duplicata_de)
            if imagem.triagem_motivo:
                marcas.append(f"triagem ({imagem.triagem}): {imagem.triagem_motivo}")
            if imagem.arquivada:
                marcas.append("arquivada: " + imagem.motivo_arquivamento)
                item.setForeground(QtGui.QBrush(QtGui.QColor(CORES["texto_suave"])))
            if imagem.integridade in {"ausente", "alterada", "ilegivel"}:
                marcas.append("integridade: " + imagem.integridade)
                item.setForeground(QtGui.QBrush(QtGui.QColor(CORES["perigo_texto"])))
            item.setToolTip(imagem.patch_id + ("\n" + "\n".join(marcas) if marcas else ""))
            self.lab_grade.addItem(item)

    def _lab_mostrar_detalhe(self, linha: int) -> None:
        if not 0 <= linha < len(self._lab_visiveis):
            self.lab_detalhe.setText("Selecione uma imagem.")
            return
        imagem = self._lab_visiveis[linha]
        nuvem = "—" if imagem.nuvem_pct is None else f"{imagem.nuvem_pct:.1f}%"
        linhas = [
            f"<b>{imagem.patch_id}</b>",
            f"Origem: {imagem.origem}",
            f"Cena: {imagem.cena or '—'} • grade {imagem.tile or '—'}",
            f"Data: {imagem.data or '—'} • safra {imagem.safra or '—'}",
            f"Região: {imagem.regiao or '—'} • nuvens {nuvem}",
            f"Tamanho: {imagem.largura}×{imagem.altura} px",
            f"Rotulagem: {imagem.estado_rotulo}",
            f"Integridade: {imagem.integridade.replace('_', ' ')}",
            f"Triagem: {imagem.triagem or 'não feita'}"
            + (f" — {imagem.triagem_motivo}" if imagem.triagem_motivo else ""),
            *([f"<b>Arquivada</b> em {imagem.arquivada_em}: {imagem.motivo_arquivamento}"] if imagem.arquivada else []),
            f"SHA-256: {imagem.sha256[:16]}…",
            f"RGB: {imagem.caminho_rgb}",
        ]
        if imagem.caminho_geotiff:
            linhas.append(f"GeoTIFF científico: {imagem.caminho_geotiff}")
        if imagem.duplicata_de:
            linhas.append(f"<span style='color:{CORES['perigo_texto']}'>Duplicata de {imagem.duplicata_de}</span>")
        if imagem.patch_id in self._lab_rotulos:
            classe, versao = self._lab_rotulos[imagem.patch_id]
            linhas.append(f"Classe: <b>{classe}</b> (esquema v{versao})")
        anotacao = self._lab_anotacoes.get(imagem.patch_id)
        if anotacao is not None:
            linhas.append(f"Anotação: {anotacao['caixas']} caixa(s) (esquema v{anotacao['versao_esquema']})")
            for erro in anotacao["erros"][:5]:
                linhas.append(f"<span style='color:{CORES['perigo_texto']}'>• {erro}</span>")
        self.lab_detalhe.setText("<br>".join(linhas))

    # -------------------------------------------------------- Laboratório: triagem

    def _lab_aplicar(self, operacao) -> None:
        """Executa uma operação de triagem no repositório e mostra o resultado/recusa."""
        projeto_id = self._lab_projeto_id()
        ids = self._lab_selecionadas()
        contexto = self._lab_contexto(criar=False)
        if projeto_id is None or contexto is None:
            return
        if not ids:
            self.lab_status.setText("Selecione ao menos uma imagem.")
            return
        try:
            mensagem = operacao(contexto[2], projeto_id, ids)
        except ValueError as erro:
            QtWidgets.QMessageBox.warning(self, "Triagem", str(erro))
            return
        self.lab_status.setText(mensagem)
        self._lab_projeto_alterado()

    def _lab_triagem(self, triagem: str) -> None:
        def operar(repo, projeto_id, ids):
            n = repo.registrar_triagem(projeto_id, {i: (triagem, "") for i in ids})
            return f"{n} imagem(ns) marcada(s) como {triagem}."
        self._lab_aplicar(operar)

    def _lab_arquivar_selecionadas(self) -> None:
        motivo, ok = QtWidgets.QInputDialog.getText(
            self, "Marcar inadequada", "Motivo (ex.: floresta predominante, pastagem, sem talhões):"
        )
        if not ok:
            return

        def operar(repo, projeto_id, ids):
            if not motivo.strip():
                raise ValueError("Informe o motivo.")
            repo.registrar_triagem(projeto_id, {i: ("descartar", motivo) for i in ids})
            n = repo.arquivar_imagens(projeto_id, ids, motivo)
            return f"{n} imagem(ns) arquivada(s). Nenhum arquivo foi apagado; use Restaurar para desfazer."
        self._lab_aplicar(operar)

    def _lab_restaurar_selecionadas(self) -> None:
        def operar(repo, projeto_id, ids):
            n = repo.restaurar_imagens(projeto_id, ids, "restaurada pela interface")
            return f"{n} imagem(ns) restaurada(s)."
        self._lab_aplicar(operar)

    # -------------------------------------------------------- Laboratório: projetos

    def _lab_criar_projeto(self, nome: str, tipo: str, classes: list[str]) -> bool:
        contexto = self._lab_contexto()
        if contexto is None:
            return False
        try:
            projeto = contexto[2].criar_projeto(nome, tipo, classes)
        except ValueError as erro:
            QtWidgets.QMessageBox.warning(self, "Não foi possível criar o projeto", str(erro))
            return False
        self._lab_recarregar_projetos(selecionar=projeto.id)
        self.lab_status.setText(f"Projeto '{projeto.nome}' criado.")
        return True

    def _lab_dialogo_novo_projeto(self) -> None:
        dialogo = QtWidgets.QDialog(self)
        dialogo.setWindowTitle("Novo projeto")
        form = QtWidgets.QFormLayout(dialogo)
        nome = QtWidgets.QLineEdit()
        tipo = QtWidgets.QComboBox()
        for chave, rotulo in ROTULOS_TIPO.items():
            tipo.addItem(rotulo, chave)
        classes = QtWidgets.QPlainTextEdit()
        classes.setPlaceholderText("Uma classe por linha")
        tipo.currentIndexChanged.connect(
            lambda: classes.setPlainText("\n".join(TIPOS_PROJETO[tipo.currentData()]))
        )
        classes.setPlainText("\n".join(TIPOS_PROJETO[tipo.currentData()]))
        aviso = QtWidgets.QLabel("O tipo não pode ser alterado depois; as classes podem ganhar novas versões.")
        aviso.setObjectName("cardHelp")
        aviso.setWordWrap(True)
        form.addRow("Nome", nome)
        form.addRow("Tipo", tipo)
        form.addRow("Classes", classes)
        form.addRow(aviso)
        botoes = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        botoes.accepted.connect(dialogo.accept)
        botoes.rejected.connect(dialogo.reject)
        form.addRow(botoes)
        if dialogo.exec() == QtWidgets.QDialog.DialogCode.Accepted:
            self._lab_criar_projeto(
                nome.text(), str(tipo.currentData()),
                [linha for linha in classes.toPlainText().splitlines() if linha.strip()],
            )

    def _lab_salvar_classes(self, classes: list[str]) -> bool:
        projeto_id = self._lab_projeto_id()
        contexto = self._lab_contexto()
        if projeto_id is None or contexto is None:
            return False
        try:
            esquema = contexto[2].nova_versao_esquema(projeto_id, classes)
        except ValueError as erro:
            QtWidgets.QMessageBox.warning(self, "Classes inválidas", str(erro))
            return False
        self._lab_projeto_alterado()
        self.lab_status.setText(f"Esquema de classes na versão {esquema.versao}.")
        return True

    def _lab_dialogo_classes(self) -> None:
        projeto_id = self._lab_projeto_id()
        contexto = self._lab_contexto()
        if projeto_id is None or contexto is None:
            return
        atual = contexto[2].esquema_atual(projeto_id)
        texto, ok = QtWidgets.QInputDialog.getMultiLineText(
            self,
            "Editar classes",
            f"Classes (uma por linha). Salvar cria a versão {atual.versao + 1}; a versão "
            f"{atual.versao} continua registrada.",
            "\n".join(atual.classes),
        )
        if ok:
            self._lab_salvar_classes([linha for linha in texto.splitlines() if linha.strip()])

    # -------------------------------------------------------- Laboratório: tarefas

    def _lab_executar(self, descricao: str, trabalho, ao_concluir) -> bool:
        """Executa `trabalho(progresso)` fora da thread da interface."""
        if self._lab_ocupado:
            return False
        self._lab_ocupado = True
        self._lab_ao_concluir = ao_concluir
        self.lab_status.setText(descricao)
        self.lab_progresso.setRange(0, 0)
        self._lab_projeto_alterado()
        ponte = self._lab_ponte

        def executar() -> None:
            try:
                resultado = trabalho(lambda feito, total: ponte.progresso.emit(feito, total))
            except Exception as erro:  # a tarefa nunca derruba a GUI
                resultado = erro
            ponte.concluido.emit(resultado)

        self._lab_pool.start(executar)
        return True

    def _lab_progresso(self, feito: int, total: int) -> None:
        self.lab_progresso.setRange(0, max(total, 1))
        self.lab_progresso.setValue(feito)

    def _lab_tarefa_concluida(self, resultado: object) -> None:
        self._lab_ocupado = False
        self.lab_progresso.setRange(0, 1)
        self.lab_progresso.setValue(0)
        callback, self._lab_ao_concluir = self._lab_ao_concluir, None
        if isinstance(resultado, Exception):
            self.lab_status.setText(f"A tarefa falhou: {resultado}")
        elif callback is not None:
            callback(resultado)
        self._lab_projeto_alterado()

    def _lab_importar_sistema(self) -> None:
        projeto_id = self._lab_projeto_id()
        contexto = self._lab_contexto()
        if projeto_id is None or contexto is None:
            return
        config, pasta, repo = contexto
        regiao = self.nome_regiao.text().strip()

        def trabalho(progresso):
            recortes, origem = descobrir_recortes(config)
            imagens = preparar_recortes(recortes, config.raiz, origem, regiao, progresso)
            novas, existentes = repo.adicionar_imagens(projeto_id, imagens)
            for imagem in imagens:
                gerar_miniatura(imagem, config.raiz, pasta)
            return origem, len(recortes), novas, existentes

        def concluir(resultado) -> None:
            origem, encontrados, novas, existentes = resultado
            fonte = "no catálogo de patches" if origem == "catalogo" else "na pasta do dataset (catálogo ausente)"
            self.lab_status.setText(
                f"{encontrados} recorte(s) encontrados {fonte}: {novas} adicionado(s), "
                f"{existentes} já estavam no projeto. Nenhum arquivo foi copiado ou alterado."
            )

        self._lab_pasta_cache = pasta
        self._lab_executar("Importando recortes do sistema…", trabalho, concluir)

    def _lab_importar_externas_dialogo(self) -> None:
        arquivos, _ = QtWidgets.QFileDialog.getOpenFileNames(
            self, "Importar imagens externas", str(Path.home()), "Imagens (*.png *.jpg *.jpeg)"
        )
        if arquivos:
            self._lab_importar_externas([Path(caminho) for caminho in arquivos])

    def _lab_importar_externas(self, arquivos: list[Path]) -> None:
        projeto_id = self._lab_projeto_id()
        contexto = self._lab_contexto()
        if projeto_id is None or contexto is None:
            return
        config, pasta, repo = contexto
        regiao = self.nome_regiao.text().strip()

        def trabalho(progresso):
            imagens, erros = importar_externas(arquivos, pasta, config.raiz, regiao, progresso)
            novas, existentes = repo.adicionar_imagens(projeto_id, imagens)
            for imagem in imagens:
                gerar_miniatura(imagem, config.raiz, pasta)
            return novas, existentes, erros

        def concluir(resultado) -> None:
            novas, existentes, erros = resultado
            texto = (
                f"{novas} imagem(ns) externa(s) copiada(s) para o laboratório; {existentes} já existiam."
                " Os arquivos originais não foram alterados."
            )
            if erros:
                texto += f" {len(erros)} recusada(s): " + "; ".join(erros[:3])
            self.lab_status.setText(texto)

        self._lab_pasta_cache = pasta
        self._lab_executar("Importando imagens externas…", trabalho, concluir)

    def _lab_verificar_integridade(self) -> None:
        projeto_id = self._lab_projeto_id()
        contexto = self._lab_contexto()
        if projeto_id is None or contexto is None:
            return
        config, pasta, repo = contexto
        imagens = repo.listar_imagens(projeto_id)

        def trabalho(progresso):
            estados = verificar_lote(imagens, config.raiz, progresso)
            repo.atualizar_integridade(projeto_id, estados)
            return estados

        def concluir(estados) -> None:
            problemas = {chave: valor for chave, valor in estados.items() if valor != "ok"}
            self.lab_status.setText(
                f"{len(estados)} imagem(ns) verificada(s): {len(estados) - len(problemas)} íntegra(s), "
                f"{len(problemas)} com problema (ausente, alterada ou ilegível)."
            )

        self._lab_pasta_cache = pasta
        self._lab_executar("Verificando integridade (SHA-256 e leitura)…", trabalho, concluir)

    def _pagina_sobre(self) -> QtWidgets.QWidget:
        pagina = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(pagina)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        apresentacao = Cartao(
            "Sentinel-2 MT Downloader",
            "Ferramenta para consultar, baixar, organizar e analisar cenas Sentinel-2.",
        )
        texto = QtWidgets.QLabel(
            "O aplicativo consulta o catálogo STAC do INPE/Brazil Data Cube, "
            "aplica filtros de qualidade, preserva os rasters científicos e "
            "pode gerar previews, patches georreferenciados e análises locais."
        )
        texto.setWordWrap(True)
        apresentacao.layout_principal.addWidget(texto)
        layout.addWidget(apresentacao)

        fluxo = Cartao("Fluxo de trabalho", "Use as páginas na ordem que melhor se adapta à sua tarefa.")
        passos = QtWidgets.QLabel(
            "1. Defina a área e o período.\n"
            "2. Escolha coleção, bandas e filtros.\n"
            "3. Salve a configuração e execute uma operação.\n"
            "4. Consulte previews, dataset, análises e histórico."
        )
        passos.setWordWrap(True)
        fluxo.layout_principal.addWidget(passos)
        layout.addWidget(fluxo)

        nota = QtWidgets.QLabel(
            "Integridade científica: os GeoTIFFs originais e multibanda mantêm "
            "georreferenciamento, valores e resolução nativa. Previews e PNGs são "
            "representações para visualização ou modelos RGB."
        )
        nota.setWordWrap(True)
        nota.setObjectName("pageSubtitle")
        layout.addWidget(nota)
        layout.addStretch()
        return pagina

    def _pagina_ajuda(self) -> QtWidgets.QWidget:
        pagina = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(pagina)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        inicio = Cartao("Primeiros passos", "Siga esta sequência para uma execução comum.")
        texto_inicio = QtWidgets.QLabel(
            "• Em Área e período, segure Shift e arraste no mapa para desenhar a região.\n"
            "• Em Dados e qualidade, marque as bandas e revise o filtro de nuvens.\n"
            "• Em Início, veja o próximo passo; em Operações, escolha a operação e clique em Executar operação.\n"
            "• Acompanhe mensagens e arquivos na Saída da operação."
        )
        texto_inicio.setWordWrap(True)
        inicio.layout_principal.addWidget(texto_inicio)
        layout.addWidget(inicio)

        problemas = Cartao("Problemas comuns", "Estas verificações resolvem a maioria dos avisos.")
        texto_problemas = QtWidgets.QLabel(
            "• Nenhuma cena encontrada: confira datas, área, coleção e URL STAC.\n"
            "• Download rejeitado: verifique conexão, espaço em disco e o log da operação.\n"
            "• RGB incompleto: a cena precisa conter B04, B03 e B02.\n"
            "• Google Drive: confirme o JSON OAuth e autorize a conta de teste do projeto.\n"
            "• Para suporte, envie a mensagem do log e a configuração sem credenciais."
        )
        texto_problemas.setWordWrap(True)
        problemas.layout_principal.addWidget(texto_problemas)
        layout.addWidget(problemas)

        seguranca = QtWidgets.QLabel(
            "Nunca compartilhe tokens, client secrets ou URLs de autorização completas. "
            "Use a página Configuração para revisar o YAML antes de executar."
        )
        seguranca.setWordWrap(True)
        seguranca.setObjectName("pageSubtitle")
        layout.addWidget(seguranca)
        layout.addStretch()
        return pagina

    def _barra_acoes(self) -> QtWidgets.QWidget:
        barra = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(barra)
        layout.setContentsMargins(0, 0, 0, 0)
        self.progresso = QtWidgets.QProgressBar()
        self.progresso.setTextVisible(False)
        self.progresso.setFixedWidth(180)
        self.progresso.setRange(0, 1)
        self.progresso.setValue(0)
        layout.addWidget(self.progresso)
        layout.addStretch()
        self.btn_salvar = botao("Salvar configuração")
        self.btn_cancelar = botao("Cancelar", "dangerButton")
        self.btn_cancelar.setEnabled(False)
        self.btn_executar = botao("Executar operação", "primaryButton")
        layout.addWidget(self.btn_salvar)
        layout.addWidget(self.btn_cancelar)
        layout.addWidget(self.btn_executar)
        return barra

    def _conectar_eventos(self) -> None:
        self.mapa.areaSelecionada.connect(self._receber_bbox)
        self.mapa.desenhoConcluido.connect(self._desenho_concluido)
        self.operacao.currentIndexChanged.connect(self._atualizar_operacao)
        self.btn_salvar.clicked.connect(self._salvar_configuracao)
        self.btn_executar.clicked.connect(self._executar)
        self.btn_cancelar.clicked.connect(self._cancelar)
        self.historico_tabela.doubleClicked.connect(self._abrir_relatorio_historico)

    def _navegar(self, indice: int) -> None:
        self.stack.setCurrentIndex(indice)
        # Mantém o menu lateral coerente quando a navegação parte de um botão da página.
        self.botoes_navegacao[indice].setChecked(True)
        self.titulo_pagina.setText(self.PAGINAS[indice][0])
        self.subtitulo_pagina.setText(self.PAGINAS[indice][1])
        titulo = self.PAGINAS[indice][0]
        if titulo == "Área e período":
            QtCore.QTimer.singleShot(0, self.mapa.reativar)
            QtCore.QTimer.singleShot(200, self.mapa.reativar)
        elif titulo == "Histórico":
            self._recarregar_historico()
        elif titulo == "Início":
            self._atualizar_painel()
        elif titulo == "Laboratório de IA":
            self._lab_recarregar_projetos()

    def _atualizar_operacao(self) -> None:
        operacao = self.operacao.currentData()
        mensagens = {
            "catalogar": "Consulta o INPE e atualiza o catálogo CSV sem baixar GeoTIFFs.",
            "baixar": "Filtra nuvens, baixa as bandas aprovadas e gera previews RGB.",
            "dataset": "Reutiliza GeoTIFFs locais e gera patches científicos e RGB PNG.",
            "analisar": "Baixa e processa cenas, executa o detector agrícola e gera resultados locais.",
            "sincronizar": "Envia as imagens locais ao Google Drive em lotes configuráveis.",
        }
        self.resumo_operacao.setText(mensagens[str(operacao)])
        self.max_execucao.setEnabled(operacao != "sincronizar")
        self.max_execucao.setToolTip(
            "Não se aplica à sincronização."
            if operacao == "sincronizar"
            else "Limite de cenas desta operação; zero processa todas."
        )

    def _colecao_marcada(self, nome: str, marcada: bool) -> None:
        """Mantém o campo textual sincronizado com a coleção marcada."""
        if marcada:
            self.colecao.blockSignals(True)
            self.colecao.setText(nome)
            self.colecao.blockSignals(False)

    def _colecao_personalizada_alterada(self, valor: str) -> None:
        if valor not in self.colecoes_checkboxes:
            for caixa in self.colecoes_checkboxes.values():
                caixa.setChecked(False)

    def _coletar_dados(self) -> dict[str, Any]:
        bbox = normalizar_bbox(
            [self.oeste.value(), self.sul.value(), self.leste.value(), self.norte.value()]
        )
        bandas = [nome for nome, caixa in self.bandas_checkboxes.items() if caixa.isChecked()]
        colecao = next(
            (nome for nome, caixa in self.colecoes_checkboxes.items() if caixa.isChecked()),
            self.colecao.text().strip(),
        )
        return {
            "bbox": bbox,
            "nome_regiao": self.nome_regiao.text().strip() or "Região personalizada",
            "uf": self.uf.text().strip().upper() or "MT",
            "colecao": colecao or "S2-16D-2",
            "stac_url": self.stac_url.text().strip() or "https://data.inpe.br/bdc/stac/v1/",
            "inicio": self.inicio.date().toString(QtCore.Qt.DateFormat.ISODate),
            "fim": self.fim.date().toString(QtCore.Qt.DateFormat.ISODate),
            "bandas": bandas or GeradorConfiguracao.BANDAS_PADRAO,
            "filtrar_nuvens": self.filtrar_nuvens.isChecked(),
            "manter_scl": self.manter_scl.isChecked(),
            "gerar_rgb": self.gerar_rgb.isChecked(),
            "nuvem_max_pct": self.nuvem_max_pct.value(),
            "pasta_download": self.pasta_download.text().strip() or "data/sentinel2",
            "catalogo": self.catalogo.text().strip() or "catalogo/catalogo_imagens.csv",
            "tamanho_max_px": self.tamanho_max_px.value(),
            "qualidade_jpeg": self.qualidade_jpeg.value(),
            "gerar_dataset": self.gerar_dataset.isChecked(),
            "patch_tamanho_px": int(self.patch_tamanho_px.currentData()),
            "patch_stride_px": self.patch_stride_px.value(),
            "patch_nuvem_max_pct": self.patch_nuvem_max_pct.value(),
            "dados_validos_min_pct": self.dados_validos_min_pct.value(),
            "dataset_rgb_metodo": "fixed",
            "dataset_rgb_minimo": self.dataset_rgb_minimo.value(),
            "dataset_rgb_maximo": self.dataset_rgb_maximo.value(),
            "modelo_ia": self.modelo_ia.text().strip() or "analise/models/best.pt",
            "modelo_sha256": self.modelo_sha256.text().strip().lower(),
            "confianca_minima": self.confianca_minima.value() / 100.0,
            "iou_maximo": self.iou_maximo.value() / 100.0,
            "tamanho_inferencia_px": self.tamanho_inferencia_px.value(),
            "pasta_analises": self.pasta_analises.text().strip() or "data/analises",
            "historico_analises": self.historico_analises.text().strip()
            or "data/historico-analises.sqlite3",
            "gerar_relatorio": self.gerar_relatorio.isChecked(),
            "max_imagens_analise": self.max_imagens_analise.value(),
            "timeout_segundos": self.timeout_segundos.value(),
            "chunk_mb": self.chunk_mb.value(),
            "max_itens_teste": self.max_itens_teste.value(),
            "max_candidatos_teste": self.max_candidatos_teste.value(),
            "pasta_remota": self.pasta_remota.text().strip() or "sentinel2-mt",
            "oauth_json": self.oauth_json.text().strip() or "${GOOGLE_OAUTH_JSON:-}",
            "token_json": self.token_json.text().strip()
            or "${GOOGLE_TOKEN_JSON:-config/google-token.json}",
            "pasta_id": self.pasta_id.text().strip() or "${GOOGLE_PASTA_ID:-root}",
            "tamanho_lote": self.tamanho_lote.value(),
            "extensoes": [".tif", ".tiff", ".jpg", ".jpeg"],
        }

    def _atualizar_preview_yaml(self) -> None:
        try:
            payload = gerar_config(self._coletar_dados())
            self.yaml_preview.setPlainText(
                yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
            )
        except (TypeError, ValueError) as erro:
            self.yaml_preview.setPlainText(f"Configuração inválida: {erro}")

    def _salvar_configuracao(self, avisar: bool = True) -> Path | None:
        try:
            destino = persistir_config(
                self.output_path.text().strip() or DEFAULT_CONFIG,
                self._coletar_dados(),
            )
            self._atualizar_preview_yaml()
            self.status.setText("Configuração salva")
            if avisar:
                self.statusBar().showMessage(f"Configuração salva em {destino}", 5000)
            return destino
        except Exception as erro:
            QtWidgets.QMessageBox.critical(self, "Configuração inválida", str(erro))
            return None

    def _executar(self) -> None:
        if self.processo.state() != QtCore.QProcess.ProcessState.NotRunning:
            return
        config = self._salvar_configuracao(avisar=False)
        if config is None:
            return
        try:
            argumentos = montar_argumentos_operacao(
                str(self.operacao.currentData()),
                config,
                inicio=self.inicio.date().toString(QtCore.Qt.DateFormat.ISODate),
                fim=self.fim.date().toString(QtCore.Qt.DateFormat.ISODate),
                max_itens=self.max_execucao.value(),
                oauth_json=self.oauth_json.text().strip(),
                tamanho_lote=self.tamanho_lote.value(),
                patch_size=int(self.patch_tamanho_px.currentData()),
                patch_stride=self.patch_stride_px.value(),
            )
        except (FileNotFoundError, TypeError, ValueError) as erro:
            QtWidgets.QMessageBox.warning(self, "Não foi possível executar", str(erro))
            return
        self._iniciar_processo(argumentos, str(self.operacao.currentData()))

    def _iniciar_processo(self, argumentos: list[str], operacao: str) -> None:
        """Inicia a CLI oficial; usado por Operações e pelo assistente de download."""
        self.log.clear()
        self._saida_pendente = ""
        self._resultado_pendente = None
        self._operacao_em_execucao = operacao
        programa, argumentos_processo, diretorio = comando_cli_empacotado(argumentos)
        self.log.appendPlainText(
            f"$ {shlex.join([programa, *argumentos_processo])}\n"
        )
        self._definir_execucao(True, "Executando")
        self.processo.setWorkingDirectory(str(diretorio))
        self.processo.start(programa, argumentos_processo)

    def _ler_saida(self) -> None:
        texto = bytes(self.processo.readAllStandardOutput()).decode("utf-8", errors="replace")
        if not texto:
            return
        self._saida_pendente += texto
        visiveis: list[str] = []
        while "\n" in self._saida_pendente:
            linha, self._saida_pendente = self._saida_pendente.split("\n", 1)
            if not self._consumir_linha(linha):
                visiveis.append(linha)
        # Linhas parciais sem quebra (por exemplo, barras de progresso) também aparecem.
        if self._saida_pendente and not self._saida_pendente.startswith("[PROGRESSO]"):
            visiveis.append(self._saida_pendente)
            self._saida_pendente = ""
        if visiveis:
            self._anexar_log("\n".join(visiveis) + ("\n" if not self._saida_pendente else ""))

    def _anexar_log(self, texto: str) -> None:
        cursor = self.log.textCursor()
        cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
        cursor.insertText(texto)
        self.log.setTextCursor(cursor)
        self.log.ensureCursorVisible()

    def _consumir_linha(self, linha: str) -> bool:
        """Trata eventos estruturados; retorna True quando a linha não vai ao log."""
        evento = ler_evento(linha.rstrip("\r"))
        if evento is not None:
            self._receber_evento_assistente(evento)
            return True
        self._processar_linha_resultado(linha)
        return False

    def _processo_finalizado(self, codigo: int, _status) -> None:
        if self._saida_pendente:
            if not self._consumir_linha(self._saida_pendente):
                self._anexar_log(self._saida_pendente)
            self._saida_pendente = ""
        if codigo == 0 and self._resultado_pendente is not None:
            try:
                self._carregar_resultado_analise(self._resultado_pendente)
            except (OSError, TypeError, ValueError, json.JSONDecodeError) as erro:
                self.log.appendPlainText(f"\n[ERRO] Resultado da análise inválido: {erro}")
            finally:
                self._resultado_pendente = None
        mensagem = "Concluído" if codigo == 0 else f"Encerrado com código {codigo}"
        self.log.appendPlainText(f"\n[{mensagem}]")
        self._definir_execucao(False, mensagem)
        self._assistente_processo_finalizado(codigo)
        self._atualizar_painel()

    def _processar_linha_resultado(self, linha: str) -> None:
        marcador = "[ANALISE_RESULTADO] "
        if self._operacao_em_execucao != "analisar" or not linha.startswith(marcador):
            return
        caminho = Path(linha[len(marcador):].strip()).expanduser()
        if caminho.anchor or ".." in caminho.parts or caminho.suffix.lower() != ".json":
            return
        self._resultado_pendente = ROOT / caminho

    def _carregar_resultado_analise(self, caminho: Path) -> None:
        raiz_dataset, raiz_analises = self._raizes_artefatos()
        caminho = self._resolver_em_raiz(caminho, raiz_analises, {".json"})
        resultado = carregar_resultado(
            caminho, raiz_projeto=ROOT, raiz_dataset=raiz_dataset, raiz_analises=raiz_analises
        )
        dados = resultado.dados
        self._ultimo_resultado = dados
        self._resultado_visual: ResultadoVisualizacao | None = resultado
        estatisticas = resultado.estatisticas
        periodo = f"{resultado.periodo_inicio or '—'} a {resultado.periodo_fim or '—'}"
        modelo = resultado.modelo_versao or "versão não informada"
        if resultado.modelo_nome:
            modelo = f"{resultado.modelo_nome} {modelo}"
        hash_curto = resultado.modelo_sha256[:12] if resultado.modelo_sha256 else "—"
        self.analise_info.setText(
            f"Região: {resultado.regiao or '—'}\nPeríodo: {periodo}\n"
            f"Cenas: {', '.join(resultado.cenas) or '—'}\n"
            f"Modelo: {modelo} (detecção por caixas; SHA-256 {hash_curto}…)\n"
            f"Dispositivo de processamento: {resultado.dispositivo or '—'}\n"
            f"Analisado em: {resultado.criado_em or '—'}\n"
            f"Detecções: {estatisticas.get('total_deteccoes', 0)} em "
            f"{len(resultado.itens) or estatisticas.get('total_tiles', 0)} recorte(s)"
        )
        por_classe = estatisticas.get("por_classe", {})
        self.analise_tabela.setRowCount(len(por_classe))
        for linha, classe in enumerate(sorted(por_classe)):
            valores = (
                classe,
                str(por_classe[classe]),
                self._percentual_confianca(estatisticas.get("confianca_media_por_classe", {}).get(classe)),
                self._percentual_confianca(estatisticas.get("confianca_minima_por_classe", {}).get(classe)),
                self._percentual_confianca(estatisticas.get("confianca_maxima_por_classe", {}).get(classe)),
            )
            for coluna, valor in enumerate(valores):
                self.analise_tabela.setItem(linha, coluna, QtWidgets.QTableWidgetItem(valor))

        self.analise_itens.blockSignals(True)
        self.analise_itens.clear()
        for item in resultado.itens:
            texto = f"{item.data or 'sem data'} • {len(item.deteccoes)} detecção(ões) • {item.patch_id[-28:]}"
            self.analise_itens.addItem(texto)
        self.analise_itens.blockSignals(False)
        if resultado.itens:
            self.analise_itens.setCurrentRow(0)
            self._exibir_item_resultado(0)
        else:
            # Resultados antigos sem lista por recorte: mostra as imagens principais.
            self._exibir_imagem_resultado(
                self.analise_original, dados.get("imagem_original", ""), raiz_dataset
            )
            self._exibir_imagem_resultado(
                self.analise_anotada, dados.get("imagem_analisada", ""), raiz_analises
            )
            self.analise_deteccoes.setRowCount(0)
            self.analise_item_info.setText("")
            self.analise_vegetacao.setText("")

        self.analise_aviso_modelo.setText(aviso_classes_modelo(resultado.modelo_classes))
        aprovado = modelo_classificacao_aprovado()
        self.analise_cultura_estado.setText(
            "Cultura não determinada" if aprovado is None else "Classificação disponível"
        )
        relatorio = resultado.caminho_relatorio
        self.btn_relatorio.setEnabled(bool(relatorio and relatorio.is_file()))
        self.btn_salvar_relatorio.setEnabled(bool(relatorio and relatorio.is_file()))
        self.btn_exportar_csv.setEnabled(True)
        self.btn_exportar_json.setEnabled(True)
        self.analise_abas.setCurrentIndex(0)
        self._navegar(self._indice_pagina("Análise da região"))
        self._recarregar_historico()

    def _exibir_item_resultado(self, linha: int) -> None:
        resultado = getattr(self, "_resultado_visual", None)
        if resultado is None or not 0 <= linha < len(resultado.itens):
            return
        item: ItemResultado = resultado.itens[linha]
        self._mostrar_pixmap_arquivo(self.analise_original, item.imagem_original)
        self._mostrar_pixmap_arquivo(self.analise_anotada, item.imagem_analisada)
        nuvens = "—" if item.nuvens_pct is None else f"{item.nuvens_pct:.1f}%"
        self.analise_item_info.setText(
            f"<b>Recorte</b> {item.patch_id}<br>Cena {item.cena or '—'} • aquisição "
            f"{item.data or '—'} • nuvens no recorte {nuvens} • "
            f"{len(item.deteccoes)} detecção(ões)"
        )
        self.analise_deteccoes.setRowCount(len(item.deteccoes))
        for indice, deteccao in enumerate(item.deteccoes):
            caixa = ", ".join(f"{valor:.0f}" for valor in deteccao.caixa_px)
            for coluna, valor in enumerate(
                (deteccao.classe, self._percentual_confianca(deteccao.confianca), caixa)
            ):
                self.analise_deteccoes.setItem(indice, coluna, QtWidgets.QTableWidgetItem(valor))
        try:
            indicadores = indicadores_vegetacao(item)
        except Exception as erro:  # leitura raster nunca deve derrubar a tela
            indicadores = []
            self.analise_vegetacao.setText(f"Indicadores de vegetação indisponíveis: {erro}")
            return
        if indicadores:
            partes = [
                f"{ind.indice} médio {ind.media:.2f} (mín. {ind.minimo:.2f}, máx. "
                f"{ind.maximo:.2f}; {ind.pixels_validos_pct:.0f}% de pixels válidos)"
                for ind in indicadores
            ]
            self.analise_vegetacao.setText(
                "Vegetação no recorte inteiro (não por talhão): " + "; ".join(partes) + "."
            )
        else:
            self.analise_vegetacao.setText(
                "Indicadores de vegetação indisponíveis para este recorte (NDVI/EVI não "
                "baixados ou escala da coleção não documentada)."
            )

    @staticmethod
    def _mostrar_pixmap_arquivo(rotulo: QtWidgets.QLabel, caminho: Path | None) -> None:
        imagem = QtGui.QPixmap(str(caminho)) if caminho else QtGui.QPixmap()
        if imagem.isNull():
            rotulo.setPixmap(QtGui.QPixmap())
            rotulo.setText("Imagem indisponível")
            return
        largura = max(160, min(rotulo.width(), 520))
        altura = ALTURA_IMAGEM_RESULTADO
        rotulo.setPixmap(
            imagem.scaled(
                largura, altura, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _exportar_resultado(self, formato: str) -> None:
        resultado = getattr(self, "_resultado_visual", None)
        if resultado is None:
            return
        sugestao = Path.home() / f"{resultado.analysis_id}.{formato}"
        filtro = "CSV (*.csv)" if formato == "csv" else "JSON (*.json)"
        destino, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Exportar resultado", str(sugestao), filtro
        )
        if not destino:
            return
        destino_path = Path(destino)
        if destino_path.suffix.lower() != f".{formato}":
            destino_path = destino_path.with_suffix(f".{formato}")
        try:
            if formato == "csv":
                linhas = exportar_csv(resultado, destino_path)
                mensagem = f"{linhas} detecção(ões) exportada(s) para {destino_path.name}."
            else:
                exportar_json(resultado, destino_path)
                mensagem = f"Resultado exportado para {destino_path.name}."
        except OSError as erro:
            QtWidgets.QMessageBox.warning(self, "Não foi possível exportar", str(erro))
            return
        self.statusBar().showMessage(mensagem, 6000)

    @staticmethod
    def _percentual_confianca(valor: object) -> str:
        return "—" if valor is None else f"{float(valor) * 100:.1f}%"

    def _raizes_artefatos(self) -> tuple[Path, Path]:
        config_path = Path(self.output_path.text().strip() or DEFAULT_CONFIG)
        config = ConfiguracaoProjeto.carregar(config_path, raiz=ROOT)
        return (
            config.caminho(config.dataset.pasta).resolve(),
            config.caminho(config.analise.pasta).resolve(),
        )

    @staticmethod
    def _resolver_em_raiz(valor: object, raiz: Path, sufixos: set[str]) -> Path:
        caminho = Path(str(valor)).expanduser()
        caminho = caminho if caminho.is_absolute() else ROOT / caminho
        if caminho.is_symlink():
            raise ValueError("Links simbólicos não são aceitos para artefatos")
        resolvido = caminho.resolve()
        try:
            resolvido.relative_to(raiz.resolve())
        except ValueError as exc:
            raise ValueError("Artefato fora da raiz permitida") from exc
        if resolvido.is_symlink() or not resolvido.is_file() or resolvido.suffix.lower() not in sufixos:
            raise ValueError("Artefato ausente ou inválido")
        return resolvido

    def _resolver_em_raiz_opcional(
        self, valor: object, raiz: Path, sufixos: set[str]
    ) -> Path | None:
        if not valor:
            return None
        try:
            return self._resolver_em_raiz(valor, raiz, sufixos)
        except ValueError:
            return None

    def _exibir_imagem_resultado(
        self, rotulo: QtWidgets.QLabel, valor: object, raiz: Path
    ) -> None:
        caminho = self._resolver_em_raiz_opcional(
            valor, raiz, {".png", ".jpg", ".jpeg"}
        )
        imagem = QtGui.QPixmap(str(caminho)) if caminho else QtGui.QPixmap()
        if imagem.isNull():
            rotulo.setText("Imagem indisponível")
            rotulo.setPixmap(QtGui.QPixmap())
            return
        rotulo.setPixmap(
            imagem.scaled(
                560, 360, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _abrir_relatorio_analise(self) -> None:
        if not self._ultimo_resultado:
            return
        _, raiz_analises = self._raizes_artefatos()
        caminho = self._resolver_em_raiz_opcional(
            self._ultimo_resultado.get("caminho_relatorio", ""), raiz_analises, {".pdf"}
        )
        if caminho and caminho.is_file():
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(caminho)))

    def _salvar_relatorio_analise(self) -> None:
        """Copia o PDF da análise para um local escolhido pelo usuário."""
        if not self._ultimo_resultado:
            return
        try:
            _, raiz_analises = self._raizes_artefatos()
            origem = self._resolver_em_raiz_opcional(
                self._ultimo_resultado.get("caminho_relatorio", ""), raiz_analises, {".pdf"}
            )
        except (OSError, TypeError, ValueError):
            origem = None
        if not origem or not origem.is_file():
            self.statusBar().showMessage("O relatório PDF da análise não está disponível.", 5000)
            return

        identificador = str(self._ultimo_resultado.get("analysis_id", "analise"))
        destino, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Salvar relatório PDF",
            str(Path.home() / f"relatorio-{identificador}.pdf"),
            "Documento PDF (*.pdf)",
        )
        if not destino:
            return
        destino_path = Path(destino).expanduser()
        if destino_path.suffix.lower() != ".pdf":
            destino_path = destino_path.with_suffix(".pdf")
        try:
            destino_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origem, destino_path)
            self.statusBar().showMessage(f"Relatório salvo em: {destino_path}", 6000)
        except OSError as erro:
            self.statusBar().showMessage(f"Não foi possível salvar o relatório: {erro}", 6000)

    def _recarregar_historico(self) -> None:
        self._registros_historico = []
        try:
            config_path = Path(self.output_path.text().strip() or DEFAULT_CONFIG)
            if config_path.is_file():
                config = ConfiguracaoProjeto.carregar(config_path, raiz=ROOT)
                banco = config.caminho(config.analise.historico)
                if banco.is_file():
                    self._registros_historico = RepositorioHistoricoAnalises(banco, ROOT).listar(
                        limite=500
                    )
        except (OSError, TypeError, ValueError):
            self._registros_historico = []
        self._aplicar_filtro_historico()

    def _aplicar_filtro_historico(self) -> None:
        registros = filtrar_historico(
            getattr(self, "_registros_historico", []),
            self.historico_filtro.text() if hasattr(self, "historico_filtro") else "",
            int(self.historico_periodo.currentData() or 0) if hasattr(self, "historico_periodo") else None,
        )
        self.historico_tabela.setRowCount(len(registros))
        for linha, registro in enumerate(registros):
            resumo = registro.resumo if isinstance(registro.resumo, dict) else {}
            valores = (
                registro.criado_em,
                registro.regiao,
                f"{registro.periodo_inicio} a {registro.periodo_fim}",
                str(resumo.get("total_deteccoes", 0)),
                registro.report_path or "—",
            )
            for coluna, valor in enumerate(valores):
                item = QtWidgets.QTableWidgetItem(valor)
                if coluna == 0:
                    item.setData(QtCore.Qt.ItemDataRole.UserRole + 1, registro.analysis_id)
                if coluna == 4:
                    item.setData(QtCore.Qt.ItemDataRole.UserRole, registro.report_path)
                self.historico_tabela.setItem(linha, coluna, item)

    def _abrir_resultado_historico(self) -> None:
        linha = self.historico_tabela.currentRow()
        item = self.historico_tabela.item(linha, 0) if linha >= 0 else None
        analysis_id = item.data(QtCore.Qt.ItemDataRole.UserRole + 1) if item else None
        if not analysis_id:
            self.statusBar().showMessage("Selecione uma análise no histórico.", 4000)
            return
        try:
            _, raiz_analises = self._raizes_artefatos()
            self._carregar_resultado_analise(raiz_analises / str(analysis_id) / "resultado.json")
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as erro:
            QtWidgets.QMessageBox.warning(self, "Não foi possível abrir o resultado", str(erro))

    def _abrir_relatorio_historico(self, indice: QtCore.QModelIndex) -> None:
        item = self.historico_tabela.item(indice.row(), 4)
        _, raiz_analises = self._raizes_artefatos()
        caminho = self._resolver_em_raiz_opcional(
            item.data(QtCore.Qt.ItemDataRole.UserRole) if item else "",
            raiz_analises,
            {".pdf"},
        )
        if caminho and caminho.is_file():
            QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(caminho)))

    def _erro_processo(self, erro) -> None:
        if erro == QtCore.QProcess.ProcessError.FailedToStart:
            self.log.appendPlainText("\n[ERRO] Não foi possível iniciar o processo Python.")
            self._definir_execucao(False, "Falha ao iniciar")

    def _definir_execucao(self, executando: bool, mensagem: str) -> None:
        self.btn_executar.setEnabled(not executando)
        self.btn_salvar.setEnabled(not executando)
        self.btn_cancelar.setEnabled(executando)
        # A operação em andamento já recebeu uma cópia dos argumentos. Manter estes
        # controles ativos evita perda de contraste e permite preparar a próxima fila.
        self.operacao.setEnabled(True)
        self._atualizar_operacao()
        self.progresso.setRange(0, 0 if executando else 1)
        if not executando:
            self.progresso.setValue(0)
        self.status.setText(mensagem)
        self._atualizar_tarefa_painel()
        if hasattr(self, "assist_buscar"):
            self.assist_buscar.setEnabled(not executando)
            self.assist_iniciar.setEnabled(not executando)
            self.assist_cancelar.setEnabled(
                executando and self._operacao_em_execucao.startswith("assist_")
            )

    def _cancelar(self) -> None:
        if self.processo.state() == QtCore.QProcess.ProcessState.NotRunning:
            return
        self.status.setText("Cancelando…")
        self.processo.terminate()
        QtCore.QTimer.singleShot(3000, self._forcar_cancelamento)

    def _forcar_cancelamento(self) -> None:
        if self.processo.state() != QtCore.QProcess.ProcessState.NotRunning:
            self.processo.kill()

    def _receber_bbox(self, bbox: object) -> None:
        if not isinstance(bbox, list):
            self.statusBar().showMessage(
                "Desenhe uma área com \"Desenhar área\" ou Shift + arraste no mapa.", 5000
            )
            return
        try:
            oeste, sul, leste, norte = normalizar_bbox(bbox)
        except (TypeError, ValueError) as erro:
            self.statusBar().showMessage(str(erro), 5000)
            return
        self.oeste.setValue(oeste)
        self.sul.setValue(sul)
        self.leste.setValue(leste)
        self.norte.setValue(norte)
        self.statusBar().showMessage("Área do mapa aplicada à configuração.", 4000)

    def _salvar_perfil(self) -> None:
        try:
            item_id = self.store.salvar(self._coletar_dados())
            self._recarregar_perfis()
            self.statusBar().showMessage(f"Região favorita #{item_id} salva.", 5000)
        except Exception as erro:
            QtWidgets.QMessageBox.critical(self, "Erro ao salvar perfil", str(erro))

    def _recarregar_perfis(self) -> None:
        self._recarregar_favoritas()
        self.perfis.clear()
        grupos = self.store.listar_por_uf()
        if not grupos:
            vazio = QtWidgets.QTreeWidgetItem(["Nenhum preset salvo"])
            vazio.setDisabled(True)
            self.perfis.addTopLevelItem(vazio)
            return

        for uf, perfis in grupos.items():
            grupo = QtWidgets.QTreeWidgetItem([uf])
            grupo.setExpanded(True)
            for perfil in perfis:
                item = QtWidgets.QTreeWidgetItem([perfil["nome_regiao"]])
                item.setData(0, QtCore.Qt.ItemDataRole.UserRole, perfil["id"])
                grupo.addChild(item)
            self.perfis.addTopLevelItem(grupo)

    def _perfil_selecionado(self) -> dict[str, Any] | None:
        item = self.perfis.currentItem()
        if item is None:
            return None
        if item.childCount() > 0:
            item = item.child(0)
        perfil_id = item.data(0, QtCore.Qt.ItemDataRole.UserRole)
        if perfil_id is None:
            return None
        return self.store.carregar(int(perfil_id))

    def _carregar_perfil(self) -> None:
        perfil = self._perfil_selecionado()
        if perfil is None:
            self.statusBar().showMessage("Selecione um perfil para carregar.", 4000)
            return
        self._aplicar_dados(json.loads(perfil["payload"]))
        self.statusBar().showMessage(f"Perfil '{perfil['nome_regiao']}' carregado.", 5000)

    def _excluir_perfil(self) -> None:
        perfil = self._perfil_selecionado()
        if perfil is None:
            self.statusBar().showMessage("Selecione um perfil para excluir.", 4000)
            return
        resposta = QtWidgets.QMessageBox.question(
            self,
            "Excluir perfil",
            f"Excluir o perfil '{perfil['nome_regiao']}'?",
        )
        if resposta == QtWidgets.QMessageBox.StandardButton.Yes:
            self.store.excluir(int(perfil["id"]))
            self._recarregar_perfis()

    def _aplicar_dados(self, dados: dict[str, Any]) -> None:
        self.nome_regiao.setText(str(dados.get("nome_regiao", self.nome_regiao.text())))
        self.uf.setText(str(dados.get("uf", self.uf.text())))
        colecao = str(dados.get("colecao", self.colecao.text()))
        if colecao in self.colecoes_checkboxes:
            self.colecoes_checkboxes[colecao].setChecked(True)
        else:
            for caixa in self.colecoes_checkboxes.values():
                caixa.setChecked(False)
            self.colecao.setText(colecao)
        self.stac_url.setText(str(dados.get("stac_url", self.stac_url.text())))
        bandas = {str(banda).upper() for banda in dados.get("bandas", [])}
        if bandas:
            for nome, caixa in self.bandas_checkboxes.items():
                caixa.setChecked(nome in bandas)
        for campo, chave in ((self.inicio, "inicio"), (self.fim, "fim")):
            data = QtCore.QDate.fromString(str(dados.get(chave, "")), QtCore.Qt.DateFormat.ISODate)
            if data.isValid():
                campo.setDate(data)
        bbox = normalizar_bbox(dados.get("bbox", self._bbox_atual()))
        self.oeste.setValue(bbox[0])
        self.sul.setValue(bbox[1])
        self.leste.setValue(bbox[2])
        self.norte.setValue(bbox[3])
        self.mapa.exibir_bbox(bbox)
        self._atualizar_preview_yaml()

    def _bbox_atual(self) -> list[float]:
        return [self.oeste.value(), self.sul.value(), self.leste.value(), self.norte.value()]

    def _selecionar_oauth(self) -> None:
        caminho, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Selecione o JSON OAuth", str(ROOT / "config"), "Arquivos JSON (*.json)"
        )
        if caminho:
            self.oauth_json.setText(caminho)

    def _selecionar_config(self) -> None:
        caminho, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Salvar configuração", self.output_path.text(), "YAML (*.yaml *.yml)"
        )
        if caminho:
            self.output_path.setText(caminho)

    def _abrir_pasta_imagens(self) -> None:
        pasta = Path(self.pasta_download.text().strip() or "data/sentinel2").expanduser()
        if not pasta.is_absolute():
            pasta = ROOT / pasta
        pasta.mkdir(parents=True, exist_ok=True)
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(str(pasta)))

    def _abrir_imagem_preview(self) -> None:
        caminho, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Abrir preview",
            str(ROOT / (self.pasta_download.text().strip() or "data/sentinel2")),
            "Imagens (*.png *.jpg *.jpeg *.bmp)",
        )
        if not caminho:
            return
        imagem = QtGui.QPixmap(caminho)
        if imagem.isNull():
            self.statusBar().showMessage("Não foi possível carregar a imagem.", 5000)
            return
        self.imagem_preview.setPixmap(
            imagem.scaled(
                520,
                380,
                QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                QtCore.Qt.TransformationMode.SmoothTransformation,
            )
        )

    def closeEvent(self, evento: QtGui.QCloseEvent) -> None:
        if self.processo.state() != QtCore.QProcess.ProcessState.NotRunning:
            resposta = QtWidgets.QMessageBox.question(
                self, "Operação em andamento", "Cancelar a operação e sair?"
            )
            if resposta != QtWidgets.QMessageBox.StandardButton.Yes:
                evento.ignore()
                return
            self.processo.kill()
            self.processo.waitForFinished(1500)
        self._pool_painel.waitForDone(5000)
        self._lab_pool.waitForDone(10000)
        evento.accept()


def main(argv: list[str] | None = None) -> int:
    argumentos = list(sys.argv[1:] if argv is None else argv)
    smoke_test = "--smoke-test" in argumentos
    argumentos_qt = [sys.argv[0], *(arg for arg in argumentos if arg != "--smoke-test")]
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(argumentos_qt)
    app.setApplicationName("Sentinel-2 MT")
    app.setOrganizationName("Sentinel2 MT")
    configurar_aplicacao(app)
    janela = MainWindow()
    janela.show()
    if smoke_test:
        QtCore.QTimer.singleShot(1200, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
