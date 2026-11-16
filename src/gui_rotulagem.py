"""Painel de rotulagem do Laboratório de IA (PySide6).

Mantido fora de `gerar_config_gui.py` para não aumentar a janela principal. A
lógica fica em `sentinel2_mt.laboratorio`; aqui só há coleta de ações e exibição.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from PySide6 import QtCore, QtGui, QtWidgets

from sentinel2_mt.laboratorio.projetos import RepositorioLaboratorio
from sentinel2_mt.laboratorio.rotulagem import (
    importar_anotacoes,
    localizar_anotadores,
    lote_atual,
    preparar_lote,
)


def _botao(texto: str, tipo: str = "secondaryButton") -> QtWidgets.QPushButton:
    componente = QtWidgets.QPushButton(texto)
    componente.setObjectName(tipo)
    componente.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
    return componente


# (config, pasta do laboratório, repositório) ou None
Contexto = Callable[[], "tuple[object, Path, RepositorioLaboratorio] | None"]


class PainelRotulagem(QtWidgets.QWidget):
    """Ações de rotulagem conforme o tipo do projeto selecionado."""

    rotulosAlterados = QtCore.Signal(str)

    def __init__(
        self,
        contexto: Contexto,
        projeto_id: Callable[[], int | None],
        selecionadas: Callable[[], list[str]],
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._contexto = contexto
        self._projeto_id = projeto_id
        self._selecionadas = selecionadas
        self._tipo = ""
        # Permite substituir a abertura de processos e pastas nos testes.
        self.iniciar_processo = QtCore.QProcess.startDetached
        self.abrir_url = QtGui.QDesktopServices.openUrl

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.pilha = QtWidgets.QStackedWidget()
        self.pilha.addWidget(self._painel_vazio())
        self.pilha.addWidget(self._painel_deteccao())
        self.pilha.addWidget(self._painel_classificacao())
        layout.addWidget(self.pilha)

    # ------------------------------------------------------------------ montagem

    def _painel_vazio(self) -> QtWidgets.QWidget:
        rotulo = QtWidgets.QLabel("Selecione um projeto para rotular.")
        rotulo.setObjectName("cardHelp")
        return rotulo

    def _painel_deteccao(self) -> QtWidgets.QWidget:
        painel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(painel)
        layout.setContentsMargins(0, 0, 0, 0)
        explicacao = QtWidgets.QLabel(
            "Detecção: desenhe caixas em um anotador externo que grave YOLO (.txt). "
            "1) Prepare um lote; 2) abra o anotador; 3) importe as anotações para validar."
        )
        explicacao.setObjectName("cardHelp")
        explicacao.setWordWrap(True)
        layout.addWidget(explicacao)
        linha = QtWidgets.QHBoxLayout()
        self.lote_tamanho = QtWidgets.QSpinBox()
        self.lote_tamanho.setRange(1, 2000)
        self.lote_tamanho.setValue(100)
        self.lote_tamanho.setSuffix(" imagens")
        self.lote_revisao = QtWidgets.QCheckBox("Incluir já rotuladas (revisão)")
        self.lote_selecionadas = QtWidgets.QCheckBox("Somente as selecionadas")
        self.btn_preparar = _botao("Preparar lote", "primaryButton")
        self.btn_preparar.clicked.connect(self.preparar_lote)
        linha.addWidget(self.lote_tamanho)
        linha.addWidget(self.lote_revisao)
        linha.addWidget(self.lote_selecionadas)
        linha.addStretch()
        linha.addWidget(self.btn_preparar)
        layout.addLayout(linha)
        linha2 = QtWidgets.QHBoxLayout()
        self.btn_anotador = _botao("Abrir no anotador")
        self.btn_anotador.clicked.connect(self.abrir_anotador)
        self.btn_pasta = _botao("Abrir pasta do lote")
        self.btn_pasta.clicked.connect(self.abrir_pasta)
        self.btn_importar = _botao("Importar anotações")
        self.btn_importar.clicked.connect(self.importar)
        for item in (self.btn_anotador, self.btn_pasta, self.btn_importar):
            linha2.addWidget(item)
        linha2.addStretch()
        layout.addLayout(linha2)
        self.estado_deteccao = QtWidgets.QLabel("")
        self.estado_deteccao.setWordWrap(True)
        self.estado_deteccao.setObjectName("cardHelp")
        layout.addWidget(self.estado_deteccao)
        return painel

    def _painel_classificacao(self) -> QtWidgets.QWidget:
        painel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(painel)
        layout.setContentsMargins(0, 0, 0, 0)
        explicacao = QtWidgets.QLabel(
            "Classificação: selecione uma ou mais imagens na grade (Ctrl/Shift para várias) "
            "e atribua a classe. Não é preciso desenhar caixas."
        )
        explicacao.setObjectName("cardHelp")
        explicacao.setWordWrap(True)
        layout.addWidget(explicacao)
        linha = QtWidgets.QHBoxLayout()
        self.classe = QtWidgets.QComboBox()
        self.classe.setMinimumWidth(160)
        self.btn_aplicar = _botao("Aplicar às selecionadas", "primaryButton")
        self.btn_aplicar.clicked.connect(self.aplicar_classe)
        self.btn_limpar = _botao("Limpar rótulo")
        self.btn_limpar.clicked.connect(self.limpar_rotulo)
        linha.addWidget(QtWidgets.QLabel("Classe"))
        linha.addWidget(self.classe)
        linha.addWidget(self.btn_aplicar)
        linha.addWidget(self.btn_limpar)
        linha.addStretch()
        layout.addLayout(linha)
        self.estado_classificacao = QtWidgets.QLabel("")
        self.estado_classificacao.setObjectName("cardHelp")
        self.estado_classificacao.setWordWrap(True)
        layout.addWidget(self.estado_classificacao)
        return painel

    # ------------------------------------------------------------------ estado

    def atualizar(self) -> None:
        """Ajusta o painel ao tipo e ao esquema do projeto atual."""
        projeto_id = self._projeto_id()
        contexto = self._contexto() if projeto_id is not None else None
        if contexto is None or projeto_id is None:
            self._tipo = ""
            self.pilha.setCurrentIndex(0)
            return
        _config, pasta, repo = contexto
        projeto = repo.obter_projeto(projeto_id)
        esquema = repo.esquema_atual(projeto_id)
        self._tipo = projeto.tipo
        if projeto.tipo == "deteccao":
            self.pilha.setCurrentIndex(1)
            lote = lote_atual(pasta, projeto_id)
            anotadores = localizar_anotadores(lote)
            self.btn_anotador.setEnabled(lote is not None and bool(anotadores))
            self.btn_pasta.setEnabled(lote is not None)
            self.btn_importar.setEnabled(lote is not None)
            if anotadores:
                self.btn_anotador.setText(f"Abrir no {anotadores[0].nome}")
                self.btn_anotador.setToolTip(anotadores[0].instrucao)
            else:
                self.btn_anotador.setText("Abrir no anotador")
                self.btn_anotador.setToolTip(
                    "Nenhum anotador encontrado no PATH. Instale um anotador YOLO fora deste "
                    "ambiente (por exemplo, `pipx install labelImg`) ou use 'Abrir pasta do lote'."
                )
            if lote is None:
                texto = "Nenhum lote preparado."
            elif lote.versao_esquema != esquema.versao:
                texto = (
                    f"O lote atual usa o esquema v{lote.versao_esquema}, mas o projeto está na "
                    f"v{esquema.versao}. Prepare um novo lote."
                )
            else:
                texto = f"Lote atual: {lote.quantidade} imagem(ns), esquema v{lote.versao_esquema}. Pasta: {lote.pasta}"
            if not anotadores:
                texto += " Nenhum anotador encontrado no PATH (veja a dica em 'Abrir no anotador')."
            self.estado_deteccao.setText(texto)
        else:
            self.pilha.setCurrentIndex(2)
            atual = self.classe.currentText()
            self.classe.clear()
            self.classe.addItems(list(esquema.classes))
            indice = self.classe.findText(atual)
            if indice >= 0:
                self.classe.setCurrentIndex(indice)

    # ------------------------------------------------------------------ detecção

    def preparar_lote(self) -> None:
        projeto_id = self._projeto_id()
        contexto = self._contexto()
        if projeto_id is None or contexto is None:
            return
        config, pasta, repo = contexto
        estados = ("pendente", "revisar", "rotulada") if self.lote_revisao.isChecked() else ("pendente", "revisar")
        selecionadas = self._selecionadas() if self.lote_selecionadas.isChecked() else None
        try:
            lote = preparar_lote(
                repo, projeto_id, config.raiz, pasta,
                estados=estados, patch_ids=selecionadas, limite=self.lote_tamanho.value(),
            )
        except (OSError, ValueError) as erro:
            QtWidgets.QMessageBox.warning(self, "Não foi possível preparar o lote", str(erro))
            return
        # Atualiza a página antes de escrever a mensagem, para não sobrescrevê-la.
        self.rotulosAlterados.emit("lote")
        self.atualizar()
        self.estado_deteccao.setText(
            f"Lote com {lote.quantidade} imagem(ns) preparado em {lote.pasta}. "
            "As imagens são cópias; os arquivos do pipeline não são abertos pelo anotador."
        )

    def abrir_anotador(self) -> None:
        projeto_id = self._projeto_id()
        contexto = self._contexto()
        if projeto_id is None or contexto is None:
            return
        lote = lote_atual(contexto[1], projeto_id)
        anotadores = localizar_anotadores(lote)
        if lote is None or not anotadores:
            return
        anotador = anotadores[0]
        iniciado = self.iniciar_processo(anotador.executavel, list(anotador.argumentos), str(lote.pasta))
        if isinstance(iniciado, tuple):
            iniciado = iniciado[0]
        if iniciado:
            self.estado_deteccao.setText(
                f"{anotador.nome} aberto. {anotador.instrucao} Ao terminar, clique em 'Importar anotações'."
            )
        else:
            QtWidgets.QMessageBox.warning(self, "Anotador", f"Não foi possível iniciar o {anotador.nome}.")

    def abrir_pasta(self) -> None:
        projeto_id = self._projeto_id()
        contexto = self._contexto()
        if projeto_id is None or contexto is None:
            return
        lote = lote_atual(contexto[1], projeto_id)
        if lote is not None:
            self.abrir_url(QtCore.QUrl.fromLocalFile(str(lote.pasta)))

    def importar(self) -> None:
        projeto_id = self._projeto_id()
        contexto = self._contexto()
        if projeto_id is None or contexto is None:
            return
        try:
            resumo = importar_anotacoes(contexto[2], projeto_id, contexto[1])
        except (OSError, ValueError) as erro:
            QtWidgets.QMessageBox.warning(self, "Não foi possível importar", str(erro))
            return
        self.rotulosAlterados.emit("deteccao")
        partes = [
            f"{resumo.rotuladas} imagem(ns) rotulada(s)",
            f"{resumo.com_erros} com erro (marcadas para revisar)",
            f"{resumo.sem_anotacao} ainda sem anotação",
        ]
        if resumo.imagem_alterada:
            partes.append(f"{resumo.imagem_alterada} com imagem do lote alterada (ignoradas)")
        texto = "Importação: " + "; ".join(partes) + "."
        if resumo.avisos:
            texto += " Aviso: " + " ".join(resumo.avisos)
        if resumo.erros_por_imagem:
            exemplo_id, exemplo = next(iter(resumo.erros_por_imagem.items()))
            texto += f" Exemplo ({exemplo_id[-24:]}): {exemplo[0]}."
        self.estado_deteccao.setText(texto)

    # ------------------------------------------------------------------ classificação

    def aplicar_classe(self) -> None:
        projeto_id = self._projeto_id()
        contexto = self._contexto()
        selecionadas = self._selecionadas()
        if projeto_id is None or contexto is None:
            return
        if not selecionadas:
            self.estado_classificacao.setText("Selecione ao menos uma imagem na grade.")
            return
        classe = self.classe.currentText()
        try:
            quantidade = contexto[2].rotular_classificacao(projeto_id, selecionadas, classe)
        except ValueError as erro:
            QtWidgets.QMessageBox.warning(self, "Não foi possível rotular", str(erro))
            return
        self.estado_classificacao.setText(f"Classe '{classe}' atribuída a {quantidade} imagem(ns).")
        self.rotulosAlterados.emit("classificacao")

    def limpar_rotulo(self) -> None:
        projeto_id = self._projeto_id()
        contexto = self._contexto()
        selecionadas = self._selecionadas()
        if projeto_id is None or contexto is None or not selecionadas:
            return
        contexto[2].limpar_rotulos(projeto_id, selecionadas)
        self.estado_classificacao.setText(f"{len(selecionadas)} imagem(ns) voltaram para pendente.")
        self.rotulosAlterados.emit("classificacao")
