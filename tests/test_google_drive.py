from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from sentinel2_mt.configuracao import (
    ConfiguracaoArea,
    ConfiguracaoDownload,
    ConfiguracaoPeriodo,
    ConfiguracaoPreview,
    ConfiguracaoProjeto,
    ConfiguracaoQualidade,
    ConfiguracaoSincronizacao,
    ConfiguracaoStac,
)
from sentinel2_mt.google_drive import AutenticadorGoogleDrive, SincronizadorGoogleDrive, dividir_em_lotes


class RequisicaoFake:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class ArquivosDriveFake:
    def __init__(self, arquivos_existentes: bool = False) -> None:
        self.arquivos_existentes = arquivos_existentes
        self.criacoes: list[dict] = []
        self.atualizacoes: list[dict] = []
        self.consultas: list[str] = []

    def list(self, **parametros):
        consulta = parametros["q"]
        self.consultas.append(consulta)
        if "mimeType" in consulta:
            encontrados = []
        else:
            encontrados = [{"id": "arquivo-existente"}] if self.arquivos_existentes else []
        return RequisicaoFake({"files": encontrados})

    def create(self, **parametros):
        self.criacoes.append(parametros)
        numero = len(self.criacoes)
        return RequisicaoFake({"id": f"criado-{numero}"})

    def update(self, **parametros):
        self.atualizacoes.append(parametros)
        return RequisicaoFake({"id": parametros["fileId"]})


class ServicoDriveFake:
    def __init__(self, arquivos_existentes: bool = False) -> None:
        self.api_arquivos = ArquivosDriveFake(arquivos_existentes)

    def files(self):
        return self.api_arquivos


class AutenticadorFake:
    def __init__(self, servico) -> None:
        self.servico = servico
        self.chamadas: list[tuple[Path, Path]] = []

    def autenticar(self, oauth_path: Path, token_path: Path):
        self.chamadas.append((oauth_path, token_path))
        return self.servico


def configuracao_temporaria(raiz: Path) -> ConfiguracaoProjeto:
    return ConfiguracaoProjeto(
        raiz=raiz,
        stac=ConfiguracaoStac("https://example.invalid/stac", "S2"),
        area=ConfiguracaoArea("Teste", "MT", (-60, -15, -55, -10)),
        periodo=ConfiguracaoPeriodo("2025-01-01", "2025-01-02"),
        bandas=("B02",),
        qualidade=ConfiguracaoQualidade(),
        preview=ConfiguracaoPreview(),
        download=ConfiguracaoDownload(pasta="imagens"),
        sincronizacao=ConfiguracaoSincronizacao(
            pasta_remota="sentinel2-mt",
            oauth_json="oauth.json",
            token_json="token.json",
            tamanho_lote=2,
        ),
    )


class TestLotesGoogleDrive(TestCase):
    def test_usa_escopo_de_menor_privilegio(self) -> None:
        self.assertEqual(AutenticadorGoogleDrive.ESCOPO, ("https://www.googleapis.com/auth/drive.file",))

    def test_divide_sem_perder_ordem(self) -> None:
        lotes = [list(lote) for lote in dividir_em_lotes([1, 2, 3, 4, 5], 2)]
        self.assertEqual(lotes, [[1, 2], [3, 4], [5]])

    def test_rejeita_lote_invalido(self) -> None:
        with self.assertRaisesRegex(ValueError, "maior que zero"):
            list(dividir_em_lotes([1], 0))

    def test_seleciona_extensoes_e_ordena(self) -> None:
        with TemporaryDirectory() as temporario:
            pasta = Path(temporario)
            (pasta / "b.JPG").write_bytes(b"imagem")
            (pasta / "a.tif").write_bytes(b"imagem")
            (pasta / "ignorar.txt").write_text("texto", encoding="utf-8")

            arquivos = SincronizadorGoogleDrive.arquivos_para_sincronizar(pasta, ("tif", ".jpg"))

            self.assertEqual([arquivo.name for arquivo in arquivos], ["a.tif", "b.JPG"])

    def test_sincroniza_hierarquia_completa_em_lotes(self) -> None:
        with TemporaryDirectory() as temporario:
            raiz = Path(temporario)
            pasta = raiz / "imagens" / "2025-01-01" / "cena-1"
            pasta.mkdir(parents=True)
            for nome in ("B02.tif", "B03.tif", "preview.jpg"):
                (pasta / nome).write_bytes(b"imagem")
            servico = ServicoDriveFake()
            autenticador = AutenticadorFake(servico)
            saida: list[str] = []
            sincronizador = SincronizadorGoogleDrive(
                configuracao_temporaria(raiz),
                autenticador=autenticador,
                saida=saida.append,
            )

            quantidade_lotes = sincronizador.sincronizar()

            envios = [
                chamada
                for chamada in servico.api_arquivos.criacoes
                if chamada.get("media_body") is not None
            ]
            self.assertEqual(quantidade_lotes, 2)
            self.assertEqual(len(envios), 3)
            self.assertEqual(len(autenticador.chamadas), 1)
            self.assertTrue(any("3 arquivo(s) em 2 lote(s)" in linha for linha in saida))
            self.assertTrue(any("[LOTE 2/2]" in linha for linha in saida))

    def test_atualiza_arquivo_que_ja_existe_no_drive(self) -> None:
        with TemporaryDirectory() as temporario:
            raiz = Path(temporario)
            pasta = raiz / "imagens"
            pasta.mkdir()
            (pasta / "preview.jpg").write_bytes(b"imagem")
            servico = ServicoDriveFake(arquivos_existentes=True)
            autenticador = AutenticadorFake(servico)
            sincronizador = SincronizadorGoogleDrive(
                configuracao_temporaria(raiz), autenticador=autenticador, saida=lambda _: None
            )

            quantidade_lotes = sincronizador.sincronizar()

            self.assertEqual(quantidade_lotes, 1)
            self.assertEqual(len(servico.api_arquivos.atualizacoes), 1)
            self.assertEqual(
                servico.api_arquivos.atualizacoes[0]["fileId"], "arquivo-existente"
            )
