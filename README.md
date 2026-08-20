# Sentinel-2 MT Downloader

Downloader automatizado de imagens Sentinel-2 para o estado de Mato Grosso (MT), usando a API STAC do INPE/Brazil Data Cube.

## Objetivo

Nesta primeira fase, o projeto cuida somente da **coleta e catalogação das imagens** que serão usadas posteriormente na análise de áreas de soja em Mato Grosso.

## Fonte de dados

- INPE / Brazil Data Cube
- STAC: `https://data.inpe.br/bdc/stac/v1/`
- Coleção inicial: `S2-16D-2`
- Sentinel-2 MSI Level-2A
- Resolução espacial: 10 m
- Composição temporal: 16 dias

## Estrutura

```text
sentinel2-mt-downloader/
├── config/
│   └── config.yaml
├── data/                 # ignorado pelo Git
├── catalogo/             # CSV gerado pelo script
├── src/
│   └── baixar_inpe_mt.py
├── .gitignore
├── requirements.txt
└── README.md
```

## Instalação

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Teste sem baixar imagens

```powershell
python src\baixar_inpe_mt.py
```

Por padrão, o script consulta o INPE, limita a quantidade de itens para teste e gera `catalogo/catalogo_imagens.csv`, mas não baixa os GeoTIFFs.

## Baixar arquivos

```powershell
python src\baixar_inpe_mt.py --baixar --max-itens 5
```

Para alterar período, bandas e limites usados na busca, edite `config/config.yaml`.

## Importante

Os arquivos `.tif/.tiff` não são enviados ao GitHub. O repositório guarda o código e o catálogo para que o dataset possa ser reproduzido sem transformar o GitHub em armazenamento de imagens de satélite.
