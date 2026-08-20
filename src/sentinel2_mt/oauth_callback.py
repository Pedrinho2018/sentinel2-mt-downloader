from __future__ import annotations

import html
import webbrowser
import wsgiref.simple_server
import wsgiref.util
from collections.abc import Callable
from urllib.parse import parse_qs


class ErroAutorizacaoOAuth(RuntimeError):
    """Erro amigável recebido durante o retorno local do Google OAuth."""


def pagina_retorno_oauth(sucesso: bool, detalhe: str = "") -> str:
    if sucesso:
        classe = "sucesso"
        etiqueta = "Google Drive conectado"
        titulo = "Autenticação concluída"
        mensagem = (
            "Sua conta foi autorizada com segurança. A sincronização continuará "
            "automaticamente no Sentinel-2 MT."
        )
        seguranca = "Permissão limitada a arquivos desta aplicação"
        rodape = "Você já pode fechar esta aba."
        icone = """
        <svg viewBox="0 0 64 64" aria-hidden="true">
          <path d="M18 33.5 27.5 43 47 22" />
        </svg>
        """
    else:
        classe = "erro"
        etiqueta = "Autorização não concluída"
        titulo = "Não foi possível conectar"
        mensagem = (
            "O Google não liberou o acesso para esta tentativa. Volte ao "
            "Sentinel-2 MT, confira a conta selecionada e tente novamente."
        )
        seguranca = "Nenhuma credencial foi armazenada"
        rodape = detalhe or "Nenhuma credencial foi salva."
        icone = """
        <svg viewBox="0 0 64 64" aria-hidden="true">
          <path d="M21 21 43 43M43 21 21 43" />
        </svg>
        """

    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(titulo)} • Sentinel-2 MT</title>
  <style>
    :root {{ color-scheme: light; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; min-height: 100vh; display: grid; place-items: center; padding: 28px;
      color: #17201d; background:
        radial-gradient(circle at 18% 18%, rgba(73,201,139,.24), transparent 30%),
        radial-gradient(circle at 82% 76%, rgba(15,117,72,.18), transparent 34%),
        linear-gradient(145deg, #edf5f1 0%, #f8fbfa 55%, #e5f0eb 100%);
    }}
    .orb {{ position: fixed; border-radius: 999px; filter: blur(1px); opacity: .45; }}
    .orb.one {{ width: 150px; height: 150px; top: -50px; right: 8%; background: #49c98b; }}
    .orb.two {{ width: 90px; height: 90px; bottom: 8%; left: 7%; background: #0f7548; }}
    main {{
      width: min(100%, 560px); position: relative; overflow: hidden; border-radius: 24px;
      background: rgba(255,255,255,.94); border: 1px solid rgba(200,214,209,.9);
      box-shadow: 0 28px 80px rgba(16,43,36,.16); backdrop-filter: blur(12px);
    }}
    .topo {{
      display: flex; align-items: center; gap: 12px; padding: 22px 26px;
      border-bottom: 1px solid #e2ebe7;
    }}
    .marca {{
      width: 42px; height: 42px; display: grid; place-items: center; border-radius: 14px;
      color: #0a2018; background: #49c98b; font-weight: 850; letter-spacing: -.04em;
      box-shadow: inset 0 0 0 1px rgba(10,32,24,.08);
    }}
    .produto {{ font-size: 15px; font-weight: 780; }}
    .produto small {{ display: block; margin-top: 2px; color: #596660; font-size: 11px; font-weight: 560; }}
    .conteudo {{ padding: 42px 42px 36px; text-align: center; }}
    .icone {{
      width: 88px; height: 88px; display: grid; place-items: center; margin: 0 auto 24px;
      border-radius: 28px; animation: entrar .55s cubic-bezier(.2,.8,.2,1) both;
    }}
    .sucesso .icone {{ color: #0f7548; background: #e0f7eb; box-shadow: 0 12px 30px rgba(15,117,72,.16); }}
    .erro .icone {{ color: #a33428; background: #fff0ee; box-shadow: 0 12px 30px rgba(143,46,36,.12); }}
    .icone svg {{ width: 54px; height: 54px; fill: none; stroke: currentColor; stroke-width: 5; stroke-linecap: round; stroke-linejoin: round; }}
    .etiqueta {{
      display: inline-flex; align-items: center; gap: 7px; padding: 7px 11px;
      border-radius: 999px; font-size: 12px; font-weight: 760;
    }}
    .sucesso .etiqueta {{ color: #135f3d; background: #e5f7ee; }}
    .erro .etiqueta {{ color: #8f2e24; background: #fff0ee; }}
    h1 {{ margin: 16px 0 12px; font-size: clamp(28px, 6vw, 38px); letter-spacing: -.045em; line-height: 1.05; }}
    p {{ max-width: 430px; margin: 0 auto; color: #485750; font-size: 16px; line-height: 1.65; }}
    .seguranca {{
      display: flex; align-items: center; justify-content: center; gap: 8px;
      margin: 24px auto 0; color: #315047; font-size: 12px; font-weight: 650;
    }}
    .seguranca::before {{ content: ""; width: 8px; height: 8px; border-radius: 50%; background: #49c98b; box-shadow: 0 0 0 5px #e5f7ee; }}
    footer {{
      padding: 17px 26px; text-align: center; color: #596660; background: #f6f9f8;
      border-top: 1px solid #e2ebe7; font-size: 12px;
    }}
    button {{
      margin-top: 28px; padding: 11px 18px; color: #fff; background: #0f7548;
      border: 0; border-radius: 11px; font: inherit; font-size: 13px; font-weight: 760;
      cursor: pointer; box-shadow: 0 8px 20px rgba(15,117,72,.2);
    }}
    button:hover {{ background: #0b633c; }}
    .erro button {{ background: #8f2e24; box-shadow: 0 8px 20px rgba(143,46,36,.18); }}
    .erro button:hover {{ background: #74241c; }}
    @keyframes entrar {{ from {{ opacity: 0; transform: scale(.72) rotate(-5deg); }} to {{ opacity: 1; transform: scale(1) rotate(0); }} }}
    @media (max-width: 520px) {{ .conteudo {{ padding: 34px 24px 30px; }} .topo {{ padding: 18px 20px; }} }}
    @media (prefers-reduced-motion: reduce) {{ * {{ animation: none !important; }} }}
  </style>
</head>
<body class="{classe}">
  <div class="orb one"></div><div class="orb two"></div>
  <main>
    <header class="topo">
      <div class="marca">S2</div>
      <div class="produto">Sentinel-2 MT<small>Downloader &amp; Sync</small></div>
    </header>
    <section class="conteudo">
      <div class="icone">{icone}</div>
      <span class="etiqueta">{html.escape(etiqueta)}</span>
      <h1>{html.escape(titulo)}</h1>
      <p>{html.escape(mensagem)}</p>
      <div class="seguranca">{html.escape(seguranca)}</div>
      <button type="button" onclick="window.close()">Fechar esta aba</button>
    </section>
    <footer>{html.escape(rodape)}</footer>
  </main>
</body>
</html>"""


class AplicacaoRetornoOAuth:
    def __init__(self, finalizar: Callable[[str], None]) -> None:
        self.finalizar = finalizar
        self.uri_retorno: str | None = None
        self.erro: Exception | None = None

    def __call__(self, ambiente, iniciar_resposta):
        self.uri_retorno = wsgiref.util.request_uri(ambiente)
        parametros = parse_qs(ambiente.get("QUERY_STRING", ""))
        detalhe = parametros.get("error_description", parametros.get("error", [""]))[0]
        sucesso = "code" in parametros and not detalhe

        if sucesso:
            try:
                resposta_segura = self.uri_retorno.replace("http://", "https://", 1)
                self.finalizar(resposta_segura)
            except Exception as erro:  # A página precisa responder mesmo se o Google falhar.
                self.erro = erro
                sucesso = False
                detalhe = "Falha ao concluir a troca segura do token."
        else:
            self.erro = ErroAutorizacaoOAuth(
                detalhe or "O retorno OAuth não contém um código de autorização."
            )

        conteudo = pagina_retorno_oauth(sucesso, detalhe).encode("utf-8")
        iniciar_resposta(
            "200 OK",
            [
                ("Content-Type", "text/html; charset=utf-8"),
                ("Content-Length", str(len(conteudo))),
                ("Cache-Control", "no-store"),
                ("X-Content-Type-Options", "nosniff"),
                (
                    "Content-Security-Policy",
                    "default-src 'none'; style-src 'unsafe-inline'; "
                    "script-src 'unsafe-inline'",
                ),
            ],
        )
        return [conteudo]


class ManipuladorOAuthSilencioso(wsgiref.simple_server.WSGIRequestHandler):
    def log_message(self, formato, *argumentos) -> None:
        return None


def autorizar_com_pagina_oauth(fluxo):
    aplicacao = AplicacaoRetornoOAuth(
        lambda resposta: fluxo.fetch_token(authorization_response=resposta)
    )
    servidor = wsgiref.simple_server.make_server(
        "127.0.0.1",
        0,
        aplicacao,
        handler_class=ManipuladorOAuthSilencioso,
    )
    try:
        fluxo.redirect_uri = f"http://127.0.0.1:{servidor.server_port}/"
        url, _ = fluxo.authorization_url(prompt="select_account")
        if not webbrowser.open(url, new=1, autoraise=True):
            print(f"[OAUTH] Abra este endereço no navegador: {url}")
        servidor.timeout = 300
        servidor.handle_request()
        if aplicacao.uri_retorno is None:
            raise ErroAutorizacaoOAuth(
                "Tempo esgotado aguardando o retorno do navegador. Tente novamente."
            )
        if aplicacao.erro:
            raise aplicacao.erro
        return fluxo.credentials
    finally:
        servidor.server_close()
