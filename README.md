# Sentinel-2 MT Downloader

Pipeline de coleta e preparação de imagens Sentinel-2 para análise agrícola no estado de Mato Grosso (MT), usando a API STAC do INPE/Brazil Data Cube.

## Objetivo

Preparar um dataset reproduzível para análise de áreas agrícolas e vigor da vegetação ao longo da safra, com foco posterior em soja.

O pipeline separa duas etapas:

1. **Cena-mãe**: download das bandas científicas e controle de qualidade.
2. **Patch de catalogação**: recorte pequeno, georreferenciado e adequado para rotulagem no LabelImage.

## Fonte de dados

- INPE / Brazil Data Cube
- STAC: `https://data.inpe.br/bdc/stac/v1/`
- Coleção: `S2-16D-2`
- Sentinel-2 MSI Level-2A
- Resolução espacial principal: 10 m
- Composição temporal: 16 dias

## Bandas utilizadas

- `B02` = azul
- `B03` = verde
- `B04` = vermelho
- `B08` = infravermelho próximo
- `NDVI` = índice de vegetação
- `SCL` = classificação de cena usada no filtro de nuvem/sombra

## Fluxo

```text
INPE / Sentinel-2
        ↓
Busca em Mato Grosso
        ↓
Filtro de nuvem/sombra da cena via SCL
        ↓
B02 + B03 + B04 + B08 + NDVI
        ↓
Cena-mãe
        ↓
Patches 128x128 px (~1,28 km por lado)
        ↓
Filtro de qualidade por patch
  - nuvem/sombra <= 8%
  - dados válidos >= 90%
        ↓
Preview RGB 768x768 px
        ↓
Catálogo CSV georreferenciado
        ↓
LabelImage / etapa de rotulagem
```

## Por que o preview é 768x768?

O dado científico continua sendo um recorte de **128x128 pixels Sentinel-2**. O JPG é ampliado para `768x768` apenas para facilitar a inspeção visual e a catalogação.

A ampliação usa `NEAREST`, preservando os pixels originais e evitando criar falsa sensação de resolução espacial adicional.

## Estrutura

```text
sentinel2-mt-downloader/
├── config/
│   └── config.yaml
├── data/
│   ├── sentinel2/          # cenas-mãe; ignoradas pelo Git
│   └── patches/            # previews dos patches; ignorados pelo Git
├── catalogo/
│   ├── catalogo_imagens.csv
│   ├── catalogo_patches.csv
│   └── resumo_patches.json
├── src/
│   ├── baixar_inpe_mt.py
│   ├── gerar_patches.py
│   └── validar_dataset.py
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

## 1. Baixar cenas boas

Teste com uma cena nova:

```powershell
python src\baixar_inpe_mt.py --baixar --max-itens 1
```

Baixar cinco cenas novas aprovadas:

```powershell
python src\baixar_inpe_mt.py --baixar --max-itens 5
```

O downloader pula cenas completas que já existem no disco e continua procurando até atingir a meta de novas cenas.

## 2. Gerar patches próximos

Teste primeiro com 20 patches:

```powershell
python src\gerar_patches.py --max-patches 20
```

Cada patch aprovado gera um `preview_rgb.jpg` e uma linha no `catalogo/catalogo_patches.csv` contendo:

- ID do patch e da cena;
- data;
- linha/coluna e offsets do raster original;
- tamanho do patch;
- área aproximada no terreno;
- percentual de nuvem/sombra;
- percentual de dados válidos;
- bounding box WGS84;
- coordenada central;
- caminho do preview;
- campos `label` e `observacao`.

## 3. Validar o dataset

```powershell
python src\validar_dataset.py
```

A validação ajuda a detectar inconsistências antes da etapa de rotulagem ou treinamento.

## Importante

Os GeoTIFFs e previews não são enviados ao GitHub. O repositório mantém código, configuração e catálogos para que o dataset possa ser reproduzido sem usar o GitHub como armazenamento de imagens de satélite.
