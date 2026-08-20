from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import yaml
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtWebEngineWidgets import QWebEngineView

from sentinel2_mt.config_builder import gerar_config, salvar_config

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "config.yaml"


class MapaWidget(QWebEngineView):
    areaSelecionada = QtCore.Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._selection = None
        self.setMinimumHeight(420)
        self.loadFinished.connect(self._on_load_finished)
        self.setHtml(self._html_do_mapa())

    def _on_load_finished(self, ok):
        if not ok:
            return
        self.page().runJavaScript("""
            if (typeof window.map !== 'undefined') {
                setTimeout(function() { window.map.invalidateSize(); }, 250);
                setTimeout(function() { window.map.invalidateSize(); }, 1000);
            }
        """)

    def _html_do_mapa(self) -> str:
        return """
        <!doctype html>
        <html>
        <head>
          <meta charset="utf-8" />
          <meta name="viewport" content="width=device-width, initial-scale=1.0" />
          <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
          <style>
            html, body {
              margin: 0;
              width: 100%;
              height: 100vh;
              min-height: 420px;
            }
            #map {
              width: 100%;
              height: 100vh;
              min-height: 420px;
              background: #dfeaf5;
            }
          </style>
        </head>
        <body>
          <div id="map"></div>
          <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
          <script>
            const defaultCenter = [-15.5, -55.0];
            const map = L.map('map', { zoomControl: true, attributionControl: true, worldCopyJump: true }).setView(defaultCenter, 5);
            window.map = map;
            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
              attribution: '&copy; OpenStreetMap contributors'
            }).addTo(map);
            map.whenReady(function() {
              setTimeout(function() { map.invalidateSize(); }, 200);
              setTimeout(function() { map.invalidateSize(); }, 800);
            });
            window.addEventListener('resize', function() { map.invalidateSize(); });

            let startLatlng = null;
            let rectangle = null;
            window.currentSelection = null;

            function updateSelection(sw, ne) {
              const bbox = [
                Math.min(sw.lng, ne.lng),
                Math.min(sw.lat, ne.lat),
                Math.max(sw.lng, ne.lng),
                Math.max(sw.lat, ne.lat)
              ];
              window.currentSelection = bbox;
            }

            function drawRectangleFromPoints(a, b) {
              const sw = { lat: Math.min(a.lat, b.lat), lng: Math.min(a.lng, b.lng) };
              const ne = { lat: Math.max(a.lat, b.lat), lng: Math.max(a.lng, b.lng) };
              if (rectangle) {
                map.removeLayer(rectangle);
              }
              rectangle = L.rectangle([[sw.lat, sw.lng], [ne.lat, ne.lng]], {
                color: '#d32f2f',
                weight: 2,
                fillOpacity: 0.2
              }).addTo(map);
              updateSelection(sw, ne);
            }

            map.on('mousedown', (e) => {
              startLatlng = e.latlng;
            });

            map.on('mousemove', (e) => {
              if (!startLatlng) return;
              drawRectangleFromPoints(startLatlng, e.latlng);
            });

            map.on('mouseup', (e) => {
              if (!startLatlng) return;
              drawRectangleFromPoints(startLatlng, e.latlng);
              startLatlng = null;
            });
          </script>
        </body>
        </html>
        """

    def capturar_bbox(self) -> None:
        def _callback(valor):
            try:
                payload = json.loads(valor) if valor else None
            except (TypeError, ValueError):
                payload = None
            if payload:
                self.areaSelecionada.emit(payload)
            else:
                self.areaSelecionada.emit(None)

        self.page().runJavaScript("JSON.stringify(window.currentSelection)", _callback)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Gerador de config.yaml - Sentinel-2 MT")
        self.resize(1400, 900)

        self.mapa = MapaWidget(self)
        self.mapa.areaSelecionada.connect(self._receber_bbox_do_mapa)

        self.nome_regiao = QtWidgets.QLineEdit("Mato Grosso")
        self.uf = QtWidgets.QLineEdit("MT")
        self.colecao = QtWidgets.QLineEdit("S2-16D-2")
        self.stac_url = QtWidgets.QLineEdit("https://data.inpe.br/bdc/stac/v1/")
        self.inicio = QtWidgets.QLineEdit("2025-09-01")
        self.fim = QtWidgets.QLineEdit("2026-04-30")
        self.bandas = QtWidgets.QLineEdit("B02, B03, B04, B08, NDVI")
        self.filtrar_nuvens = QtWidgets.QCheckBox("Filtrar nuvens/sombra")
        self.filtrar_nuvens.setChecked(True)
        self.manter_scl = QtWidgets.QCheckBox("Manter SCL")
        self.manter_scl.setChecked(True)
        self.gerar_rgb = QtWidgets.QCheckBox("Gerar preview RGB")
        self.gerar_rgb.setChecked(True)
        self.nuvem_max_pct = QtWidgets.QSpinBox(); self.nuvem_max_pct.setRange(0, 100); self.nuvem_max_pct.setValue(20)
        self.pasta_download = QtWidgets.QLineEdit("data/sentinel2")
        self.catalogo = QtWidgets.QLineEdit("catalogo/catalogo_imagens.csv")
        self.output_path = QtWidgets.QLineEdit(str(DEFAULT_CONFIG))
        self.oauth_json = QtWidgets.QLineEdit("${GOOGLE_OAUTH_JSON:-}")
        self.token_json = QtWidgets.QLineEdit("${GOOGLE_TOKEN_JSON:-config/google-token.json}")
        self.pasta_remota = QtWidgets.QLineEdit("sentinel2-mt")
        self.pasta_id = QtWidgets.QLineEdit("${GOOGLE_PASTA_ID:-root}")
        self.btn_oauth = QtWidgets.QPushButton("Selecionar JSON OAuth")
        self.btn_oauth.clicked.connect(self.selecionar_oauth)
        self.btn_imagem = QtWidgets.QPushButton("Abrir imagem de preview")
        self.btn_imagem.clicked.connect(self.abrir_imagem_preview)
        self.tamanho_lote = QtWidgets.QSpinBox(); self.tamanho_lote.setRange(1, 1000); self.tamanho_lote.setValue(100)
        self.tamanho_max_px = QtWidgets.QSpinBox(); self.tamanho_max_px.setRange(100, 5000); self.tamanho_max_px.setValue(1600)
        self.qualidade_jpeg = QtWidgets.QSpinBox(); self.qualidade_jpeg.setRange(1, 100); self.qualidade_jpeg.setValue(92)
        self.timeout_segundos = QtWidgets.QSpinBox(); self.timeout_segundos.setRange(30, 600); self.timeout_segundos.setValue(120)
        self.chunk_mb = QtWidgets.QSpinBox(); self.chunk_mb.setRange(1, 50); self.chunk_mb.setValue(1)
        self.max_itens_teste = QtWidgets.QSpinBox(); self.max_itens_teste.setRange(0, 1000); self.max_itens_teste.setValue(5)
        self.max_candidatos_teste = QtWidgets.QSpinBox(); self.max_candidatos_teste.setRange(1, 1000); self.max_candidatos_teste.setValue(40)

        self.oeste = QtWidgets.QDoubleSpinBox(); self.oeste.setRange(-180, 180); self.oeste.setValue(-61.65)
        self.sul = QtWidgets.QDoubleSpinBox(); self.sul.setRange(-90, 90); self.sul.setValue(-18.05)
        self.leste = QtWidgets.QDoubleSpinBox(); self.leste.setRange(-180, 180); self.leste.setValue(-50.20)
        self.norte = QtWidgets.QDoubleSpinBox(); self.norte.setRange(-90, 90); self.norte.setValue(-7.30)

        self.preview = QtWidgets.QPlainTextEdit(); self.preview.setReadOnly(True)
        self.btn_usar_mapa = QtWidgets.QPushButton("Usar área selecionada no mapa")
        self.btn_usar_mapa.clicked.connect(self.mapa.capturar_bbox)
        self.btn_gerar = QtWidgets.QPushButton("Gerar config.yaml")
        self.btn_gerar.clicked.connect(self.gerar_config)

        form = QtWidgets.QFormLayout()
        form.addRow("Nome da região:", self.nome_regiao)
        form.addRow("UF:", self.uf)
        form.addRow("Coleção STAC:", self.colecao)
        form.addRow("URL STAC:", self.stac_url)
        form.addRow("Data inicial:", self.inicio)
        form.addRow("Data final:", self.fim)
        form.addRow("Bandas:", self.bandas)
        form.addRow("Filtrar nuvens:", self.filtrar_nuvens)
        form.addRow("Manter SCL:", self.manter_scl)
        form.addRow("Gerar preview RGB:", self.gerar_rgb)
        form.addRow("Máximo de nuvens (%):", self.nuvem_max_pct)
        form.addRow("Pasta de download:", self.pasta_download)
        form.addRow("Catalogo:", self.catalogo)
        form.addRow("Arquivo de saída:", self.output_path)
        form.addRow("JSON OAuth:", self.oauth_json)
        form.addRow("", self.btn_oauth)
        form.addRow("Token do Drive:", self.token_json)
        form.addRow("Pasta remota:", self.pasta_remota)
        form.addRow("Pasta ID:", self.pasta_id)
        form.addRow("Tamanho do lote:", self.tamanho_lote)
        form.addRow("Tamanho preview (px):", self.tamanho_max_px)
        form.addRow("Qualidade JPEG:", self.qualidade_jpeg)
        form.addRow("Timeout (s):", self.timeout_segundos)
        form.addRow("Chunk MB:", self.chunk_mb)
        form.addRow("Max itens teste:", self.max_itens_teste)
        form.addRow("Max candidatos:", self.max_candidatos_teste)
        form.addRow("Oeste (lon):", self.oeste)
        form.addRow("Sul (lat):", self.sul)
        form.addRow("Leste (lon):", self.leste)
        form.addRow("Norte (lat):", self.norte)

        left_panel = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_panel)
        left_layout.addLayout(form)
        left_layout.addWidget(self.btn_usar_mapa)
        left_layout.addWidget(self.btn_gerar)

        self.imagem_preview = QtWidgets.QLabel("Preview da imagem será exibida aqui")
        self.imagem_preview.setAlignment(QtCore.Qt.AlignCenter)
        self.imagem_preview.setMinimumHeight(240)
        self.imagem_preview.setStyleSheet("border: 1px solid #b0b0b0; background: #f5f5f5; color: #444; padding: 8px;")

        right_panel = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_panel)
        right_layout.addWidget(self.mapa)
        right_layout.addWidget(self.preview)
        right_layout.addWidget(self.btn_imagem)
        right_layout.addWidget(self.imagem_preview)

        split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        split.addWidget(left_panel)
        split.addWidget(right_panel)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)

        self.setCentralWidget(split)

    def selecionar_oauth(self):
        caminho, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Selecione o arquivo JSON OAuth",
            "",
            "Arquivos JSON (*.json)",
        )
        if caminho:
            self.oauth_json.setText(caminho)

    def abrir_imagem_preview(self):
        caminho, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Selecione a imagem para visualizar",
            str(Path(self.pasta_download.text().strip() or "data/sentinel2").resolve()),
            "Imagens (*.png *.jpg *.jpeg *.tif *.tiff *.bmp)",
        )
        if not caminho:
            return
        self._mostrar_imagem(caminho)

    def _mostrar_imagem(self, caminho: str):
        pixmap = QtGui.QPixmap(caminho)
        if pixmap.isNull():
            self.imagem_preview.setText(f"Não foi possível carregar a imagem:\n{caminho}")
            return
        escala = min(1.0, 680 / max(pixmap.width(), 1))
        pixmap = pixmap.scaled(int(pixmap.width() * escala), int(pixmap.height() * escala), QtCore.Qt.KeepAspectRatio)
        self.imagem_preview.setPixmap(pixmap)

    def _receber_bbox_do_mapa(self, bbox):
        if not bbox or len(bbox) != 4:
            return
        self.oeste.setValue(float(bbox[0]))
        self.sul.setValue(float(bbox[1]))
        self.leste.setValue(float(bbox[2]))
        self.norte.setValue(float(bbox[3]))
        self.preview.setPlainText(
            "Área selecionada no mapa:\n"
            f"oeste={bbox[0]}, sul={bbox[1]}, leste={bbox[2]}, norte={bbox[3]}"
        )

    def coletar_dados(self) -> dict[str, Any]:
        bandas = [item.strip() for item in self.bandas.text().split(",") if item.strip()]
        return {
            "bbox": [self.oeste.value(), self.sul.value(), self.leste.value(), self.norte.value()],
            "nome_regiao": self.nome_regiao.text().strip() or "Região personalizada",
            "uf": self.uf.text().strip() or "MT",
            "colecao": self.colecao.text().strip() or "S2-16D-2",
            "stac_url": self.stac_url.text().strip() or "https://data.inpe.br/bdc/stac/v1/",
            "inicio": self.inicio.text().strip() or "2025-09-01",
            "fim": self.fim.text().strip() or "2026-04-30",
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
            "token_json": self.token_json.text().strip() or "${GOOGLE_TOKEN_JSON:-config/google-token.json}",
            "pasta_id": self.pasta_id.text().strip() or "${GOOGLE_PASTA_ID:-root}",
            "tamanho_lote": self.tamanho_lote.value(),
            "extensoes": [".tif", ".tiff", ".jpg", ".jpeg"],
        }

    def gerar_config(self):
        try:
            dados = self.coletar_dados()
            payload = gerar_config(dados)
            caminho = self.output_path.text().strip() or str(DEFAULT_CONFIG)
            destino = salvar_config(caminho, dados)
            self.preview.setPlainText(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))
            QtWidgets.QMessageBox.information(self, "Configuração salva", f"Arquivo gerado em:\n{destino}")
        except Exception as exc:  # pragma: no cover
            QtWidgets.QMessageBox.critical(self, "Erro", str(exc))


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
