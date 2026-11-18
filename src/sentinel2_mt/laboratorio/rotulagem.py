"""Rotulagem do Laboratório de IA.

Detecção: a aplicação prepara um *lote* (pasta com cópias dos PNGs, `classes.txt`
do esquema e anotações existentes para revisão), o usuário anota em uma
ferramenta externa que grave YOLO `.txt` (LabelImg, X-AnyLabeling ou outra) e a
aplicação importa e valida cada arquivo. O código da ferramenta não é alterado.

Classificação: não usa caixas; a classe é atribuída por imagem na própria
aplicação (ver `RepositorioLaboratorio.rotular_classificacao`).

Nenhum rótulo é inventado: imagens sem `.txt` continuam pendentes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import shutil

from .datasets import sha256_arquivo
from .projetos import ImagemDataset, RepositorioLaboratorio


LIMITE_LINHAS_ANOTACAO = 1000
LIMITE_BYTES_ANOTACAO = 256 * 1024
TOLERANCIA = 1e-6
ESTADOS_LOTE_PADRAO = ("pendente", "revisar")


# --------------------------------------------------------------------------- validação


@dataclass(frozen=True)
class ResultadoValidacao:
    caixas: int
    erros: tuple[str, ...]
    por_classe: dict[int, int] = field(default_factory=dict)

    @property
    def valido(self) -> bool:
        return not self.erros


def validar_anotacao_yolo(texto: str, quantidade_classes: int) -> ResultadoValidacao:
    """Valida um `.txt` YOLO de detecção: `classe cx cy largura altura` por linha.

    Regras: ID inteiro dentro do esquema; coordenadas normalizadas em [0, 1];
    largura e altura positivas; caixa inteira dentro da imagem. Arquivo vazio é
    válido e significa "imagem rotulada sem talhões".
    """
    erros: list[str] = []
    por_classe: dict[int, int] = {}
    linhas = texto.splitlines()
    if len(linhas) > LIMITE_LINHAS_ANOTACAO:
        return ResultadoValidacao(0, (f"mais de {LIMITE_LINHAS_ANOTACAO} linhas",))
    vistas: set[tuple] = set()
    caixas = 0
    for numero, linha in enumerate(linhas, start=1):
        if not linha.strip():
            continue
        partes = linha.split()
        if len(partes) != 5:
            erros.append(f"linha {numero}: esperados 5 valores (classe cx cy largura altura), encontrados {len(partes)}")
            continue
        try:
            # Aceita "0" e também "0.0", que algumas ferramentas gravam e o Ultralytics lê.
            bruto = float(partes[0])
            if not math.isfinite(bruto) or not bruto.is_integer():
                raise ValueError
            classe = int(bruto)
        except ValueError:
            erros.append(f"linha {numero}: ID de classe '{partes[0]}' não é inteiro")
            continue
        if not 0 <= classe < quantidade_classes:
            erros.append(
                f"linha {numero}: classe {classe} fora do esquema (IDs válidos: 0 a {quantidade_classes - 1})"
            )
            continue
        try:
            cx, cy, largura, altura = (float(valor) for valor in partes[1:])
        except ValueError:
            erros.append(f"linha {numero}: coordenadas devem ser números")
            continue
        if not all(math.isfinite(v) for v in (cx, cy, largura, altura)):
            erros.append(f"linha {numero}: coordenadas não finitas")
            continue
        if not all(-TOLERANCIA <= v <= 1 + TOLERANCIA for v in (cx, cy, largura, altura)):
            erros.append(f"linha {numero}: coordenadas devem estar normalizadas entre 0 e 1")
            continue
        if largura <= 0 or altura <= 0:
            erros.append(f"linha {numero}: largura e altura devem ser maiores que zero")
            continue
        if (
            cx - largura / 2 < -TOLERANCIA
            or cx + largura / 2 > 1 + TOLERANCIA
            or cy - altura / 2 < -TOLERANCIA
            or cy + altura / 2 > 1 + TOLERANCIA
        ):
            erros.append(f"linha {numero}: caixa ultrapassa a borda da imagem")
            continue
        chave = (classe, round(cx, 6), round(cy, 6), round(largura, 6), round(altura, 6))
        if chave in vistas:
            erros.append(f"linha {numero}: caixa duplicada")
            continue
        vistas.add(chave)
        caixas += 1
        por_classe[classe] = por_classe.get(classe, 0) + 1
    return ResultadoValidacao(caixas, tuple(erros), por_classe)


# --------------------------------------------------------------------------- lote


@dataclass(frozen=True)
class Lote:
    pasta: Path
    imagens: Path
    rotulos: Path
    classes: Path
    manifesto: Path
    quantidade: int
    versao_esquema: int


def pasta_lotes(pasta_laboratorio: Path, projeto_id: int) -> Path:
    return pasta_laboratorio / "projetos" / str(int(projeto_id)) / "rotulagem"


class LoteComPendencias(ValueError):
    """O lote atual tem anotações não importadas (ou diferentes do banco)."""


def pendencias_do_lote(repo: RepositorioLaboratorio, projeto_id: int, pasta: Path) -> list[str]:
    """Arquivos .txt do lote que não estão no banco ou diferem do que foi importado."""
    rotulos = pasta / "labels"
    if not rotulos.is_dir():
        return []
    anotacoes = repo.anotacoes_deteccao(projeto_id)
    arquivadas = {i.patch_id for i in repo.listar_imagens(projeto_id) if i.arquivada}
    pendentes = []
    for txt in sorted(rotulos.glob("*.txt")):
        if txt.name == "classes.txt" or txt.stem in arquivadas:
            continue
        conteudo = txt.read_text(encoding="utf-8", errors="replace")
        importada = anotacoes.get(txt.stem)
        if importada is None:
            if conteudo.strip():
                pendentes.append(txt.name)
        elif conteudo != importada["conteudo"]:
            pendentes.append(txt.name)
    return pendentes


def arquivar_lote_atual(repo: RepositorioLaboratorio, projeto_id: int, pasta_laboratorio: Path) -> Path | None:
    """Move o `lote-atual` para `lotes-anteriores/<data>` (nunca apaga).

    Recusa com LoteComPendencias se houver anotação .txt não importada ou
    alterada depois da importação: importe (ou revise) antes de trocar o lote.
    """
    base = pasta_lotes(pasta_laboratorio, projeto_id)
    pasta = base / "lote-atual"
    if not pasta.exists():
        return None
    pendentes = pendencias_do_lote(repo, projeto_id, pasta)
    if pendentes:
        raise LoteComPendencias(
            f"O lote atual tem {len(pendentes)} anotação(ões) não importada(s) ou alterada(s) "
            f"(ex.: {pendentes[0]}). Importe as anotações antes de preparar outro lote."
        )
    destino = base / "lotes-anteriores" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    sufixo = 1
    while destino.exists():
        destino = destino.with_name(f"{destino.name.split('-')[0]}-{sufixo}")
        sufixo += 1
    destino.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(pasta), str(destino))
    return destino


def preparar_lote(
    repo: RepositorioLaboratorio,
    projeto_id: int,
    raiz: Path,
    pasta_laboratorio: Path,
    *,
    estados: tuple[str, ...] = ESTADOS_LOTE_PADRAO,
    patch_ids: list[str] | None = None,
    limite: int = 200,
) -> Lote:
    """Cria a pasta do lote para um anotador externo (somente detecção).

    As imagens são **copiadas** como `<patch_id>.png`, para que nenhuma
    ferramenta externa tenha acesso aos arquivos do pipeline. Anotações já
    importadas são gravadas em `labels/` para revisão e correção.
    """
    projeto = repo.obter_projeto(projeto_id)
    if projeto.tipo != "deteccao":
        raise ValueError("Lotes para anotador externo são usados apenas em projetos de detecção.")
    if limite <= 0:
        raise ValueError("O lote precisa ter ao menos uma imagem.")
    esquema = repo.esquema_atual(projeto_id)
    imagens = repo.listar_imagens(projeto_id, incluir_arquivadas=False)
    if patch_ids is not None:
        escolhidas = set(patch_ids)
        candidatas = [imagem for imagem in imagens if imagem.patch_id in escolhidas]
    else:
        candidatas = [imagem for imagem in imagens if imagem.estado_rotulo in estados]
    candidatas = [
        imagem
        for imagem in candidatas
        if imagem.triagem != "descartar" and not imagem.duplicata_de and imagem.integridade not in {"ausente", "alterada", "ilegivel"}
    ][:limite]
    if not candidatas:
        raise ValueError("Nenhuma imagem disponível para o lote com os critérios escolhidos.")

    pasta = pasta_lotes(pasta_laboratorio, projeto_id) / "lote-atual"
    # O lote anterior é preservado em lotes-anteriores/ (recusa se houver anotação não importada).
    arquivar_lote_atual(repo, projeto_id, pasta_laboratorio)
    pasta_imagens = pasta / "images"
    pasta_rotulos = pasta / "labels"
    pasta_imagens.mkdir(parents=True)
    pasta_rotulos.mkdir()
    anotacoes = repo.anotacoes_deteccao(projeto_id)
    itens = []
    for imagem in candidatas:
        origem = raiz / imagem.caminho_rgb
        if not origem.is_file():
            continue
        destino = pasta_imagens / f"{imagem.patch_id}{origem.suffix.lower()}"
        shutil.copyfile(origem, destino)
        anotacao = anotacoes.get(imagem.patch_id)
        if anotacao is not None:
            (pasta_rotulos / f"{imagem.patch_id}.txt").write_text(anotacao["conteudo"], encoding="utf-8")
        itens.append({"patch_id": imagem.patch_id, "arquivo": destino.name, "sha256": imagem.sha256})
    classes = pasta / "classes.txt"
    texto_classes = "\n".join(esquema.classes) + "\n"
    classes.write_text(texto_classes, encoding="utf-8")
    # LabelImg também procura classes.txt na pasta de saída das anotações.
    (pasta_rotulos / "classes.txt").write_text(texto_classes, encoding="utf-8")
    manifesto = pasta / "lote.json"
    manifesto.write_text(
        json.dumps(
            {
                "projeto_id": projeto_id,
                "projeto": projeto.nome,
                "versao_esquema": esquema.versao,
                "classes": list(esquema.classes),
                "criado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "itens": itens,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return Lote(pasta, pasta_imagens, pasta_rotulos, classes, manifesto, len(itens), esquema.versao)


def lote_atual(pasta_laboratorio: Path, projeto_id: int) -> Lote | None:
    pasta = pasta_lotes(pasta_laboratorio, projeto_id) / "lote-atual"
    manifesto = pasta / "lote.json"
    if not manifesto.is_file():
        return None
    try:
        dados = json.loads(manifesto.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return Lote(
        pasta, pasta / "images", pasta / "labels", pasta / "classes.txt", manifesto,
        len(dados.get("itens", [])), int(dados.get("versao_esquema", 0)),
    )


@dataclass
class ResumoImportacao:
    rotuladas: int = 0
    com_erros: int = 0
    sem_anotacao: int = 0
    imagem_alterada: int = 0
    somente_pascal_voc: int = 0
    avisos: list[str] = field(default_factory=list)
    erros_por_imagem: dict[str, list[str]] = field(default_factory=dict)


def importar_anotacoes(
    repo: RepositorioLaboratorio, projeto_id: int, pasta_laboratorio: Path
) -> ResumoImportacao:
    """Lê os `.txt` YOLO do lote, valida contra o esquema do lote e grava no banco."""
    lote = lote_atual(pasta_laboratorio, projeto_id)
    if lote is None:
        raise ValueError("Nenhum lote preparado para este projeto.")
    dados = json.loads(lote.manifesto.read_text(encoding="utf-8"))
    if int(dados.get("projeto_id", -1)) != projeto_id:
        raise ValueError("O lote pertence a outro projeto.")
    classes_lote = tuple(dados.get("classes", ()))
    esquemas = {e.versao: e.classes for e in repo.listar_esquemas(projeto_id)}
    if esquemas.get(lote.versao_esquema) != classes_lote:
        raise ValueError("O esquema de classes do lote não confere com o registrado no projeto.")

    resumo = ResumoImportacao()
    arquivadas = {i.patch_id for i in repo.listar_imagens(projeto_id) if i.arquivada}
    # Ferramentas como o LabelImg acrescentam classes novas ao classes.txt.
    for arquivo_classes in (lote.classes, lote.rotulos / "classes.txt"):
        if arquivo_classes.is_file():
            atuais = tuple(l.strip() for l in arquivo_classes.read_text(encoding="utf-8").splitlines() if l.strip())
            if atuais != classes_lote:
                resumo.avisos.append(
                    f"{arquivo_classes.name} foi alterado no anotador ({', '.join(atuais)}). "
                    "Classes novas não entram no projeto por aqui: use 'Editar classes' e prepare outro lote."
                )
                break

    for item in dados.get("itens", []):
        patch_id = str(item.get("patch_id", ""))
        if patch_id in arquivadas:
            resumo.avisos.append(f"{patch_id}: imagem arquivada/retirada do projeto; anotação do lote ignorada.")
            continue
        imagem = lote.imagens / str(item.get("arquivo", ""))
        candidatos = (lote.rotulos / f"{patch_id}.txt", lote.imagens / f"{patch_id}.txt")
        arquivo = next((c for c in candidatos if c.is_file() and not c.is_symlink()), None)
        if arquivo is None:
            resumo.sem_anotacao += 1
            # O LabelImg grava Pascal VOC (.xml) por padrão; esse formato não é importado.
            if any((pasta / f"{patch_id}.xml").is_file() for pasta in (lote.rotulos, lote.imagens)):
                resumo.somente_pascal_voc += 1
            continue
        if not imagem.is_file() or sha256_arquivo(imagem) != item.get("sha256"):
            resumo.imagem_alterada += 1
            resumo.erros_por_imagem[patch_id] = ["a imagem do lote foi alterada ou removida"]
            continue
        if arquivo.stat().st_size > LIMITE_BYTES_ANOTACAO:
            erros = ["arquivo de anotação grande demais"]
            conteudo, caixas = "", 0
        else:
            conteudo = arquivo.read_text(encoding="utf-8", errors="replace")
            validacao = validar_anotacao_yolo(conteudo, len(classes_lote))
            erros, caixas = list(validacao.erros), validacao.caixas
        repo.registrar_anotacao_deteccao(
            projeto_id, patch_id, lote.versao_esquema, conteudo, caixas, erros, str(item.get("sha256"))
        )
        if erros:
            resumo.com_erros += 1
            resumo.erros_por_imagem[patch_id] = erros
        else:
            resumo.rotuladas += 1
    if resumo.somente_pascal_voc:
        resumo.avisos.append(
            f"{resumo.somente_pascal_voc} imagem(ns) foram salvas só em Pascal VOC (.xml), o formato padrão "
            "do LabelImg, e continuam pendentes. No LabelImg, clique no botão de formato até mostrar "
            "'YOLO' e salve de novo (Ctrl+S)."
        )
    return resumo


# --------------------------------------------------------------------------- anotadores


@dataclass(frozen=True)
class Anotador:
    nome: str
    executavel: str
    argumentos: tuple[str, ...]
    instrucao: str


def localizar_anotadores(lote: Lote | None = None, which=None) -> list[Anotador]:
    """Ferramentas externas encontradas no PATH. Nada é instalado automaticamente."""
    which = which or shutil.which
    anotadores: list[Anotador] = []
    for nome_exe in ("labelImg", "labelimg"):
        caminho = which(nome_exe)
        if caminho:
            argumentos = (
                (str(lote.imagens), str(lote.classes), str(lote.rotulos)) if lote else ()
            )
            anotadores.append(
                Anotador(
                    "LabelImg",
                    caminho,
                    argumentos,
                    "No LabelImg, confirme o formato YOLO (botão abaixo de 'Save') antes de salvar. "
                    "As anotações vão para a pasta labels do lote.",
                )
            )
            break
    for nome_exe in ("xanylabeling", "X-AnyLabeling", "anylabeling"):
        caminho = which(nome_exe)
        if caminho:
            anotadores.append(
                Anotador(
                    "X-AnyLabeling",
                    caminho,
                    (),
                    "No X-AnyLabeling, abra a pasta images do lote e exporte as anotações em "
                    "formato YOLO para a pasta labels do lote, usando o classes.txt do lote.",
                )
            )
            break
    return anotadores
