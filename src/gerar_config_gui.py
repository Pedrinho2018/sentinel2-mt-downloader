from __future__ import annotations

import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any

import yaml
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtWebEngineWidgets import QWebEngineView

from sentinel2_mt.config_builder import gerar_config, salvar_config as persistir_config
from sentinel2_mt.gui_support import (
    LocalConfigStore,
    montar_argumentos_operacao,
    normalizar_bbox,
)
from sentinel2_mt.gui_theme import CORES, folha_estilos


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


class MapaWidget(QWebEngineView):
    areaSelecionada = QtCore.Signal(object)

    def __init__(self, bbox_inicial: list[float], parent=None) -> None:
        super().__init__(parent)
        self.bbox_inicial = bbox_inicial
        self.setMinimumHeight(460)
        self.loadFinished.connect(self._ao_carregar)
        self.setHtml(self._html())

    def _ao_carregar(self, carregou: bool) -> None:
        if carregou:
            self.exibir_bbox(self.bbox_inicial)

    def exibir_bbox(self, bbox: list[float]) -> None:
        bbox = normalizar_bbox(bbox)
        self.page().runJavaScript(f"window.setSelection({json.dumps(bbox)});")

    def capturar_bbox(self) -> None:
        def receber(valor: str | None) -> None:
            try:
                bbox = json.loads(valor) if valor else None
            except (TypeError, ValueError):
                bbox = None
            self.areaSelecionada.emit(bbox)

        self.page().runJavaScript("JSON.stringify(window.currentSelection)", receber)

    @staticmethod
    def _html() -> str:
        return """
        <!doctype html><html><head><meta charset="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
        <style>
          html, body, #map { margin: 0; width: 100%; height: 100%; background: #dfe9e5; }
          .hint { position: absolute; z-index: 900; top: 12px; left: 50%; transform: translateX(-50%);
            background: rgba(16,43,36,.92); color: white; border-radius: 8px; padding: 8px 12px;
            font: 12px sans-serif; box-shadow: 0 3px 12px rgba(0,0,0,.18); }
        </style></head><body><div id="map"></div>
        <div class="hint">Segure Shift e arraste para selecionar uma área</div>
        <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
        <script>
          const map = L.map('map', {worldCopyJump: true}).setView([-15.5, -55.0], 5);
          L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
            attribution: '&copy; OpenStreetMap contributors'
          }).addTo(map);
          let start = null;
          let rectangle = null;
          window.currentSelection = null;

          window.setSelection = function(bbox) {
            if (!bbox || bbox.length !== 4) return;
            window.currentSelection = bbox;
            const bounds = [[bbox[1], bbox[0]], [bbox[3], bbox[2]]];
            if (rectangle) map.removeLayer(rectangle);
            rectangle = L.rectangle(bounds, {color: '#168a58', weight: 2, fillOpacity: .16}).addTo(map);
            map.fitBounds(bounds, {padding: [24, 24], maxZoom: 10});
            setTimeout(() => map.invalidateSize(), 100);
          };

          map.on('mousedown', function(event) {
            if (!event.originalEvent.shiftKey) return;
            start = event.latlng;
            map.dragging.disable();
          });
          map.on('mousemove', function(event) {
            if (!start) return;
            window.setSelection([
              Math.min(start.lng, event.latlng.lng), Math.min(start.lat, event.latlng.lat),
              Math.max(start.lng, event.latlng.lng), Math.max(start.lat, event.latlng.lat)
            ]);
          });
          map.on('mouseup', function(event) {
            if (!start) return;
            window.setSelection([
              Math.min(start.lng, event.latlng.lng), Math.min(start.lat, event.latlng.lat),
              Math.max(start.lng, event.latlng.lng), Math.max(start.lat, event.latlng.lat)
            ]);
            start = null;
            map.dragging.enable();
          });
          window.addEventListener('resize', () => map.invalidateSize());
        </script></body></html>
        """


