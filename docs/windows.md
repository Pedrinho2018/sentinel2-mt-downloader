# Versão Windows x64

A versão Windows é distribuída como um pacote portátil. Não é necessário instalar Python, criar ambiente virtual ou executar comandos para usar a interface gráfica.

## Como usar

1. Baixe o arquivo `sentinel2-mt-X.Y.Z-windows-x64.zip` nos artefatos do GitHub Actions ou na GitHub Release.
2. Extraia **todo o conteúdo do ZIP** para uma pasta local.
3. Execute `Sentinel2-MT.exe`.
4. Configure área, período, bandas e filtros pela interface.
5. Use **Executar operação** para catalogar, baixar ou sincronizar.

Não mova somente o `Sentinel2-MT.exe`. A pasta `_internal`, o arquivo `sentinel2-mt.exe` e a pasta `config` fazem parte da aplicação.

## Arquivos principais

```text
Sentinel2-MT-Windows/
├── Sentinel2-MT.exe            # interface gráfica
├── sentinel2-mt.exe            # motor CLI/TUI usado pela GUI
├── sentinel2-mt-config.yaml    # configuração padrão para uso direto do motor
├── config/
│   ├── config.yaml
│   └── google-oauth.example.json
├── SHA256SUMS.txt
└── _internal/                  # bibliotecas do executável gráfico
```

## Google Drive

Para sincronizar com o Google Drive, use um JSON OAuth do tipo **Desktop app** e selecione o arquivo pela interface. O token gerado permanece local e não deve ser enviado ao GitHub.

## Linha de comando

O motor também pode ser usado pelo PowerShell ou Prompt de Comando:

```powershell
.\sentinel2-mt.exe --version
.\sentinel2-mt.exe --help
.\sentinel2-mt.exe --baixar --max-itens 1
```

Sem argumentos, `sentinel2-mt.exe` abre a interface de terminal (TUI).

## Windows SmartScreen

Os executáveis ainda não possuem assinatura digital Authenticode. Por isso, o Windows SmartScreen pode exibir um aviso de aplicativo não reconhecido. Confira o SHA-256 publicado junto com o pacote antes de executar.

## Dados baixados

Por padrão, imagens e catálogos são gravados dentro da pasta extraída:

```text
data/sentinel2/
catalogo/
```

Para uso contínuo, extraia o programa para uma pasta em que seu usuário tenha permissão de escrita, por exemplo `C:\Apps\Sentinel2-MT` ou uma pasta dentro do seu perfil de usuário.
