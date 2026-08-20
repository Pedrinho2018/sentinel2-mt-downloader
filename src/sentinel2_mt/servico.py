from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from pystac_client import Client

from .catalogo import RepositorioCatalogoCSV
from .configuracao import ConfiguracaoProjeto
from .http import ClienteDownloadHTTP
from .imagens import ProcessadorImagem
from .modelos import RegistroCatalogo, ResumoExecucao


@dataclass(frozen=True)
class OpcoesColeta:
    baixar_arquivos: bool = False
    inicio: str | None = None
    fim: str | None = None
    max_itens: int | None = None


class ServicoSentinel2:
    def __init__(
        self,
        config: ConfiguracaoProjeto,
        catalogo: RepositorioCatalogoCSV | None = None,
        processador: ProcessadorImagem | None = None,
        cliente_stac_factory: Callable[[str], object] = Client.open,
        saida: Callable[[str], None] = print,
    ) -> None:
        self.config = config
        self.catalogo = catalogo or RepositorioCatalogoCSV()
        self.processador = processador or ProcessadorImagem()
        self.cliente_stac_factory = cliente_stac_factory
        self.saida = saida

    def executar(self, opcoes: OpcoesColeta) -> ResumoExecucao:
        inicio = opcoes.inicio or self.config.periodo.inicio
        fim = opcoes.fim or self.config.periodo.fim
        max_itens = self.config.download.max_itens_teste if opcoes.max_itens is None else opcoes.max_itens
        if max_itens < 0:
            raise ValueError("max_itens não pode ser negativo")

        self._imprimir_cabecalho(inicio, fim, max_itens)
        cliente = self.cliente_stac_factory(self.config.stac.url)
        busca = cliente.search(
            collections=[self.config.stac.colecao],
            bbox=list(self.config.area.bbox),
            datetime=f"{inicio}/{fim}",
        )
        downloader = None
        if opcoes.baixar_arquivos:
            downloader = ClienteDownloadHTTP(self.config.download.timeout_segundos, self.config.download.chunk_mb)

        registros: list[RegistroCatalogo] = []
        resumo = ResumoExecucao()
        pasta_dados = self.config.caminho(self.config.download.pasta)

        for item in busca.items():
            if max_itens > 0 and resumo.aprovadas >= max_itens:
                break
            resumo.candidatos += 1
            if max_itens > 0 and resumo.candidatos > self.config.download.max_candidatos_teste:
                self.saida(f"[LIMITE] {self.config.download.max_candidatos_teste} candidatos avaliados.")
                break

            data = self.data_item(item)
            pasta_item = pasta_dados / data / item.id
            self.saida(f"\n[CANDIDATO {resumo.candidatos}] {item.id} | {data}")
            if not opcoes.baixar_arquivos:
                registros.append(self._registro(item, data, "CENA", "nao_avaliado", "candidato_catalogado"))
                resumo.aprovadas += 1
                continue

            nuvem = self._avaliar_qualidade(item, data, pasta_item, downloader, registros, resumo)
            if nuvem is None:
                continue

            resumo.aprovadas += 1
            self.saida(f"  [APROVADA {resumo.aprovadas}] baixando bandas científicas...")
            arquivos = self._baixar_bandas(item, data, pasta_item, nuvem, downloader, registros, resumo)
            self._gerar_preview(item, data, pasta_item, nuvem, arquivos, registros, resumo)

        caminho_catalogo = self.config.caminho(self.config.download.catalogo)
        self.catalogo.salvar(caminho_catalogo, registros)
        self._imprimir_resumo(resumo, caminho_catalogo, opcoes.baixar_arquivos)
        return resumo

    def _avaliar_qualidade(
        self,
        item,
        data: str,
        pasta_item: Path,
        downloader: ClienteDownloadHTTP,
        registros: list[RegistroCatalogo],
        resumo: ResumoExecucao,
    ) -> float | str | None:
        qualidade = self.config.qualidade
        if not qualidade.filtrar_nuvens:
            return "nao_avaliado"

        scl = self.localizar_asset(item, "SCL")
        if scl is None:
            resumo.descartadas += 1
            self.saida("  [DESCARTADA] SCL indisponível.")
            registros.append(self._registro(item, data, "SCL", "indisponivel", "descartada_sem_scl"))
            return None

        scl_path = pasta_item / "qualidade" / f"SCL{self.extensao(scl.href)}"
        try:
            status = downloader.baixar(scl.href, scl_path)
            nuvem = self.processador.percentual_nuvem(scl_path)
            self.saida(f"  [QUALIDADE] nuvem/sombra estimada: {nuvem:.2f}%")
            if nuvem > qualidade.nuvem_max_pct:
                resumo.descartadas += 1
                self.saida(f"  [DESCARTADA] acima de {qualidade.nuvem_max_pct:.1f}%; bandas grandes não serão baixadas.")
                registros.append(
                    self._registro(item, data, "SCL", nuvem, "descartada_nuvem", scl.href, self._relativo(scl_path))
                )
                if not qualidade.manter_scl:
                    scl_path.unlink(missing_ok=True)
                return None
            registros.append(self._registro(item, data, "SCL", nuvem, status, scl.href, self._relativo(scl_path)))
            return nuvem
        except Exception as exc:
            resumo.erros += 1
            self.saida(f"  [ERRO] SCL: {exc}")
            registros.append(
                self._registro(item, data, "SCL", "erro", "erro_qualidade", scl.href, self._relativo(scl_path), str(exc))
            )
            return None

    def _baixar_bandas(
        self,
        item,
        data: str,
        pasta_item: Path,
        nuvem: float | str,
        downloader: ClienteDownloadHTTP,
        registros: list[RegistroCatalogo],
        resumo: ResumoExecucao,
    ) -> dict[str, Path]:
        arquivos: dict[str, Path] = {}
        for banda in self.config.bandas:
            asset = self.localizar_asset(item, banda)
            if asset is None:
                registros.append(self._registro(item, data, banda, nuvem, "asset_nao_encontrado"))
                continue
            destino = pasta_item / f"{banda}{self.extensao(asset.href)}"
            try:
                status = downloader.baixar(asset.href, destino)
                arquivos[banda] = destino
                erro = ""
                self.saida(f"  [OK] {banda}: {status}")
            except Exception as exc:
                status, erro = "erro_download", str(exc)
                resumo.erros += 1
                self.saida(f"  [ERRO] {banda}: {exc}")
            registros.append(
                self._registro(item, data, banda, nuvem, status, asset.href, self._relativo(destino), erro)
            )
        return arquivos

    def _gerar_preview(
        self,
        item,
        data: str,
        pasta_item: Path,
        nuvem: float | str,
        arquivos: dict[str, Path],
        registros: list[RegistroCatalogo],
        resumo: ResumoExecucao,
    ) -> None:
        if not self.config.preview.gerar_rgb:
            return
        preview = pasta_item / "preview_rgb.jpg"
        try:
            status = self.processador.gerar_rgb(arquivos, preview, self.config.preview)
            resumo.previews += int(status == "gerado")
            self.saida(f"  [PREVIEW] {status}: {preview.name}")
            registros.append(self._registro(item, data, "RGB_PREVIEW", nuvem, status, arquivo=self._relativo(preview)))
        except Exception as exc:
            resumo.erros += 1
            self.saida(f"  [ERRO] preview: {exc}")
            registros.append(
                self._registro(item, data, "RGB_PREVIEW", nuvem, "erro_preview", arquivo=self._relativo(preview), erro=str(exc))
            )

    def _registro(
        self,
        item,
        data: str,
        banda: str,
        nuvem,
        status: str,
        url: str = "",
        arquivo: str = "",
        erro: str = "",
    ) -> RegistroCatalogo:
        return RegistroCatalogo.criar(item, data, self.config.stac.colecao, banda, nuvem, status, url, arquivo, erro)

    def _relativo(self, caminho: Path) -> str:
        try:
            return str(caminho.relative_to(self.config.raiz))
        except ValueError:
            return str(caminho)

    def _imprimir_cabecalho(self, inicio: str, fim: str, max_itens: int) -> None:
        self.saida("=" * 72)
        self.saida(" Sentinel-2 MT Downloader | seleção para análise agrícola")
        self.saida("=" * 72)
        self.saida(f"Período: {inicio} até {fim} | coleção: {self.config.stac.colecao}")
        filtro = "SIM" if self.config.qualidade.filtrar_nuvens else "NÃO"
        self.saida(f"Filtro de nuvens/sombra: {filtro} | máximo: {self.config.qualidade.nuvem_max_pct:.1f}%")
        self.saida(f"Meta: {'todas' if max_itens == 0 else max_itens} cena(s) aprovada(s)")

    def _imprimir_resumo(self, resumo: ResumoExecucao, catalogo: Path, baixou: bool) -> None:
        self.saida("\n" + "=" * 72)
        self.saida(
            f"Candidatos: {resumo.candidatos} | aprovadas: {resumo.aprovadas} | "
            f"descartadas por qualidade: {resumo.descartadas}"
        )
        self.saida(f"Previews: {resumo.previews} | erros: {resumo.erros}")
        self.saida(f"Catálogo: {self._relativo(catalogo)}")
        if not baixou:
            self.saida("Modo seguro: use --baixar --max-itens 1 para avaliar e baixar uma cena boa.")

    @staticmethod
    def data_item(item) -> str:
        if item.datetime:
            return item.datetime.strftime("%Y-%m-%d")
        inicio = item.properties.get("start_datetime", "")
        if inicio:
            try:
                return datetime.fromisoformat(inicio.replace("Z", "+00:00")).strftime("%Y-%m-%d")
            except ValueError:
                return inicio[:10]
        return "sem_data"

    @staticmethod
    def localizar_asset(item, nome: str):
        if nome in item.assets:
            return item.assets[nome]
        alvo = nome.casefold()
        return next((asset for chave, asset in item.assets.items() if chave.casefold() == alvo), None)

    @staticmethod
    def extensao(url: str) -> str:
        return Path(urlparse(url).path).suffix or ".tif"
