# Sentinel-2 MT Downloader

Pipeline para gerar imagens limpas de Sentinel-2 voltadas à catalogação e futura classificação de soja em Mato Grosso.

## Objetivo atual

Preparar uma fila de **5.000 imagens** distribuídas entre setembro/2025 e abril/2026, mantendo qualidade visual, rastreabilidade temporal e separação espacial correta para Machine Learning.

## Arquitetura

```text
Planetary Computer STAC
        ↓
Sentinel-2 L2A
        ↓
ESA WorldCover 2021
classe 40 = cropland
(PRÉ-FILTRO, não rótulo de soja)
        ↓
localizações agrícolas candidatas
        ↓
para cada mês da safra:
  várias cenas Sentinel-2
        ↓
para cada patch 128x128 px (~1,28 km):
  Data API NPY lê só o recorte remoto
        ↓
SCL remove nuvem/sombra
        ↓
combinação de pixels limpos de várias datas
        ↓
>= 99,5% de cobertura limpa
        ↓
preview_rgb.jpg
        ↓
fila_catalogacao_5000.csv
```

O pipeline **não baixa cenas completas** e **não usa Rasterio/GDAL** no fluxo principal.

## Dataset de 5.000 imagens

O período padrão possui 8 meses:

| Mês | Meta |
| --- | ---: |
| 2025-09 | 625 |
| 2025-10 | 625 |
| 2025-11 | 625 |
| 2025-12 | 625 |
| 2026-01 | 625 |
| 2026-02 | 625 |
| 2026-03 | 625 |
| 2026-04 | 625 |
| **Total** | **5.000** |

As localizações são embaralhadas de forma determinística para evitar pegar apenas uma faixa do AOI. O mesmo `spatial_id` é priorizado ao longo dos meses para formar séries temporais.

## Split sem vazamento espacial

O conjunto é dividido pelo `spatial_id`, nunca aleatoriamente por imagem:

- 70% treino;
- 15% validação;
- 15% teste.

Um mesmo local geográfico nunca pode aparecer em mais de um conjunto, mesmo em meses diferentes.

## Classes para catalogação

A fila nasce com o rótulo vazio e status `PENDENTE`.

Classes previstas:

- `SOJA`
- `NAO_SOJA`
- `INCERTO`

`INCERTO` deve ser revisado antes de entrar no treinamento final.

## Pré-filtro agrícola

O pipeline usa ESA WorldCover como filtro de área agrícola:

- coleção: `esa-worldcover`;
- ano de referência: 2021;
- classe cropland: `40`;
- mínimo atual: 35% de cropland dentro do patch.

Esse dado **não determina que existe soja**. Ele serve apenas para reduzir patches dominados por floresta, água e áreas urbanas antes da rotulagem humana.

Os resultados da máscara são cacheados em:

```text
catalogo/cache_mascara_agricola.csv
```

## Instalação

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Teste rápido

```powershell
python src\pipeline.py --mes 2026-04 --max-patches 5 --limpar
```

Saída:

```text
data/patches_soja/2026-04/.../preview_rgb.jpg
```

## Produção das 5.000 imagens

Primeira execução:

```powershell
python src\pipeline.py --dataset-5000 --limpar
```

Se a execução for interrompida, **não use `--limpar` novamente**. Basta continuar:

```powershell
python src\pipeline.py --dataset-5000
```

O catálogo é salvo durante a execução, então o trabalho já concluído é reutilizado.

## Saída do dataset principal

```text
data/
└── dataset_soja_5000/
    ├── 2025-09/
    ├── 2025-10/
    ├── 2025-11/
    ├── 2025-12/
    ├── 2026-01/
    ├── 2026-02/
    ├── 2026-03/
    └── 2026-04/
```

Cada patch contém, por padrão:

```text
preview_rgb.jpg
```

Os arquivos científicos não são persistidos por padrão para evitar ocupar espaço desnecessário.

## Catálogos

```text
catalogo/catalogo_soja_5000.csv
catalogo/fila_catalogacao_5000.csv
catalogo/resumo_soja_5000.json
```

### `catalogo_soja_5000.csv`

Registra:

- `patch_id`;
- `spatial_id`;
- mês;
- split;
- percentual de cropland;
- coordenadas;
- cobertura limpa;
- redundância temporal;
- cenas-fonte utilizadas;
- datas das fontes;
- NDVI médio;
- preview;
- label;
- status de rotulagem;
- revisor;
- observação.

### `fila_catalogacao_5000.csv`

É a planilha operacional para a etapa humana de catalogação.

## Scripts principais

```text
src/gerar_dataset_soja.py   # teste e geração enxuta
src/gerar_dataset_5000.py   # produção balanceada das 5.000 imagens
src/pipeline.py              # comando único
```

Os scripts antigos estão desativados e permanecem apenas como histórico do desenvolvimento.

## Testes automatizados

O GitHub Actions valida:

- instalação das dependências;
- sintaxe/imports;
- acesso real ao Planetary Computer Data API;
- geração real de patch Sentinel-2;
- geração real de patch com máscara agrícola ativa.

## Fontes de dados

- Microsoft Planetary Computer — `sentinel-2-l2a`;
- ESA WorldCover — `esa-worldcover`.
