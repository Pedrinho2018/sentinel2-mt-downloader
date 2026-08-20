# Sentinel-2 MT Downloader

Downloader automatizado de imagens Sentinel-2 para o estado de Mato Grosso (MT), usando a API STAC do INPE/Brazil Data Cube.

## Objetivo

Nesta primeira fase, o projeto cuida somente da **coleta, catalogação e conferência visual das imagens** que serão usadas posteriormente na análise de áreas de soja em Mato Grosso.

## Fonte de dados

- INPE / Brazil Data Cube
- STAC: `https://data.inpe.br/bdc/stac/v1/`
- Coleção inicial: `S2-16D-2`
- Sentinel-2 MSI Level-2A
- Resolução espacial: 10 m
- Composição temporal: 16 dias

## Importante sobre as imagens escuras

Os arquivos `B02.tif`, `B03.tif`, `B04.tif`, `B08.tif` e `NDVI.tif` são **bandas científicas individuais**, não fotografias RGB prontas. Quando abertas isoladamente em um visualizador comum, podem parecer muito escuras ou em tons de cinza.

Depois do download, o programa combina automaticamente:

- `B04` = vermelho
- `B03` = verde
- `B02` = azul

O resultado é salvo como:

```text
preview_rgb.jpg
```

Esse arquivo serve para conferência visual. Os GeoTIFFs originais continuam sendo preservados para a futura análise de soja e Machine Learning.

## Estrutura

```text
sentinel2-mt-downloader/
├── config/
│   └── config.yaml
├── data/                 # ignorado pelo Git
├── catalogo/             # CSV gerado pelo script
├── src/
│   ├── baixar_inpe_mt.py
│   ├── tui.py
│   └── sentinel2_mt/
│       ├── catalogo.py
│       ├── cli.py
│       ├── configuracao.py
│       ├── google_drive.py
│       ├── http.py
│       ├── imagens.py
│       ├── modelos.py
│       └── servico.py
├── tests/
├── iniciar_tui.py
├── .gitignore
├── requirements.txt
└── README.md
```

### Organização de engenharia

- `ConfiguracaoProjeto` e suas classes imutáveis representam a configuração validada;
- `ServicoSentinel2` coordena o caso de uso de catalogação e download;
- `SincronizadorGoogleDrive` encapsula autenticação, pastas, lotes e uploads;
- `ClienteDownloadHTTP`, `ProcessadorImagem` e `RepositorioCatalogoCSV` são adaptadores especializados;
- `AplicacaoCLI` e `SentinelTUI` são interfaces que dependem dos serviços, sem conter regras de negócio;
- dependências substituíveis são recebidas pelos construtores, facilitando testes sem rede.

Cada cena baixada fica parecida com:

```text
data/sentinel2/2026-02-02/ID_DA_CENA/
├── B02.tif
├── B03.tif
├── B04.tif
├── B08.tif
├── NDVI.tif
└── preview_rgb.jpg
```

## Instalação

O inicializador cria o ambiente virtual e instala as dependências sem alterar o
Python do sistema:

```bash
python iniciar_tui.py
```

Na primeira execução, a instalação pode demorar alguns minutos. Nas próximas,
a TUI abre diretamente; as dependências só são atualizadas quando
`requirements.txt` mudar.

## Teste sem baixar imagens

```powershell
python src\baixar_inpe_mt.py
```

Por padrão, o script consulta o INPE, limita a quantidade de itens para teste e gera `catalogo/catalogo_imagens.csv`, mas não baixa os GeoTIFFs.

## Baixar 1 cena para validar

```powershell
python src\baixar_inpe_mt.py --baixar --max-itens 1
```

Depois abra o arquivo `preview_rgb.jpg` gerado dentro da pasta da cena.

## Baixar 5 cenas

```powershell
python src\baixar_inpe_mt.py --baixar --max-itens 5
```

Para alterar período, bandas, preview e limites usados na busca, edite `config/config.yaml`.

## Interface de terminal (TUI)

Abra a interface com um único comando:

```bash
python iniciar_tui.py
```

Pela interface é possível:

- catalogar cenas sem baixar arquivos;
- baixar imagens com período e limite personalizados;
- sincronizar com Google Drive apontando o JSON OAuth;
- definir o tamanho dos lotes de sincronização;
- acompanhar a saída em tempo real e cancelar a operação atual.

Use `Ctrl+Q` para sair e `Ctrl+L` para limpar o painel de logs.

## Testes

Os testes unitários não acessam o INPE nem o Google Drive:

```bash
.venv/bin/python -m unittest discover -v
```

No Windows, use `.venv\Scripts\python -m unittest discover -v`.

## Pacotes Linux e GitHub Releases

O workflow [packages.yml](.github/workflows/packages.yml) gera automaticamente:

- `sentinel2-mt-downloader_VERSION_amd64.deb`;
- `sentinel2-mt-downloader-VERSION-1.x86_64.rpm`;
- `sentinel2-mt-downloader-bin-VERSION-1-x86_64.pkg.tar.zst`;
- `PKGBUILD`, binário Linux x86_64 e `SHA256SUMS`.

Para publicar uma versão, atualize `__version__` em
`src/sentinel2_mt/__init__.py`, crie uma tag com a mesma versão e envie a tag:

```bash
git tag v2.0.0
git push origin v2.0.0
```

O GitHub Actions executa os testes, compila o binário, monta os pacotes e anexa
os artefatos à GitHub Release. Também é possível executar o workflow manualmente
para testar o build sem publicar uma Release. Consulte
`packaging/README.md` para detalhes.

## Sincronização em lotes com Google Drive

A sincronização usa diretamente a API do Google Drive, sem `rclone` ou outra
ferramenta externa. Copie `config/google-oauth.example.json` para
`config/google-oauth.json` e use o JSON de um cliente OAuth do tipo aplicativo
para computador criado no Google Cloud. O arquivo real não deve ser versionado.

Depois de instalar as dependências, basta executar:

```powershell
python src\baixar_inpe_mt.py --sincronizar
```

Na primeira execução, o navegador será aberto para autorização. O token será
salvo automaticamente em `config/google-token.json`; nas próximas execuções
ele será reutilizado. A estrutura de pastas é preservada e os arquivos
existentes são atualizados.

O projeto solicita o escopo `drive.file`, limitado aos arquivos criados ou
abertos pela própria aplicação. Se o consentimento OAuth estiver com status
`Testing`, adicione a conta que fará a sincronização à lista de usuários de
teste no Google Cloud.

Também é possível informar o JSON diretamente:

```powershell
python src\baixar_inpe_mt.py --sincronizar --oauth-json C:\caminho\client_secret.json
```

As imagens são enviadas em lotes de `sincronizacao.tamanho_lote` arquivos. Para
alterar o tamanho apenas em uma execução:

```powershell
python src\baixar_inpe_mt.py --sincronizar --lote 25
```

Por padrão são sincronizados `.tif`, `.tiff`, `.jpg` e `.jpeg`. A lista pode
ser ajustada pela chave `sincronizacao.extensoes`.

## Importante

Os arquivos `.tif/.tiff` e os previews gerados não são enviados ao GitHub. O repositório guarda o código e o catálogo para que o dataset possa ser reproduzido sem transformar o GitHub em armazenamento de imagens de satélite.
