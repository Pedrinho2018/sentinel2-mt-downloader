# Interface gráfica Qt para gerar o config.yaml

Este projeto inclui uma interface desktop em Python usando Qt for Python (PySide6) para criar o arquivo `config/config.yaml` de forma visual.

## Como executar

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements-gui.txt
python src/gerar_config_gui.py
```

## O que ela faz

- abre uma janela desktop em Qt
- carrega um mapa OpenStreetMap em um navegador embutido
- permite desenhar uma região e capturar a bounding box
- gera um arquivo `config.yaml` compatível com o downloader Sentinel-2 MT
- salva também outros campos importantes do projeto, como:
  - período
  - coleção STAC
  - pasta de download
  - catálogo
  - limites de nuvem
  - preview e tamanho de chunk
  - JSON OAuth, token, pasta remota e tamanho dos lotes de sincronização

## Observação

A seleção de área usa um mapa gratuito do OpenStreetMap, sem necessidade de chave de API. A aplicação fica em Qt, em vez de navegador, para funcionar como desktop nativo.

O arquivo OAuth pode ser escolhido na própria interface. O token é criado automaticamente no primeiro login e não deve ser versionado.