class MainWindow(QtWidgets.QMainWindow):
    PAGINAS = (
        ("Visão geral", "Execute e acompanhe as operações"),
        ("Área e período", "Escolha a região no mapa"),
        ("Dados e qualidade", "Ajuste STAC, bandas e filtros"),
        ("Google Drive", "Configure OAuth e sincronização"),
        ("Configuração", "Revise YAML e perfis locais"),
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

        self._criar_campos()
        self._montar_janela()
        self._conectar_eventos()
        self._atualizar_preview_yaml()
        self._recarregar_perfis()
        self._atualizar_operacao()

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
        self.stac_url = QtWidgets.QLineEdit("https://data.inpe.br/bdc/stac/v1/")
        self.bandas = QtWidgets.QLineEdit("B02, B03, B04, B08, NDVI")
        self.filtrar_nuvens = QtWidgets.QCheckBox("Descartar cenas com nuvens/sombra")
        self.filtrar_nuvens.setChecked(True)
        self.manter_scl = QtWidgets.QCheckBox("Manter arquivo SCL")
        self.manter_scl.setChecked(True)
        self.gerar_rgb = QtWidgets.QCheckBox("Gerar preview RGB")
        self.gerar_rgb.setChecked(True)
        self.nuvem_max_pct = self._inteiro(0, 100, 20, "%")
        self.tamanho_max_px = self._inteiro(100, 5000, 1600, " px")
        self.qualidade_jpeg = self._inteiro(1, 100, 92, "%")

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

    @staticmethod
    def _inteiro(minimo: int, maximo: int, valor: int, sufixo: str = "") -> QtWidgets.QSpinBox:
        campo = QtWidgets.QSpinBox()
        campo.setRange(minimo, maximo)
        campo.setValue(valor)
        campo.setSuffix(sufixo)
        return campo

    @staticmethod
    def _coordenada(minimo: float, maximo: float, valor: float) -> QtWidgets.QDoubleSpinBox:
        campo = QtWidgets.QDoubleSpinBox()
        campo.setRange(minimo, maximo)
        campo.setDecimals(6)
        campo.setValue(valor)
        return campo

    def _montar_janela(self) -> None:
        raiz = QtWidgets.QWidget()
        raiz.setObjectName("root")
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
        self.stack.addWidget(self._pagina_visao_geral())
        self.stack.addWidget(self._pagina_area())
        self.stack.addWidget(self._pagina_dados())
        self.stack.addWidget(self._pagina_drive())
        self.stack.addWidget(self._pagina_config())
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

    def _pagina_visao_geral(self) -> QtWidgets.QWidget:
        pagina = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(pagina)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        topo = QtWidgets.QHBoxLayout()
        operacao = Cartao("Nova operação", "A configuração é salva automaticamente antes da execução.")
        form = QtWidgets.QFormLayout()
        form.addRow("Operação", self.operacao)
        form.addRow("Máximo de cenas", self.max_execucao)
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
        mapa_card = Cartao("Área de interesse", "Navegue normalmente; use Shift + arraste para desenhar.")
        mapa_card.layout_principal.addWidget(self.mapa, 1)
        aplicar = botao("Aplicar seleção do mapa", "primaryButton")
        aplicar.clicked.connect(self.mapa.capturar_bbox)
        mapa_card.layout_principal.addWidget(aplicar)
        layout.addWidget(mapa_card, 3)

        detalhes = Cartao("Região e período", "As coordenadas usam a ordem oeste, sul, leste, norte.")
        form = QtWidgets.QFormLayout()
        form.addRow("Nome", self.nome_regiao)
        form.addRow("UF", self.uf)
        form.addRow("Data inicial", self.inicio)
        form.addRow("Data final", self.fim)
        form.addRow("Oeste", self.oeste)
        form.addRow("Sul", self.sul)
        form.addRow("Leste", self.leste)
        form.addRow("Norte", self.norte)
        detalhes.layout_principal.addLayout(form)
        salvar_perfil = botao("Salvar como perfil local")
        salvar_perfil.clicked.connect(self._salvar_perfil)
        detalhes.layout_principal.addWidget(salvar_perfil)
        detalhes.layout_principal.addStretch()
        layout.addWidget(detalhes, 1)
        return pagina

    def _pagina_dados(self) -> QtWidgets.QWidget:
        pagina = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(pagina)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)

        fonte = Cartao("Fonte STAC", "Catálogo público do INPE/Brazil Data Cube.")
        form_fonte = QtWidgets.QFormLayout()
        form_fonte.addRow("URL STAC", self.stac_url)
        form_fonte.addRow("Coleção", self.colecao)
        form_fonte.addRow("Bandas", self.bandas)
        form_fonte.addRow("Pasta de download", self.pasta_download)
        form_fonte.addRow("Catálogo CSV", self.catalogo)
        form_fonte.addRow("Timeout", self.timeout_segundos)
        form_fonte.addRow("Chunk", self.chunk_mb)
        form_fonte.addRow("Cenas padrão", self.max_itens_teste)
        form_fonte.addRow("Candidatos máximos", self.max_candidatos_teste)
        fonte.layout_principal.addLayout(form_fonte)
        fonte.layout_principal.addStretch()
        layout.addWidget(fonte, 1)

        qualidade = Cartao("Qualidade e visualização", "Filtre antes de baixar as bandas científicas maiores.")
        form_qualidade = QtWidgets.QFormLayout()
        form_qualidade.addRow(self.filtrar_nuvens)
        form_qualidade.addRow("Limite de nuvens", self.nuvem_max_pct)
        form_qualidade.addRow(self.manter_scl)
        form_qualidade.addRow(self.gerar_rgb)
        form_qualidade.addRow("Tamanho do preview", self.tamanho_max_px)
        form_qualidade.addRow("Qualidade JPEG", self.qualidade_jpeg)
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
        form.addRow("Credencial OAuth", linha_oauth)
        form.addRow("Token local", self.token_json)
        credenciais.layout_principal.addLayout(form)
        layout.addWidget(credenciais)

        destino = Cartao("Destino e lotes", "A hierarquia local de datas e cenas é preservada no Drive.")
        form_destino = QtWidgets.QFormLayout()
        form_destino.addRow("Nome da pasta remota", self.pasta_remota)
        form_destino.addRow("ID da pasta pai", self.pasta_id)
        form_destino.addRow("Tamanho do lote", self.tamanho_lote)
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
        self.operacao.currentIndexChanged.connect(self._atualizar_operacao)
        self.btn_salvar.clicked.connect(self._salvar_configuracao)
        self.btn_executar.clicked.connect(self._executar)
        self.btn_cancelar.clicked.connect(self._cancelar)

    def _navegar(self, indice: int) -> None:
        self.stack.setCurrentIndex(indice)
        self.titulo_pagina.setText(self.PAGINAS[indice][0])
        self.subtitulo_pagina.setText(self.PAGINAS[indice][1])

    def _atualizar_operacao(self) -> None:
        operacao = self.operacao.currentData()
        mensagens = {
            "catalogar": "Consulta o INPE e atualiza o catálogo CSV sem baixar GeoTIFFs.",
            "baixar": "Filtra nuvens, baixa as bandas aprovadas e gera previews RGB.",
            "sincronizar": "Envia as imagens locais ao Google Drive em lotes configuráveis.",
        }
        self.resumo_operacao.setText(mensagens[str(operacao)])
        self.max_execucao.setEnabled(operacao != "sincronizar")
        self.max_execucao.setToolTip(
            "Não se aplica à sincronização."
            if operacao == "sincronizar"
            else "Limite de cenas desta operação; zero processa todas."
        )

    def _coletar_dados(self) -> dict[str, Any]:
        bbox = normalizar_bbox(
            [self.oeste.value(), self.sul.value(), self.leste.value(), self.norte.value()]
        )
        bandas = [banda.strip() for banda in self.bandas.text().split(",") if banda.strip()]
        return {
            "bbox": bbox,
            "nome_regiao": self.nome_regiao.text().strip() or "Região personalizada",
            "uf": self.uf.text().strip().upper() or "MT",
            "colecao": self.colecao.text().strip() or "S2-16D-2",
            "stac_url": self.stac_url.text().strip() or "https://data.inpe.br/bdc/stac/v1/",
            "inicio": self.inicio.date().toString(QtCore.Qt.DateFormat.ISODate),
            "fim": self.fim.date().toString(QtCore.Qt.DateFormat.ISODate),
            "bandas": bandas or ["B02", "B03", "B04", "B08", "NDVI"],
            "filtrar_nuvens": self.filtrar_nuvens.isChecked(),
            "manter_scl": self.manter_scl.isChecked(),
            "gerar_rgb": self.gerar_rgb.isChecked(),
            "nuvem_max_pct": self.nuvem_max_pct.value(),
            "pasta_download": self.pasta_download.text().strip() or "data/sentinel2",
            "catalogo": self.catalogo.text().strip() or "catalogo/catalogo_imagens.csv",
            "tamanho_max_px": self.tamanho_max_px.value(),
            "qualidade_jpeg": self.qualidade_jpeg.value(),
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
            )
        except (FileNotFoundError, TypeError, ValueError) as erro:
            QtWidgets.QMessageBox.warning(self, "Não foi possível executar", str(erro))
            return

        self.log.clear()
        programa, argumentos_processo, diretorio = comando_cli_empacotado(argumentos)
        self.log.appendPlainText(
            f"$ {shlex.join([programa, *argumentos_processo])}\n"
        )
        self._definir_execucao(True, "Executando")
        self.processo.setWorkingDirectory(str(diretorio))
        self.processo.start(programa, argumentos_processo)

    def _ler_saida(self) -> None:
        texto = bytes(self.processo.readAllStandardOutput()).decode("utf-8", errors="replace")
        if texto:
            cursor = self.log.textCursor()
            cursor.movePosition(QtGui.QTextCursor.MoveOperation.End)
            cursor.insertText(texto)
            self.log.setTextCursor(cursor)
            self.log.ensureCursorVisible()

    def _processo_finalizado(self, codigo: int, _status) -> None:
        mensagem = "Concluído" if codigo == 0 else f"Encerrado com código {codigo}"
        self.log.appendPlainText(f"\n[{mensagem}]")
        self._definir_execucao(False, mensagem)

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
            self.statusBar().showMessage("Desenhe uma área com Shift + arraste no mapa.", 5000)
            return
        try:
            oeste, sul, leste, norte = normalizar_bbox(bbox)
        except ValueError as erro:
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
            self.statusBar().showMessage(f"Perfil local #{item_id} salvo.", 5000)
        except Exception as erro:
            QtWidgets.QMessageBox.critical(self, "Erro ao salvar perfil", str(erro))

    def _recarregar_perfis(self) -> None:
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
        self.colecao.setText(str(dados.get("colecao", self.colecao.text())))
        self.stac_url.setText(str(dados.get("stac_url", self.stac_url.text())))
        self.bandas.setText(", ".join(dados.get("bandas", [])) or self.bandas.text())
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
