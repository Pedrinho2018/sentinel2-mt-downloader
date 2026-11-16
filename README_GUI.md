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

- página **Início** com o resumo local (regiões, imagens encontradas e
  baixadas, recortes prontos, última análise, modelos, espaço em disco e tarefa
  atual), avisos e o próximo passo recomendado;
- navegação por início, operações, área, qualidade, Google Drive, análise,
  histórico e configuração;
- mapa OpenStreetMap com seleção da bounding box pelo botão **Desenhar área**
  ou usando `Shift + arrastar`;
- pesquisa de municípios (sem acento) e de regiões favoritas, importação de
  limites GeoJSON e Shapefile;
- execução integrada de catalogação, download, dataset, análise e sincronização;
- log em tempo real, estado da operação e cancelamento seguro;
- escolha do JSON OAuth e sincronização em lotes;
- geração e revisão visual do `config.yaml`;
- configuração de patches 256/512, stride, nuvem e dados válidos mínimos;
- geração do dataset junto ao download ou a partir das cenas locais;
- perfis locais de região em SQLite, sem armazenar OAuth ou tokens;
- abertura da pasta de imagens e visualização dos previews RGB.

## Área e período

- **Pesquisar município ou região favorita**: digite parte do nome, com ou sem
  acento. Municípios vêm de `assets/municipios_ibge.json`, lista local gerada a
  partir da API do IBGE com `tools/gerar_municipios_ibge.py --uf MT`; a busca não
  usa a internet. Sem esse arquivo, a GUI mostra como gerá-lo. O retângulo de
  cada município é aproximado (malha de qualidade intermediária) e pode ser
  ajustado. A lista atual (gerada em 16/11/2026) tem 141 municípios de MT;
  Boa Esperança do Norte (5101837) ainda não tem geometria na malha do IBGE e
  fica registrado em `municipios_sem_malha`, fora da busca, até a malha ser
  publicada. Para essa área, desenhe no mapa ou importe um limite.
- **Desenhar área**: ative o botão e clique e arraste no mapa; a área é aplicada
  ao soltar o mouse. `Shift + arrastar` continua funcionando.
- **Importar limite…**: aceita GeoJSON (`.geojson`, `.json`) e Shapefile (`.shp`
  com o `.prj` na mesma pasta). Sistemas projetados, como SIRGAS 2000 / UTM, são
  convertidos para EPSG:4326 com amostragem das bordas, de modo que o retângulo
  final envolva a área original. Shapefile sem `.prj`, geometria sem área ou
  coordenadas fora do intervalo geográfico são recusados com mensagem.
- **Retângulo, não contorno**: a consulta ao STAC e o pipeline usam bounding
  box. Um polígono importado é reduzido ao seu retângulo envolvente, e a tela
  informa isso.
- **Regiões favoritas**: **Salvar como região favorita** grava área, período e
  parâmetros no SQLite local (sem credenciais). A lista ao lado abre a região
  com clique duplo; as favoritas também aparecem na pesquisa.

## Baixar imagens (assistente)

Fluxo guiado em três passos, sem exigir conhecimento de bandas ou YAML:

1. **Região e período**: confira a região (altere no mapa se precisar) e escolha
   as datas ou um período rápido (últimos 30/90 dias, safra de verão).
2. **Escolher cenas**: **Buscar imagens disponíveis** consulta o catálogo do
   INPE sem baixar nada e lista data, cena, nuvens e tamanho. Filtre por nuvens
   e oculte as cenas já baixadas; marque as que deseja. A coluna de nuvens vem
   dos metadados do catálogo quando informada; cenas sem essa informação não
   são escondidas pelo filtro. A avaliação pela camada SCL continua acontecendo
   no download e pode descartar a cena. Ao clicar em uma linha, a GUI mostra a
   prévia local, se existir, ou a miniatura do catálogo (somente do mesmo host
   do STAC). **Opções avançadas** leva a bandas, filtros e dataset.
3. **Baixar**: revise a quantidade, as datas, as bandas e a estimativa de
   espaço. A estimativa usa o tamanho informado pelo catálogo ou, na falta dele,
   a média das cenas já baixadas, e diz qual foi a fonte. Durante o download há
   progresso geral e por arquivo, velocidade e mensagens simples.

**Cancelar e retomar.** Cada arquivo é recebido em `.NOME.part`, ao lado do
destino, com um pequeno `.NOME.part.json` de controle. Ao cancelar, ou se a
conexão cair, o parcial é mantido; o próximo download do mesmo arquivo pede ao
servidor apenas o restante (`Range` com `If-Range`). Se o servidor não aceitar
retomada ou o arquivo remoto tiver mudado, o download recomeça do zero, sem
misturar bytes. O arquivo final só aparece depois da conferência de tamanho e
da leitura do GeoTIFF. Temporários antigos com nome aleatório, de versões
anteriores, não podem ser retomados; o passo 3 oferece removê-los, com
confirmação e sem tocar em imagens completas.

O assistente executa a mesma CLI: a busca usa a catalogação com
`--progresso-json`, e o download usa `--baixar --cenas ID1,ID2 --max-itens 0
--progresso-json`. As linhas `[PROGRESSO] {json}` alimentam a tela e não
aparecem no registro de Operações.

## Página Início

