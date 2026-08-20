# Sentinel-2 MT Downloader

Pipeline reproduzível de preparação de imagens Sentinel-2 para análise agrícola em Mato Grosso, usando a API STAC do INPE/Brazil Data Cube.

## Objetivo

Preparar dados para análise de áreas agrícolas e vigor da vegetação ao longo da safra, com foco posterior em soja.

O projeto não usa mais uma cena isolada como unidade final de catalogação. A estratégia atual é:

1. coletar **séries temporais por tile e mês**;
2. combinar observações complementares;
3. gerar **mosaicos mensais sem nuvens por best-pixel**;
4. recortar os mosaicos em patches georreferenciados;
5. auditar o dataset antes da rotulagem no LabelImage.

## Fonte de dados

- INPE / Brazil Data Cube
- STAC: `https://data.inpe.br/bdc/stac/v1/`
- Coleção: `S2-16D-2`
- Sentinel-2 MSI Level-2A
- Resolução espacial principal: 10 m
- Composição temporal de origem: 16 dias

## Bandas utilizadas

- `B02` = azul
- `B03` = verde
- `B04` = vermelho
- `B08` = infravermelho próximo
- `NDVI` = índice de vegetação
- `SCL` = classificação de cena usada para rejeitar nuvens/sombras pixel a pixel

## Arquitetura atual

```text
INPE / Sentinel-2
        ↓
Catálogo STAC
        ↓
Agrupamento por TILE + MÊS
        ↓
2 cenas-fonte por tile/mês
        ↓
SCL de cada fonte
        ↓
┌────────────────────────────────────────────┐
│ MOSAICO TEMPORAL MENSAL                   │
│                                            │
│ Para cada pixel:                           │
│ - descarta nuvem/sombra/cirrus             │
│ - aplica margem de segurança               │
│ - escolhe observação limpa mais próxima    │
│   do centro do mês                         │
│ - mantém todas as bandas da MESMA fonte    │
└────────────────────────────────────────────┘
        ↓
B02 / B03 / B04 / B08 / NDVI
VALID_MASK / OBS_COUNT / SOURCE_INDEX
        ↓
Mosaico mensal limpo e rastreável
        ↓
Patches 128x128 px (~1,28 km)
        ↓
Preview 768x768 para catalogação
        ↓
Catálogo CSV georreferenciado
        ↓
Auditoria
        ↓
LabelImage / rotulagem
```

## Por que mudar para mosaico temporal?

Uma cena com poucos por cento de nuvem ainda pode apresentar milhares de pequenas nuvens espalhadas. Para agricultura isso atrapalha a inspeção visual e pode contaminar o treinamento.

O mosaico temporal resolve isso usando **datas complementares do mesmo tile**. Quando uma data está nublada em um pixel, o pipeline tenta preencher aquele local com uma observação limpa de outra data do mesmo mês.

Importante: o algoritmo não escolhe o maior NDVI nem o pixel "mais verde", porque isso enviesaria a análise de vigor. A prioridade é a observação limpa mais próxima do centro do mês, preservando coerência temporal e espectral.

## Produtos do mosaico

Cada `tile/mês` gera:

```text
data/mosaicos_temporais/TILE/YYYY-MM/
├── B02.tif
├── B03.tif
├── B04.tif
├── B08.tif
├── NDVI.tif
├── VALID_MASK.tif
├── OBS_COUNT.tif
├── SOURCE_INDEX.tif
├── preview_rgb.jpg
└── metadata.json
```

- `VALID_MASK`: pixels que receberam uma observação limpa.
- `OBS_COUNT`: quantidade de observações limpas disponíveis para cada pixel.
- `SOURCE_INDEX`: identifica qual cena forneceu o pixel usado no mosaico.
- `metadata.json`: mapeia cada índice para a cena/data original.

Isso permite rastrear qualquer pixel de volta à fonte.

## Estrutura do repositório

```text
sentinel2-mt-downloader/
├── config/
│   └── config.yaml
├── data/                         # ignorado pelo Git
│   ├── sentinel2/
│   ├── mosaicos_temporais/
│   └── patches/
├── catalogo/
│   ├── catalogo_series.csv
│   ├── catalogo_mosaicos.csv
│   ├── catalogo_patches.csv
│   └── relatorio_validacao.json
├── src/
│   ├── baixar_series_temporais.py
│   ├── gerar_mosaicos_temporais.py
│   ├── gerar_patches.py
│   ├── validar_dataset.py
│   ├── pipeline.py
│   └── baixar_inpe_mt.py        # downloader antigo/auxiliar
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

## Teste recomendado

Primeiro atualize a branch:

```powershell
git pull
```

Depois execute o pipeline completo em **um tile** e limite o resultado a 20 patches:

```powershell
python src\pipeline.py --max-tiles 1 --max-patches 20 --limpar
```

O processo executa automaticamente:

```text
baixar_series_temporais.py
        ↓
gerar_mosaicos_temporais.py
        ↓
gerar_patches.py
        ↓
validar_dataset.py
```

## Executar por etapas

### 1. Coletar a série temporal

```powershell
python src\baixar_series_temporais.py --max-tiles 1 --cenas-por-mes 2
```

Para um tile específico:

```powershell
python src\baixar_series_temporais.py --tile 014018 --max-tiles 1 --cenas-por-mes 2
```

### 2. Gerar mosaicos mensais

```powershell
python src\gerar_mosaicos_temporais.py --limpar-saida
```

### 3. Gerar 20 patches de teste

```powershell
python src\gerar_patches.py --max-patches 20 --limpar-saida
```

### 4. Auditar

```powershell
python src\validar_dataset.py
```

## Catálogo dos patches

Cada patch registra:

- `patch_id`;
- `mosaic_id`;
- tile;
- mês da safra;
- offsets no mosaico;
- tamanho;
- percentual de dados válidos;
- percentual de pixels com duas ou mais observações limpas;
- bounding box WGS84;
- coordenada central;
- caminho do preview;
- `label`;
- `observacao`.

## Escala futura

Depois de validar visualmente um tile, o mesmo pipeline pode ser expandido para todos os tiles de Mato Grosso:

```powershell
python src\pipeline.py --max-tiles 0 --max-patches 0
```

**Não execute o estado inteiro antes de validar o teste.** O volume de dados pode ser muito grande.

## GitHub

GeoTIFFs e previews permanecem fora do Git. O repositório guarda código, configuração, catálogos e metodologia para tornar o dataset reproduzível sem usar o GitHub como armazenamento de imagens de satélite.
