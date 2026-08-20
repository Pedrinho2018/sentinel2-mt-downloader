# Visão de qualidade do projeto

Este repositório não é apenas um downloader. Ele implementa uma cadeia auditável e reproduzível de preparação de dados Sentinel-2 para análise agrícola e acompanhamento do vigor da vegetação ao longo da safra.

## Princípios

- Dados científicos originais preservados.
- Separação entre cena-fonte, mosaico temporal e patch de catalogação.
- Nuvem/sombra removida pixel a pixel usando SCL e margem de segurança.
- Nenhuma escolha de pixel baseada em "maior NDVI" ou "mais verde", evitando viés artificial de vigor.
- Todas as bandas de um pixel do mosaico vêm da mesma cena-fonte.
- Composição mensal para preservar a dinâmica temporal da safra.
- Processamento em blocos para suportar rasters grandes sem exigir memória excessiva.
- Rastreabilidade por `SOURCE_INDEX.tif` e `metadata.json`.
- `OBS_COUNT.tif` registra redundância temporal de observações limpas.
- Patches pequenos, georreferenciados e adequados para anotação.
- Configuração versionada.
- Auditoria automática antes da rotulagem/ML.
- Dados pesados fora do Git; código e metodologia versionados.

## Cadeia de proveniência

Cada patch deve permitir responder:

1. Qual é o tile Sentinel-2?
2. Qual mês da safra ele representa?
3. De qual mosaico temporal veio?
4. Qual sua localização WGS84?
5. Qual percentual do patch possui dados válidos?
6. Quantos pixels possuem duas ou mais observações limpas disponíveis?
7. Qual preview foi apresentado ao anotador?
8. Qual label foi atribuído?

No nível do mosaico, cada pixel deve permitir responder:

1. Qual cena-fonte forneceu o pixel?
2. Qual era a data dessa cena?
3. Qual foi a cobertura de nuvem/sombra estimada da cena-fonte?
4. Quantas observações limpas estavam disponíveis naquele pixel?

## Produtos de rastreabilidade

```text
mosaico/
├── B02.tif
├── B03.tif
├── B04.tif
├── B08.tif
├── NDVI.tif
├── VALID_MASK.tif
├── OBS_COUNT.tif
├── SOURCE_INDEX.tif
└── metadata.json
```

`SOURCE_INDEX.tif` aponta para o registro correspondente em `metadata.json`, evitando que o mosaico se torne uma imagem sem proveniência científica.

## Resultado esperado

O objetivo é entregar um dataset temporal defensável, em que qualidade, origem e transformação de cada amostra possam ser auditadas antes do treinamento de qualquer modelo.
