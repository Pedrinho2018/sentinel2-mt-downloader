# AGENTS.md

## Visão do projeto

Este repositório é uma aplicação Python 3.10+ para consultar o STAC do
INPE/Brazil Data Cube, baixar cenas Sentinel-2, gerar previews e datasets
geoespaciais, operar por CLI/TUI/GUI e sincronizar artefatos com o Google Drive.

O fluxo principal é `CLI/TUI/GUI -> ConfiguracaoProjeto -> ServicoSentinel2`.
Adaptadores de HTTP, catálogo CSV, processamento raster, OAuth e Google Drive
devem continuar substituíveis nos testes.

## Mapa do repositório

- `src/sentinel2_mt/`: domínio, serviços e adaptadores reutilizáveis.
- `src/baixar_inpe_mt.py`: entrada compatível da CLI.
- `src/tui.py`: interface Textual.
- `src/gerar_config_gui.py`: interface PySide6/Qt WebEngine.
- `config/config.yaml`: configuração de desenvolvimento.
- `packaging/config.yaml`: configuração instalada pelos pacotes Linux.
- `tests/`: testes `unittest`, incluindo rasters sintéticos e fakes de APIs.
- `docs/dataset.md`: contrato científico do pipeline de patches.
- `packaging/` e `.github/workflows/packages.yml`: build e distribuição Linux.

## Regras de implementação

- Preserve textos e nomes públicos em português, salvo exigência de uma API.
- Mantenha regras de negócio em `src/sentinel2_mt/`; interfaces apenas coletam
  dados, chamam serviços e apresentam resultados.
- Preserve injeção de dependências em clientes externos para que testes não
  dependam de rede, navegador ou Google Drive real.
- Trate caminhos com `pathlib.Path` e mantenha compatibilidade entre execução
  pelo repositório, binário PyInstaller e pacotes Linux.
- Mudanças no esquema YAML devem manter compatibilidade com configurações
  anteriores quando viável e atualizar `config/config.yaml`,
  `packaging/config.yaml`, GUI/TUI/CLI, testes e documentação aplicável.
- Em rasters, preserve CRS, transform, bounds, resolução, dtype, nodata e
  máscara. Use `nearest` para classes categóricas e `bilinear` apenas para
  bandas contínuas. Reamostragem não pode ser descrita como ganho de resolução.
- Não sobrescreva GeoTIFFs científicos com previews RGB ou representações
  `uint8`. Mantenha IDs e catálogos determinísticos e reproduzíveis.
- Em builds locais Arch, não presuma a extensão `.pkg.tar.zst`: o `makepkg`
  pode usar `PKGEXT` configurado no sistema e gerar `.pkg.tar`. Ambos são
  formatos válidos para `pacman -U`; valide o arquivo realmente produzido
  antes de instalar ou copiar o artefato.
- Não altere arquivos grandes/gerados em `data/`, catálogos reais ou artefatos
  de `build/`, `dist/` e `release/`, salvo solicitação explícita.
- Preserve alterações locais preexistentes e evite mudanças fora do escopo.

## Segurança e integrações

- Nunca leia, exiba ou versione credenciais reais de `.env`, JSON OAuth ou token.
- Mantenha `config/google-oauth.json`, `config/google-token.json`,
  `config/client_secret_*.json` e bancos locais fora do Git.
- O Google Drive deve continuar usando o escopo mínimo `drive.file`; qualquer
  ampliação de escopo exige justificativa e revisão do agente `security`.
- Tokens persistidos devem manter permissão restritiva e mensagens/logs não
  podem revelar token, secret, código OAuth ou URL de autorização completa.
- Testes não devem chamar STAC, baixar cenas, abrir navegador nem escrever no
  Drive real. Use fakes, mocks, `TemporaryDirectory` e rasters sintéticos.
- Revise especialmente URLs externas, callback em `127.0.0.1`, escrita de
  caminhos configuráveis, downloads parciais e scripts de empacotamento/CI.

## Validação

