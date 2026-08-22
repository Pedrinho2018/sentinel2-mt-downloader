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
- configuração de patches 256/512, stride, nuvem e dados válidos mínimos;
- geração do dataset junto ao download ou a partir das cenas locais;
- perfis locais de região em SQLite, sem armazenar OAuth ou tokens;
- abertura da pasta de imagens e visualização dos previews RGB.

## Observação

O mapa é carregado somente ao abrir **Área e período**. A biblioteca Leaflet é
buscada no unpkg e, se necessário, no jsDelivr; durante a inicialização a GUI
mostra **Carregando mapa...** e mantém uma mensagem visível se a biblioteca ou
o documento não responder. Para tentar novamente, saia da página e abra-a de
novo. Sem conexão, ou se apenas os tiles do OpenStreetMap falharem, o mapa pode
ficar indisponível ou sem fundo, mas as quatro coordenadas continuam editáveis
manualmente.

O token é criado automaticamente no primeiro login e não deve ser versionado.
Depois do consentimento, o callback local exibe uma página responsiva com o
estado real da autenticação e orientação para retornar à sincronização.

### Erro 403 `access_denied` no Google

O JSON OAuth identifica um projeto do Google Cloud. Quando esse projeto está com
o status **Testing**, o Google permite o login apenas das contas cadastradas em
**Google Auth Platform → Audience → Test users**. O proprietário do projeto deve
adicionar ali o mesmo e-mail escolhido no navegador. Não é necessário gerar
outro JSON depois dessa liberação.

O login sempre exibe o seletor de contas. Escolha exatamente o e-mail incluído
em **Test users**; estar conectado a outra conta Google no navegador não concede
acesso ao projeto.

Se o navegador informar que `localhost` recusou a conexão, não recarregue uma
aba antiga: o endereço usa uma porta temporária que só existe enquanto a
sincronização está aguardando o login. Volte à GUI, cancele a operação se ainda
estiver ativa e execute a sincronização novamente. O callback atual usa
explicitamente `127.0.0.1` para evitar incompatibilidade entre IPv4 e IPv6.

Essa restrição é aplicada pelo Google antes de o programa receber o token e não
pode ser removida pelo código local. Para distribuição pública, o proprietário
deve publicar/verificar o aplicativo. Consulte a
[documentação oficial sobre audiência e usuários de teste](https://support.google.com/cloud/answer/15549945).

O projeto solicita apenas `https://www.googleapis.com/auth/drive.file`. Se a URL
de autorização ainda mostrar `https://www.googleapis.com/auth/drive`, feche a
instância antiga da GUI e abra novamente com `python iniciar_gui.py`.

## Dataset na GUI

Em **Dados e qualidade**, a GUI expõe as bandas, o limite global da cena, a
geração do dataset, tamanho/stride dos patches, limite de nuvem por patch,
percentual mínimo de dados válidos e faixa fixa do RGB PNG. A GUI expõe essa
faixa fixa; `dataset.rgb.metodo`, percentis, caminhos e toggles avançados
permanecem configuráveis pela edição externa do YAML.

Na visão geral, **Gerar dataset das cenas locais** executa o pipeline sem novo
download. A operação **Baixar imagens aprovadas** também gera patches quando
**Gerar dataset após o download** estiver marcado.

O visualizador da GUI pode abrir JPEG ou PNG, mas isso não altera a hierarquia
de dados: os GeoTIFFs originais são a fonte científica, os GeoTIFFs multibanda
são os recortes para ML e o JPEG é somente preview.

A sincronização Google Drive da GUI percorre `download.pasta`. Artefatos em
`dataset.pasta`, PNG, JSON e `patches.csv` não são sincronizados por padrão.

## Legibilidade e validação

A interface usa a paleta Qt Fusion para não herdar combinações ilegíveis do
tema claro ou escuro do sistema. Campos, menus, calendários, logs, botões e
textos auxiliares usam pares de cores com contraste WCAG AA de pelo menos
`4.5:1`.

Para executar os testes de contraste, navegação, perfis, YAML, subprocessos e
sincronização simulada:

```bash
.venv/bin/python -m unittest discover -v
```
