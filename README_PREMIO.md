# Visão de qualidade do projeto

Este repositório não deve ser tratado apenas como um script de download. A proposta é manter uma cadeia de preparação de dados auditável e reproduzível para imagens Sentinel-2 usadas em análise agrícola.

## Princípios

- Dados científicos originais preservados.
- Controle de qualidade antes e depois do download.
- Unidades de catalogação pequenas e georreferenciadas.
- Rastreabilidade entre patch, cena e coordenadas.
- Configuração versionada.
- Separação entre visualização (`preview_rgb.jpg`) e dados científicos (`GeoTIFF`).
- Processamento incremental para evitar desperdício de armazenamento e banda.
- Dataset de labels versionável e auditável.

## Resultado esperado

Cada amostra rotulada deve responder, no mínimo:

1. De qual cena Sentinel-2 veio?
2. Qual a data da observação?
3. Onde está no mapa?
4. Qual era o percentual de nuvem/sombra?
5. Quais pixels da cena original correspondem à amostra?
6. Qual preview foi apresentado ao anotador?
7. Qual label foi atribuído e qual observação foi registrada?

Essa rastreabilidade é essencial para transformar um conjunto de imagens em um dataset científico defensável.
