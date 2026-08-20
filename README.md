# Sentinel-2 MT Downloader

Pipeline reproduzível de preparação de imagens Sentinel-2 para análise agrícola e catalogação de soja em Mato Grosso.

## Fonte correta

O pipeline principal usa agora:

- INPE / Brazil Data Cube STAC
- coleção `S2_L2A-1`
- Sentinel-2 Level-2A Surface Reflectance
- Cloud Optimized GeoTIFF (COG)
- cenas reais disponibilizadas continuamente

O antigo `S2-16D-2` não é mais usado como fonte principal para remover nuvens. Ele já é uma composição de 16 dias e, em meses muito nublados, oferece poucas observações independentes.

## Pipeline

```text
S2_L2A-1
   ↓
cenas reais por MGRS tile + mês
   ↓
avalia até 10 candidatos/mês
   ↓
guarda até 6 fontes complementares/mês
   ↓
SCL 20 m -> grade 10 m (nearest)
   ↓
buffer de ~60 m em nuvem/sombra
   ↓
best-pixel limpo
   ↓
B02 + B03 + B04 + B08 da MESMA cena-fonte
   ↓
NDVI calculado pelo pipeline
   ↓
mosaico mensal
   ↓
VALID_MASK + OBS_COUNT + SOURCE_INDEX
   ↓
somente mosaico APROVADO
   ↓
patches 128x128
   ↓
preview 768x768
   ↓
LabelImage
```

## Bandas

Baixadas das cenas L2A:

- `B02` azul - 10 m
- `B03` verde - 10 m
- `B04` vermelho - 10 m
- `B08` infravermelho próximo - 10 m
- `SCL` classificação da cena - 20 m

Gerada pelo pipeline:

- `NDVI`, calculado com B08 e B04 depois da composição

## Controle de nuvens

O SCL de 20 m é reprojetado para a mesma grade dos dados de 10 m por vizinho mais próximo.

São rejeitados:

- sombra de nuvem;
- pixel não classificado/suspeito;
- nuvem média;
- nuvem alta;
- cirrus;
- neve/gelo.

Depois é aplicado um buffer de aproximadamente 60 m ao redor da contaminação.

## Coerência espectral

Quando um pixel limpo é escolhido, `B02`, `B03`, `B04` e `B08` vêm todos da mesma passagem Sentinel-2. O pipeline não mistura bandas de datas diferentes no mesmo pixel.

Também não escolhe o maior NDVI, evitando favorecer artificialmente vegetação mais verde.

## Produtos do mosaico

```text
data/mosaicos_temporais/<MGRS_TILE>/<AAAA-MM>/
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

- `VALID_MASK`: pixels realmente preenchidos com observação limpa.
- `OBS_COUNT`: número de observações limpas disponíveis para cada pixel.
- `SOURCE_INDEX`: de qual cena real veio o pixel escolhido.
- `metadata.json`: mapeia o índice para item STAC, data e qualidade.

Mosaico com menos de 98% de cobertura válida recebe status `cobertura_insuficiente` e **não gera patches**.

## Patches para LabelImage

- 128x128 pixels Sentinel-2
- aproximadamente 1,28 x 1,28 km
- preview 768x768 por nearest-neighbor
- pelo menos 99,5% de pixels válidos
- pelo menos 20% do patch com duas ou mais observações limpas disponíveis

## Teste

```powershell
git checkout feature/pipeline-premium
git pull
pip install -r requirements.txt
python src\pipeline.py --max-tiles 1 --max-patches 20 --limpar
```

No teste, o sistema usa um recorte pequeno no centro-norte de Mato Grosso e escolhe automaticamente o MGRS tile com melhor cobertura temporal.

Primeiro confira os mosaicos:

```text
data/mosaicos_temporais/<TILE>/<AAAA-MM>/preview_rgb.jpg
```

Depois confira os patches:

```text
data/patches/<TILE>/<AAAA-MM>/<PATCH_ID>/preview_rgb.jpg
```

## Estrutura

```text
sentinel2-mt-downloader/
├── config/
│   └── config.yaml
├── src/
│   ├── baixar_series_temporais.py
│   ├── gerar_mosaicos_temporais.py
│   ├── gerar_patches.py
│   ├── validar_dataset.py
│   └── pipeline.py
├── data/
│   ├── sentinel2_l2a/
│   ├── mosaicos_temporais/
│   └── patches/
├── catalogo/
└── requirements.txt
```

## Escala futura

Só depois da validação visual do tile de teste:

```powershell
python src\pipeline.py --max-tiles 0 --max-patches 0
```

Não rode Mato Grosso inteiro antes de validar qualidade e armazenamento.
