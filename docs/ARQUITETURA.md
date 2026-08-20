# Arquitetura do pipeline

## Objetivo

Construir um fluxo reproduzível para seleção, download, controle de qualidade e preparação de imagens Sentinel-2 destinadas à análise agrícola e catalogação de áreas de soja em Mato Grosso.

## Fluxo

```text
INPE / Brazil Data Cube (STAC)
        |
        v
S2-16D-2 - Sentinel-2 L2A / 10 m / composição 16 dias
        |
        v
Filtro de cena usando SCL
        |
        +--> cena acima do limite de nuvem/sombra -> descartada
        |
        v
B02 + B03 + B04 + B08 + NDVI + SCL
        |
        v
Cena original preservada
        |
        v
Patches 256 x 256 px (~2,56 km x 2,56 km)
        |
        v
Filtro de qualidade por patch
        |
        +--> nuvem/sombra > limite -> descartado
        +--> dados válidos < limite -> descartado
        |
        v
preview_rgb.jpg para catalogação/rotulagem
        |
        v
catalogo_patches.csv
        |
        v
LabelImage / etapa de classificação
```

## Por que não classificar a cena inteira?

Uma cena do BDC cobre uma área muito maior do que uma unidade adequada de rotulagem visual. Os patches transformam a cena em amostras pequenas, rastreáveis e georreferenciadas, preservando a ligação com a imagem original.

## Rastreabilidade

Cada patch recebe um identificador determinístico com:

- ID da cena;
- linha e coluna do recorte;
- offset em pixels;
- data;
- percentual de nuvem/sombra;
- percentual de dados válidos;
- bounding box WGS84;
- centroide WGS84;
- caminho do preview;
- campos de label e observação.

Isso permite voltar do rótulo visual até os pixels científicos originais sem duplicar todo o dataset.

## Estratégia de armazenamento

Por padrão, os patches exportam somente `preview_rgb.jpg`. Os GeoTIFFs originais continuam preservados em `data/sentinel2/`.

Se for necessário criar GeoTIFFs por patch, altere:

```yaml
patches:
  exportar_tifs: true
```

Essa decisão reduz drasticamente duplicação e mantém o pipeline escalável.

## Próximas camadas recomendadas

1. Limite oficial de Mato Grosso para remover patches fora do estado.
2. Máscara de área agrícola para reduzir floresta, água e área urbana antes da rotulagem.
3. Organização temporal por safra e região produtora.
4. Controle de versões do dataset de labels.
5. Separação espacial entre treino, validação e teste para evitar vazamento geográfico.
6. Métricas de qualidade e auditoria do conjunto rotulado.
