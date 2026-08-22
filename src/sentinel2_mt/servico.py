from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
import re
from urllib.parse import urlparse, urlsplit, urlunsplit

from pystac_client import Client

from .assets import ResolvedorAssets
from .catalogo import RepositorioCatalogoCSV, RepositorioCatalogoPatchesCSV
from .caminhos import caminho_contido, componente_seguro
from .configuracao import ConfiguracaoDataset, ConfiguracaoProjeto
from .http import ClienteDownloadHTTP
from .imagens import ProcessadorImagem
from .modelos import RegistroCatalogo, ResumoDataset, ResumoExecucao
from .patches import GeradorDataset


@dataclass(frozen=True)
class OpcoesColeta:
    baixar_arquivos: bool = False
    inicio: str | None = None
    fim: str | None = None
    max_itens: int | None = None
    gerar_dataset: bool = False
    patch_size: int | None = None
    patch_stride: int | None = None


class ServicoSentinel2:
    def __init__(
        self,
        config: ConfiguracaoProjeto,
        catalogo: RepositorioCatalogoCSV | None = None,
        processador: ProcessadorImagem | None = None,
        catalogo_patches: RepositorioCatalogoPatchesCSV | None = None,
        cliente_stac_factory: Callable[[str], object] = Client.open,
        saida: Callable[[str], None] = print,
    ) -> None:
        self.config = config
        self.catalogo = catalogo or RepositorioCatalogoCSV()
        self.processador = processador or ProcessadorImagem()
        self.catalogo_patches = catalogo_patches or RepositorioCatalogoPatchesCSV()
        self.cliente_stac_factory = cliente_stac_factory
        self.saida = saida

    def executar(self, opcoes: OpcoesColeta) -> ResumoExecucao:
        inicio = opcoes.inicio or self.config.periodo.inicio
        fim = opcoes.fim or self.config.periodo.fim
        max_itens = self.config.download.max_itens_teste if opcoes.max_itens is None else opcoes.max_itens
        if max_itens < 0:
            raise ValueError("max_itens não pode ser negativo")

        dataset_config = self._config_dataset(opcoes)
        if opcoes.gerar_dataset and not opcoes.baixar_arquivos:
            return self._gerar_dataset_local(inicio, fim, max_itens, dataset_config)
        dataset_ativo = dataset_config.gerar and opcoes.baixar_arquivos

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
        registros_patches = []
        resumo_dataset_total = ResumoDataset()
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
            pasta_item = caminho_contido(
                pasta_dados,
                componente_seguro(data, "sem_data"),
                componente_seguro(item.id, "scene"),
            )
            self.saida(f"\n[CANDIDATO {resumo.candidatos}] {item.id} | {data}")
            if not opcoes.baixar_arquivos:
                registros.append(self._registro(item, data, "CENA", "nao_avaliado", "candidato_catalogado"))
                resumo.aprovadas += 1
                continue

            auxiliares = self._baixar_auxiliares(item, data, pasta_item, downloader, registros, resumo)
            nuvem = self._avaliar_qualidade(item, data, auxiliares, registros, resumo)
            if nuvem is None:
                continue

            resumo.aprovadas += 1
            self.saida(f"  [APROVADA {resumo.aprovadas}] baixando bandas científicas...")
            arquivos = self._baixar_bandas(item, data, pasta_item, nuvem, downloader, registros, resumo)
            self._gerar_preview(item, data, pasta_item, nuvem, arquivos, registros, resumo)
            if dataset_ativo and dataset_config.patches.habilitado:
                try:
                    novos, resumo_dataset = self._gerar_dataset_cena(
                        dataset_config, item.id, data, pasta_item, arquivos, auxiliares
                    )
                    registros_patches.extend(novos)
                    resumo_dataset_total.acumular(resumo_dataset)
                    resumo.erros += resumo_dataset.erros
                except Exception as exc:
                    resumo.erros += 1
                    resumo_dataset_total.erros += 1
                    self.saida(f"[DATASET] ERRO na cena {item.id}: {exc}")
            self._remover_scl_se_configurado(auxiliares)

        caminho_catalogo = self.config.caminho(self.config.download.catalogo)
        self.catalogo.salvar(caminho_catalogo, registros)
        if dataset_ativo:
            self.catalogo_patches.salvar(
                self.config.caminho(dataset_config.catalogo), registros_patches
            )
            self._imprimir_resumo_dataset(resumo_dataset_total)
        self._imprimir_resumo(resumo, caminho_catalogo, opcoes.baixar_arquivos)
        return resumo

    def _baixar_auxiliares(
        self,
        item,
        data: str,
        pasta_item: Path,
        downloader: ClienteDownloadHTTP,
        registros: list[RegistroCatalogo],
        resumo: ResumoExecucao,
    ) -> dict[str, Path]:
        arquivos: dict[str, Path] = {}
        for nome in self.config.qualidade.camadas_auxiliares:
            asset = self.localizar_asset(item, nome)
            if asset is None:
                registros.append(self._registro(item, data, nome, "nao_avaliado", "asset_nao_encontrado"))
                self.saida(f"  [AUXILIAR] {nome}: asset não encontrado")
                continue
            destino = caminho_contido(
                pasta_item,
                "qualidade",
                f"{componente_seguro(nome, 'auxiliar')}{self.extensao(asset.href)}",
            )
            try:
                status = downloader.baixar(asset.href, destino)
                arquivos[nome] = destino
                erro = ""
                self.saida(f"  [AUXILIAR] {nome}: {status}")
            except Exception as exc:
                status, erro = "erro_download", self._erro_publico(str(exc))
                resumo.erros += 1
                self.saida(f"  [ERRO] auxiliar {nome}: {erro}")
            registros.append(
                self._registro(
                    item, data, nome, "nao_avaliado", status, asset.href,
                    self._relativo(destino), erro
                )
            )
        return arquivos

    def _avaliar_qualidade(
        self,
        item,
        data: str,
        auxiliares: dict[str, Path],
        registros: list[RegistroCatalogo],
        resumo: ResumoExecucao,
    ) -> float | str | None:
        qualidade = self.config.qualidade
        if not qualidade.filtrar_nuvens:
            return "nao_avaliado"

        scl_path = auxiliares.get("SCL")
        if scl_path is None:
            self.saida("  [QUALIDADE] SCL indisponível; cena mantida sem avaliação global.")
            registros.append(self._registro(item, data, "SCL_QUALITY", "indisponivel", "nao_avaliado_sem_scl"))
            return "scl_indisponivel"

        try:
            nuvem = self.processador.percentual_nuvem(scl_path)
            self.saida(f"  [QUALIDADE] nuvem/sombra estimada: {nuvem:.2f}%")
            if nuvem > qualidade.nuvem_max_pct:
                resumo.descartadas += 1
                self.saida(
                    f"  [DESCARTADA] acima de {qualidade.nuvem_max_pct:.1f}%; "
                    "bandas científicas não serão baixadas."
                )
                registros.append(
                    self._registro(
                        item, data, "SCL_QUALITY", nuvem, "descartada_nuvem",
                        arquivo=self._relativo(scl_path)
                    )
                )
                if not qualidade.manter_scl:
                    scl_path.unlink(missing_ok=True)
                    auxiliares.pop("SCL", None)
                return None
            registros.append(
                self._registro(
                    item, data, "SCL_QUALITY", nuvem, "aprovada_qualidade",
                    arquivo=self._relativo(scl_path)
                )
            )
            return nuvem
        except Exception as exc:
            resumo.erros += 1
            self.saida(f"  [ERRO] avaliação SCL: {exc}; cena mantida sem filtro global.")
            registros.append(
                self._registro(
                    item, data, "SCL_QUALITY", "erro", "erro_qualidade",
                    arquivo=self._relativo(scl_path), erro=str(exc)
                )
            )
            return "erro_scl"

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
            nome_banda = ResolvedorAssets.normalizar(banda)
            asset = self.localizar_asset(item, nome_banda)
            if asset is None:
                registros.append(self._registro(item, data, nome_banda, nuvem, "asset_nao_encontrado"))
                continue
            destino = caminho_contido(
                pasta_item,
                f"{componente_seguro(nome_banda, 'banda')}{self.extensao(asset.href)}",
            )
            try:
                status = downloader.baixar(asset.href, destino)
                arquivos[nome_banda] = destino
                erro = ""
                self.saida(f"  [OK] {nome_banda}: {status}")
            except Exception as exc:
                status, erro = "erro_download", self._erro_publico(str(exc))
                resumo.erros += 1
                self.saida(f"  [ERRO] {nome_banda}: {erro}")
            registros.append(
                self._registro(item, data, nome_banda, nuvem, status, asset.href, self._relativo(destino), erro)
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

    def _config_dataset(self, opcoes: OpcoesColeta) -> ConfiguracaoDataset:
        patches = self.config.dataset.patches
        if opcoes.patch_size is not None:
            stride = opcoes.patch_stride if opcoes.patch_stride is not None else opcoes.patch_size
            patches = replace(patches, tamanho_px=opcoes.patch_size, stride_px=stride)
        elif opcoes.patch_stride is not None:
            patches = replace(patches, stride_px=opcoes.patch_stride)
        dataset = replace(
            self.config.dataset,
            gerar=self.config.dataset.gerar or opcoes.gerar_dataset,
            patches=patches,
        )
        replace(self.config, dataset=dataset).validar()
        return dataset

    def _gerar_dataset_cena(
        self,
        dataset_config: ConfiguracaoDataset,
        scene_id: str,
        data: str,
        pasta_item: Path,
        arquivos: dict[str, Path],
        auxiliares: dict[str, Path],
    ):
        gerador = GeradorDataset(dataset_config, self.config.raiz, self.processador, self.saida)
        return gerador.gerar_cena(
            scene_id=scene_id,
            collection=self.config.stac.colecao,
            date=data,
            source_scene=pasta_item,
            arquivos=arquivos,
            auxiliares=auxiliares,
            bandas_desejadas=tuple(
                ResolvedorAssets.normalizar(nome) for nome in self.config.bandas
            ),
            manter_scl=self.config.qualidade.manter_scl,
        )

    def _gerar_dataset_local(
        self,
        inicio: str,
        fim: str,
        max_itens: int,
        dataset_config: ConfiguracaoDataset,
    ) -> ResumoExecucao:
        pasta_dados = self.config.caminho(self.config.download.pasta)
        if not pasta_dados.is_dir():
            raise FileNotFoundError(f"Pasta de cenas não encontrada: {pasta_dados}")
        if not dataset_config.patches.habilitado:
            caminho_catalogo = self.config.caminho(dataset_config.catalogo)
            self.catalogo_patches.salvar(caminho_catalogo, [])
            self.saida("[DATASET] Geração de patches está desabilitada na configuração.")
            return ResumoExecucao()
        cenas = sorted(
            pasta for pasta in pasta_dados.glob("*/*")
            if pasta.is_dir() and inicio <= pasta.parent.name <= fim
        )
        if max_itens > 0:
            cenas = cenas[:max_itens]

        resumo = ResumoExecucao()
        registros_patches = []
        resumo_dataset_total = ResumoDataset()
        for pasta_item in cenas:
            resumo.candidatos += 1
            data, scene_id = pasta_item.parent.name, pasta_item.name
            arquivos, auxiliares = self._localizar_arquivos_cena(pasta_item)
            try:
                novos, resumo_dataset = self._gerar_dataset_cena(
                    dataset_config, scene_id, data, pasta_item, arquivos, auxiliares
                )
                registros_patches.extend(novos)
                resumo_dataset_total.acumular(resumo_dataset)
                resumo.aprovadas += 1
                resumo.erros += resumo_dataset.erros
            except Exception as exc:
                resumo.erros += 1
                resumo_dataset_total.erros += 1
                self.saida(f"[DATASET] ERRO na cena {scene_id}: {exc}")
            self._remover_scl_se_configurado(auxiliares)

        caminho_catalogo = self.config.caminho(dataset_config.catalogo)
        self.catalogo_patches.salvar(caminho_catalogo, registros_patches)
        self.saida(f"[DATASET] Catálogo: {self._relativo(caminho_catalogo)}")
        self._imprimir_resumo_dataset(resumo_dataset_total)
        if not cenas:
            self.saida("[DATASET] Nenhuma cena local encontrada no período informado.")
        return resumo

    def _localizar_arquivos_cena(self, pasta_item: Path) -> tuple[dict[str, Path], dict[str, Path]]:
        desejadas = {ResolvedorAssets.normalizar(nome) for nome in self.config.bandas}
        arquivos: dict[str, Path] = {}
        for caminho in pasta_item.iterdir():
            nome = ResolvedorAssets.normalizar(caminho.stem)
            if caminho.is_file() and nome in desejadas and caminho.suffix.casefold() in {".tif", ".tiff", ".jp2"}:
                arquivos[nome] = caminho

        auxiliares: dict[str, Path] = {}
        pasta_qualidade = pasta_item / "qualidade"
        if pasta_qualidade.is_dir():
            for caminho in pasta_qualidade.iterdir():
                if caminho.is_file():
                    nome = ResolvedorAssets.normalizar(caminho.stem)
                    if nome in self.config.qualidade.camadas_auxiliares:
                        auxiliares[nome] = caminho
        return arquivos, auxiliares

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
        return RegistroCatalogo.criar(
            item,
            data,
            self.config.stac.colecao,
            banda,
            nuvem,
            status,
            self._url_publica(url),
            arquivo,
            self._erro_publico(erro),
        )

    def _remover_scl_se_configurado(self, auxiliares: dict[str, Path]) -> None:
        if self.config.qualidade.manter_scl:
            return
        scl = auxiliares.pop("SCL", None)
        if scl is not None:
            scl.unlink(missing_ok=True)
            self.saida("  [QUALIDADE] SCL removida conforme manter_scl=false")

    @staticmethod
    def _url_publica(url: str) -> str:
        if not url:
            return ""
        partes = urlsplit(url)
        host = partes.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        try:
            numero_porta = partes.port
        except ValueError:
            numero_porta = None
        porta = f":{numero_porta}" if numero_porta is not None else ""
        return urlunsplit((partes.scheme, f"{host}{porta}", partes.path, "", ""))

    @classmethod
    def _erro_publico(cls, erro: str) -> str:
        return re.sub(
            r"https?://[^\s'\"]+",
            lambda match: cls._url_publica(match.group(0)),
            erro,
        )

    def _relativo(self, caminho: Path) -> str:
        try:
            return str(caminho.resolve().relative_to(self.config.raiz.resolve()))
        except ValueError:
            return caminho.name

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

    def _imprimir_resumo_dataset(self, resumo: ResumoDataset) -> None:
        self.saida("[DATASET] Resumo final")
        self.saida(f"Patches candidatos: {resumo.candidatos}")
        self.saida(f"Patches aprovados: {resumo.aprovados}")
        self.saida(f"Descartados por nuvem: {resumo.descartados_nuvem}")
        self.saida(f"Descartados por nodata: {resumo.descartados_nodata}")
        self.saida(f"Erros: {resumo.erros}")

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
        return ResolvedorAssets.localizar(item.assets, nome)

    @staticmethod
    def extensao(url: str) -> str:
        extensao = Path(urlparse(url).path).suffix.casefold()
        return extensao if re.fullmatch(r"\.[a-z0-9]{1,10}", extensao) else ".tif"
