from __future__ import annotations

from io import BytesIO
from unittest import TestCase
from unittest.mock import patch

from sentinel2_mt.oauth_callback import (
    AplicacaoRetornoOAuth,
    ErroAutorizacaoOAuth,
    autorizar_com_pagina_oauth,
    pagina_retorno_oauth,
)


def ambiente_oauth(query: str) -> dict:
    return {
        "REQUEST_METHOD": "GET",
        "SCRIPT_NAME": "",
        "PATH_INFO": "/",
        "QUERY_STRING": query,
        "SERVER_NAME": "127.0.0.1",
        "SERVER_PORT": "54321",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "HTTP_HOST": "127.0.0.1:54321",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "http",
        "wsgi.input": BytesIO(),
        "wsgi.errors": BytesIO(),
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": True,
    }


class ServidorFake:
    def __init__(self, aplicacao, query: str = "code=codigo&state=estado") -> None:
        self.aplicacao = aplicacao
        self.query = query
        self.server_port = 54321
        self.timeout = None
        self.fechado = False
        self.status = None
        self.cabecalhos = None

    def handle_request(self) -> None:
        def iniciar_resposta(status, cabecalhos) -> None:
            self.status = status
            self.cabecalhos = dict(cabecalhos)

        list(self.aplicacao(ambiente_oauth(self.query), iniciar_resposta))

    def server_close(self) -> None:
        self.fechado = True


class FluxoFake:
    def __init__(self) -> None:
        self.redirect_uri = ""
        self.parametros_autorizacao = None
        self.resposta_token = None
        self.credentials = "credenciais-fake"

    def authorization_url(self, **parametros):
        self.parametros_autorizacao = parametros
        return "https://accounts.example.test/oauth", "estado"

    def fetch_token(self, **parametros) -> None:
        self.resposta_token = parametros["authorization_response"]


class TestPaginaOAuth(TestCase):
    def test_html_de_sucesso_tem_identidade_e_orientacao(self) -> None:
        pagina = pagina_retorno_oauth(True)

        self.assertIn("Autenticação concluída", pagina)
        self.assertIn("Sentinel-2 MT", pagina)
        self.assertIn("Google Drive conectado", pagina)
        self.assertIn("Fechar esta aba", pagina)
        self.assertIn('class="sucesso"', pagina)

    def test_html_de_erro_escapa_detalhes(self) -> None:
        pagina = pagina_retorno_oauth(False, "Conta <não autorizada>")

        self.assertIn("Não foi possível conectar", pagina)
        self.assertIn("Conta &lt;não autorizada&gt;", pagina)
        self.assertNotIn("Conta <não autorizada>", pagina)
        self.assertIn("Nenhuma credencial foi armazenada", pagina)
        self.assertIn('class="erro"', pagina)

    def test_aplicacao_conclui_token_antes_de_mostrar_sucesso(self) -> None:
        respostas: list[str] = []
        aplicacao = AplicacaoRetornoOAuth(respostas.append)
        status = []

        corpo = b"".join(
            aplicacao(
                ambiente_oauth("code=codigo&state=estado"),
                lambda valor, _cabecalhos: status.append(valor),
            )
        ).decode("utf-8")

        self.assertEqual(status, ["200 OK"])
        self.assertEqual(
            respostas,
            ["https://127.0.0.1:54321/?code=codigo&state=estado"],
        )
        self.assertIn("Autenticação concluída", corpo)
        self.assertIsNone(aplicacao.erro)

    def test_aplicacao_exibe_erro_quando_acesso_e_negado(self) -> None:
        aplicacao = AplicacaoRetornoOAuth(lambda _resposta: None)

        corpo = b"".join(
            aplicacao(
                ambiente_oauth("error=access_denied&error_description=Acesso+negado"),
                lambda _status, _cabecalhos: None,
            )
        ).decode("utf-8")

        self.assertIsInstance(aplicacao.erro, ErroAutorizacaoOAuth)
        self.assertIn("Não foi possível conectar", corpo)
        self.assertIn("Acesso negado", corpo)

    def test_fluxo_completo_usa_ipv4_e_fecha_servidor(self) -> None:
        fluxo = FluxoFake()
        servidor_criado = None

        def criar_servidor(_host, _porta, aplicacao, handler_class):
            nonlocal servidor_criado
            self.assertIsNotNone(handler_class)
            servidor_criado = ServidorFake(aplicacao)
            return servidor_criado

        with (
            patch(
                "sentinel2_mt.oauth_callback.wsgiref.simple_server.make_server",
                side_effect=criar_servidor,
            ),
            patch("sentinel2_mt.oauth_callback.webbrowser.open", return_value=True) as abrir,
        ):
            credenciais = autorizar_com_pagina_oauth(fluxo)

        self.assertEqual(credenciais, "credenciais-fake")
        self.assertEqual(fluxo.redirect_uri, "http://127.0.0.1:54321/")
        self.assertEqual(fluxo.parametros_autorizacao, {"prompt": "select_account"})
        self.assertEqual(
            fluxo.resposta_token,
            "https://127.0.0.1:54321/?code=codigo&state=estado",
        )
        abrir.assert_called_once_with(
            "https://accounts.example.test/oauth", new=1, autoraise=True
        )
        self.assertEqual(servidor_criado.timeout, 300)
        self.assertTrue(servidor_criado.fechado)