É a primeira tela. Os botões **Selecionar região**, **Buscar imagens**,
**Analisar região** e **Consultar resultados** apenas levam à tela da etapa e
pré-selecionam a operação; nada é baixado ou analisado sem clicar em
**Executar operação**.

O resumo é calculado em segundo plano, somente lendo arquivos locais: catálogo
de cenas, pastas de imagens e recortes, catálogo de patches, histórico de
análises e pesos `.pt` em `analise/models`. Ele avisa quando há downloads
interrompidos (`.part`) ou recortes no disco sem `catalogo/patches.csv`, caso em
que a análise não encontra entradas. "Integridade SHA-256 conferida" indica só
que o peso coincide com o manifesto aprovado, não que o modelo foi validado
cientificamente. Use **Atualizar resumo** após alterações externas.

## Análise agrícola na GUI

Em **Dados e qualidade**, configure o modelo, confiança mínima, IoU máximo,
tamanho da inferência, pasta de análises, histórico, geração do PDF e limite de
imagens. Em **Operações**, selecione **Analisar região com IA**.

A GUI salva o YAML e inicia a CLI em um `QProcess`. A CLI baixa e processa as
cenas com `ServicoSentinel2`, gera patches científicos multibanda e suas
representações RGB, aplica `DetectorAgricola` somente aos PNGs RGB aprovados,
calcula estatísticas, grava overlays e `resultado.json`, gera o PDF opcional e
atualiza o histórico SQLite. A linha `[ANALISE_RESULTADO]` emitida pela CLI
permite que a GUI abra o JSON e mostre contagens por classe, confianças, imagem
original, overlay e dispositivo usado.

O peso esperado na execução pelo código-fonte é
`src/sentinel2_mt/analise/models/best.pt`. O arquivo acompanha esta árvore e é
incluído nos pacotes do aplicativo. Sem ele, a operação informa que o modelo é
inexistente e não executa inferência. Ultralytics e Torch são necessários para
o detector; Matplotlib é necessário quando o relatório PDF está habilitado.
Consulte [docs/modelo-ia.md](docs/modelo-ia.md) antes de substituir o peso.

### Tela de resultados

A página **Análise da região** separa os resultados em duas abas:

- **Talhões (detecção)**: lista dos recortes analisados (data de aquisição,
  número de detecções), imagem RGB original e resultado da IA sobreposto lado a
  lado, tabela de detecções do recorte (classe, confiança e caixa em pixels),
  resumo por classe e, quando NDVI/EVI foram baixados, a média, o mínimo e o
  máximo desses índices **no recorte inteiro** (não por talhão). Os índices só
  aparecem para coleções com escala documentada; para `S2-16D-2`, o STAC do INPE
  informa escala 0,0001 e nodata -9999, e a leitura respeita a máscara do
  GeoTIFF multibanda.
- **Culturas (classificação)**: mostra **Cultura não determinada** enquanto não
  houver um modelo validado e aprovado para identificar culturas.

O `best.pt` atual é um modelo de **detecção por caixas** com 22 classes, várias
com nomes de culturas e coberturas (`soja`, `milho`, `algodao`, `pastagem`,
`nuvem`…). Como não há validação de campo aprovada, a tela chama essas classes
de **classe do modelo**, mostra um alerta quando elas citam culturas e não as
apresenta como cultura identificada.

O cabeçalho informa região, período, cenas, modelo e versão (com o início do
SHA-256), dispositivo e data da análise. **Exportar CSV** grava uma linha por
detecção, com caixas em pixels; **Exportar JSON** grava o resultado com
observações sobre área e cultura. Nenhuma exportação contém área.

Em **Histórico**, filtre por região, cena, modelo ou ID e por período (30 dias,
12 meses); **Abrir resultado** carrega a análise selecionada na tela de
resultados. O clique duplo continua abrindo o PDF.

O histórico guarda metadados e caminhos, não os GeoTIFFs. As caixas e contagens
da tela não representam área agrícola: uma bbox seleciona a região de consulta,
mas, sem escala e geometria georreferenciada validadas para as detecções, não é
correto converter caixas ou pixels em hectares.

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

Em **Dados e qualidade**, a GUI expõe caixas de seleção para as bandas e índices
B02, B03, B04, B05, B06, B07, B08, B8A, B11, B12, NDVI e EVI, com uma descrição
de cada faixa. As coleções conhecidas também podem ser selecionadas por caixa;
um ID de coleção personalizada continua disponível no campo de texto. A GUI
expõe ainda o limite global da cena, a
geração do dataset, tamanho/stride dos patches, limite de nuvem por patch,
percentual mínimo de dados válidos e faixa fixa do RGB PNG. A GUI expõe essa
faixa fixa; `dataset.rgb.metodo`, percentis, caminhos e toggles avançados
permanecem configuráveis pela edição externa do YAML.

Em **Operações**, **Gerar dataset das cenas locais** executa o pipeline sem novo
download. A operação **Baixar imagens aprovadas** também gera patches quando
**Gerar dataset após o download** estiver marcado.

O visualizador da GUI pode abrir JPEG ou PNG, mas isso não altera a hierarquia
de dados: os GeoTIFFs originais são a fonte científica, os GeoTIFFs multibanda
são os recortes científicos, o PNG identificado por UUID é a representação 8-bit usada pelo
detector e o JPEG é somente preview. O overlay também é um PNG de visualização;
ele não sobrescreve o patch RGB nem o GeoTIFF multibanda.

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
