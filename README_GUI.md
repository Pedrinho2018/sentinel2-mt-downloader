# Interface gráfica integrada

O projeto inclui uma central desktop em Qt for Python (PySide6) para configurar,
executar e acompanhar o Sentinel-2 MT Downloader.

## Como executar

```bash
python iniciar_gui.py
```

O inicializador cria ou reaproveita a `.venv` e instala as dependências gráficas
sem modificar os pacotes Python do sistema. Para apenas preparar o ambiente:

```bash
python iniciar_gui.py --setup-only
```

## O que ela faz

- navegação por visão geral, área, qualidade, Google Drive e configuração;
- mapa OpenStreetMap com seleção da bounding box usando `Shift + arrastar`;
- execução integrada de catalogação, download e sincronização;
- log em tempo real, estado da operação e cancelamento seguro;
- escolha do JSON OAuth e sincronização em lotes;
- geração e revisão visual do `config.yaml`;
- perfis locais de região em SQLite, sem armazenar OAuth ou tokens;
- abertura da pasta de imagens e visualização dos previews RGB.

## Observação

A seleção de área usa o OpenStreetMap sem chave de API. Se o mapa estiver sem
conexão, as quatro coordenadas ainda podem ser preenchidas manualmente.

O token é criado automaticamente no primeiro login e não deve ser versionado.
