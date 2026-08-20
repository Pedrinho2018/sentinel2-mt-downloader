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
│   └── baixar_inpe_mt.py
├── .gitignore
├── requirements.txt
└── README.md
```

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

## Importante

Os arquivos `.tif/.tiff` e os previews gerados não são enviados ao GitHub. O repositório guarda o código e o catálogo para que o dataset possa ser reproduzido sem transformar o GitHub em armazenamento de imagens de satélite.