Use o ambiente virtual existente quando disponível:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -v
```

Para iterações, execute primeiro o módulo diretamente relacionado, por exemplo:

```bash
.venv/bin/python -m unittest tests.test_configuracao -v
.venv/bin/python -m unittest tests.test_servico -v
.venv/bin/python -m unittest tests.test_patches -v
.venv/bin/python -m unittest tests.test_google_drive -v
.venv/bin/python -m unittest tests.test_gui_qt -v
```

- Alterações em configuração ou empacotamento também exigem
  `tests.test_packaging`.
- Alterações na GUI exigem testes Qt em modo headless; execute smoke tests
  interativos apenas quando a tarefa pedir validação visual.
- Não execute download real, OAuth real, upload real ou build de pacotes apenas
  para validar uma mudança unitária.
- Se uma dependência opcional impedir um teste, relate o teste pulado e o motivo;
  não declare a suíte integralmente aprovada.
- Relate warnings novos; `ResourceWarning`, processos pendentes e recursos não
  fechados não devem ser ocultados como execução limpa.
- O projeto ainda não configura lint, typecheck ou coverage. Não declare esses
  gates executados e não os torne obrigatórios sem adicionar ferramenta,
  configuração, dependência e CI correspondentes.

## Multi-agent workflow

Use subagentes em tarefas complexas e divida somente trabalhos independentes.
Agentes de análise podem trabalhar em paralelo; evite escritas paralelas nos
mesmos arquivos. O agente principal deve delegar, aguardar, consolidar as
evidências, resolver conflitos e apresentar a decisão final.

### `architect`

Use antes de mudanças estruturais, novos módulos, novas integrações, alterações
transversais de configuração ou refatorações grandes. Ele deve mapear o fluxo
real, propor limites e contratos e identificar migrações, testes, documentação
e riscos; a implementação permanece com o agente principal/worker.

### `security`

Use quando houver mudanças em OAuth, Google Drive, APIs/HTTP/STAC, secrets,
persistência SQLite/CSV/JSON, caminhos graváveis, subprocessos, Docker,
GitHub Actions, dependências, pacotes, permissões ou infraestrutura. Use-o no
desenho e novamente no diff final quando a mudança for sensível. Ele faz revisão
somente leitura e retorna achados priorizados com evidência e mitigação.

### `geospatial`

Use quando houver mudanças em STAC/assets, bandas ou índices, filtros SCL,
previews, `imagens.py`, `patches.py`, catálogos/metadata do dataset, CRS,
transform, bounds, resolução, dtype, nodata, máscaras ou resampling. Ele revisa
somente leitura a integridade científica e propõe testes com rasters sintéticos.

### `tester`

Use após features, correções de bugs e refatorações. Ele seleciona a menor
suíte relevante, adiciona casos de borda ao plano de validação e, ao final,
executa a suíte completa quando proporcional ao risco. Falhas preexistentes e
regressões da alteração devem ser distinguidas. Skips inesperados e warnings
novos devem ser relatados; teste Qt pulado não aprova mudança de GUI.

### `reviewer`

Antes de considerar qualquer implementação concluída, delegue uma revisão
somente leitura do diff. Priorize bugs, regressões, integridade geoespacial,
segurança, portabilidade, ausência de testes e divergência entre interfaces.
O agente principal deve resolver ou justificar cada achado material.

### `docs`

Quando comportamento público mudar, delegue a atualização de `README.md`,
`README_GUI.md`, `docs/`, `packaging/README.md` e exemplos aplicáveis. São
públicos: flags CLI, esquema/defaults YAML, variáveis de ambiente, formatos de
saída, layout de arquivos, OAuth/sincronização, interfaces e instalação.

## Regras de revisão de código

- Trate como bloqueador qualquer perda silenciosa de georreferenciamento,
  alteração indevida de dtype/nodata/máscara ou uso de resampling categórico
  incorreto.
- Sinalize divergência entre CLI, TUI, GUI, `config/config.yaml` e
  `packaging/config.yaml` para o mesmo recurso.
- Sinalize testes que alcancem serviços externos ou que dependam de credenciais,
  rede, estado do usuário, ordem global ou arquivos fora de diretórios temporários.
- Sinalize qualquer ampliação do escopo OAuth, vazamento de secrets/logs ou
  permissão mais ampla de token sem necessidade comprovada.
- Comentários puramente estilísticos só são relevantes quando ocultam risco
  de correção, manutenção ou comportamento.
