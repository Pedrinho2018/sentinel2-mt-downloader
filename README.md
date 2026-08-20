# Sentinel-2 MT Downloader

Pipeline enxuto para gerar imagens limpas de Sentinel-2 voltadas à catalogação e futura classificação de soja em Mato Grosso.

## O que mudou

O projeto **não baixa mais cenas completas** do Sentinel-2 para o computador.

A fonte operacional passou a ser a coleção pública `sentinel-2-l2a` da Microsoft Planetary Computer. Os dados são Cloud Optimized GeoTIFF (COG), então o script lê apenas o pequeno recorte necessário de cada banda pela internet.

## Pipeline atual

```text
Planetary Computer STAC
        ↓
Sentinel-2 L2A
        ↓
busca por mês + AOI
        ↓
seleciona cenas com menor cobertura de nuvem
        ↓
para cada patch de 128x128 px (~1,28 km):
  lê somente aquele recorte remoto
        ↓
SCL remove nuvem/sombra
        ↓
combina pixels limpos de várias datas
        ↓
exige >= 99,5% de cobertura limpa
        ↓
preview_rgb.jpg
        ↓
catalogo_soja.csv
        ↓
LabelImage / rotulagem
```

## Por que essa arquitetura

- não ocupa dezenas ou centenas de GB com cenas completas;
- o filtro de nuvem é feito no **patch**, não pela porcentagem da cena inteira;
- várias datas do mesmo mês podem preencher pixels nublados;
- B02/B03/B04/B08 de um pixel vêm da mesma observação;
- NDVI é calculado depois da composição;
- cada patch registra os IDs das cenas usadas, permitindo reproduzir os dados.

## Estrutura principal

```text
config/
└── config.yaml

src/
├── gerar_dataset_soja.py   # script principal
└── pipeline.py              # atalho para o script principal

data/
└── patches_soja/            # somente recortes aprovados

catalogo/
├── catalogo_soja.csv
└── resumo_soja.json
```

Os scripts antigos permanecem apenas como histórico de desenvolvimento e não fazem parte do fluxo principal.

## Instalação

```powershell
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Primeiro teste

Teste somente abril de 2026 e gere no máximo 5 patches:

```powershell
python src\pipeline.py --mes 2026-04 --max-patches 5 --limpar
```

Confira os arquivos:

```text
data/patches_soja/2026-04/.../preview_rgb.jpg
```

Se esses previews estiverem visualmente bons, aumente para 20:

```powershell
python src\pipeline.py --mes 2026-04 --max-patches 20 --limpar
```

Somente depois de validar a qualidade visual deve-se processar toda a safra:

```powershell
python src\pipeline.py --max-patches 20 --limpar
```

## Configuração

O teste usa um recorte pequeno no centro-norte de Mato Grosso, em região agrícola. O AOI, período, limite de nuvem e tamanho de patch ficam em `config/config.yaml`.

O padrão atual salva apenas `preview_rgb.jpg` e o catálogo para economizar espaço. Se futuramente for necessário armazenar os arrays científicos B02/B03/B04/B08/NDVI, altere `salvar_npz` para `true`.

## Fonte

Microsoft Planetary Computer — coleção `sentinel-2-l2a`.
