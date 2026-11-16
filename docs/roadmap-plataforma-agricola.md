# Evolução para plataforma agrícola: diagnóstico e plano incremental

Documento de trabalho da branch `feat/painel-inicial-diagnostico`. Ele registra
a auditoria do código em 16/11/2026, a proposta para os modos **Usuário** e
**Laboratório de IA** e o plano de entregas pequenas. Somente a entrega E1
(painel inicial) foi implementada até agora.

## 1. Diagnóstico do código atual

### Evidências da auditoria

| Verificação | Resultado |
| --- | --- |
| Suíte `unittest` antes da mudança (`QT_QPA_PLATFORM=offscreen`) | 148 testes, OK, 0 pulados |
| Suíte depois da E1 | 161 testes, OK, 0 pulados |
| Avisos na suíte | mensagens do Qt WebEngine/Vulkan no modo offscreen; já existiam antes da mudança |
| Ambiente | Python `.venv`, PySide6 6.12, Ultralytics 8.4, Torch 2.15 com CUDA disponível, psutil instalado |
| LabelImg | não instalado na `.venv` |
| Lint, typecheck, coverage | não configurados no projeto; não executados |

Nenhum download, treinamento, OAuth ou sincronização real foi executado.

### Funcionalidades prontas e reutilizáveis

| Componente | Onde | Reuso previsto |
| --- | --- | --- |
| `ConfiguracaoProjeto` | `src/sentinel2_mt/configuracao.py` | fonte única de caminhos e parâmetros para painel, assistentes e Laboratório |
| `ServicoSentinel2` | `servico.py` | busca STAC, filtro SCL, download e geração de dataset nos assistentes |
| `ClienteDownloadHTTP` | `http.py` | download atômico com `.part`, verificação de tamanho e leitura de pontos do raster |
| `GeradorDataset` + `RepositorioCatalogoPatchesCSV` | `patches.py`, `catalogo.py` | fonte de patches para o gerenciador de datasets; o catálogo já tem `label`, `label_source`, `label_confidence` |
| `ServicoAnaliseAgricola`, `DetectorAgricola` | `analise/` | inferência com snapshot e SHA-256 do peso, overlays, JSON, PDF |
| `RepositorioHistoricoAnalises` | `analise/historico.py` | histórico de análises e padrão SQLite com caminhos relativos |
| `LocalConfigStore` | `gui_support.py` | perfis de região (base para "regiões favoritas") |
| `MapaWidget` (Leaflet) | `gerar_config_gui.py` | mapa e seleção de bbox |
| `QProcess` + marcador `[ANALISE_RESULTADO]` | `gerar_config_gui.py` | padrão para executar treino fora da GUI e receber resultados |
| `tools/preparar_dataset_culturas.py` | `tools/` | validação de grupo por split (anti‑vazamento) já implementada para classificação |
| `tools/classificar_culturas.py` | `tools/` | rejeita peso de detecção e classes incompatíveis |

### Limitações e riscos encontrados

1. **Catálogo de patches ausente no ambiente local.** Existem 281 patches em
   `data/dataset`, mas `catalogo/patches.csv` não existe. A análise depende
   desse catálogo e falharia com "Catálogo de patches não encontrado". O painel
   novo passa a avisar isso.
2. **Download interrompido deixa `.part` órfão.** Há
   `.B03.tif.*.part.tif` em uma cena. O cliente remove o `.part` em exceções,
   mas não quando o processo é morto (`kill`). Não há retomada por `Range`.
3. **Progresso real indisponível na GUI.** O `tqdm` escreve na saída, mas a GUI
   usa barra indeterminada; não há velocidade, tamanho estimado nem ETA.
4. **GUI monolítica.** `src/gerar_config_gui.py` tem ~1.950 linhas e uma só
   classe; páginas eram navegadas por índice fixo (corrigido na E1 para
   navegação por título).
5. **Sem pesquisa de município, desenho de polígono ou importação GeoJSON/
   Shapefile.** A área é apenas bbox (`Shift + arrastar`).
6. **Leaflet carregado de CDN** (unpkg/jsDelivr); sem rede, não há mapa.
7. **Manifesto de modelo único.** `model_metadata.json` descreve apenas o peso
   ativo; não há registro de versões, métricas, dataset ou rollback.
8. **Treinamento só por scripts** (`tools/`), sem integração à GUI, sem split
   espacial automático e sem painel de métricas.
9. **Mistura semântica possível.** O peso atual é de *detecção* (`task:
   detection`); as classes de cultura existem apenas no piloto de
   classificação. A interface precisa manter essa separação (regra já descrita
   em `docs/transicao-talhoes-culturas.md`).
10. **Varredura de disco síncrona** seria cara em pastas grandes (7,2 GB
    locais); na E1 o painel calcula em thread separada.

### Sobre o LabelImg

O projeto LabelImg foi arquivado pelos mantenedores e incorporado ao Label
Studio; a última versão no PyPI é antiga e há relatos de falhas com PyQt5 em
Python 3.10+. Ele ainda grava YOLO `.txt` corretamente quando funciona, mas não
deve ser dependência obrigatória. Proposta:

- integração **por contrato de arquivos**, não por código: a aplicação prepara
  `images/`, `labels/` e `classes.txt` e abre um anotador externo por
  subprocesso; ao fechar, importa e valida os `.txt`;
- anotadores suportados: LabelImg (se instalado) e **X-AnyLabeling** (mantido,
  exporta YOLO). A validação dos rótulos é feita pela aplicação, independente
  da ferramenta;
- classificação de culturas não usa caixas: tela própria de atribuição de
  classe por patch, gravando no catálogo do dataset de classificação.

Compatibilidade de versão do anotador deve ser confirmada na máquina antes da
E5; isso não foi testado nesta entrega.

## 2. Jornadas propostas

### Modo Usuário

`Início → Selecionar região → Buscar imagens → Baixar → Analisar → Resultados`

- **Início** (E1, pronto): indicadores locais, tarefa atual, avisos e próximo
  passo; quatro ações com nomes simples que apenas navegam.
- **Região** (E2, pronto): pesquisa de município, desenho de retângulo/polígono,
  importação GeoJSON/Shapefile e favoritos sobre `LocalConfigStore`.
- **Assistente de download** (E3, pronto): período → busca → tabela de cenas com data,
  nuvem e preview → seleção → download com progresso por arquivo, velocidade,
  cancelamento e retomada quando o servidor aceitar `Range`. Bandas e YAML
  ficam em "Opções avançadas".
- **Resultados** (E4, pronto): imagem original e overlay, classes e confiança, modelo e
  versão, data da cena, NDVI/EVI quando baixados, exportação. Detecção de
  talhões e classificação de culturas em abas separadas; culturas só aparecem
  com modelo aprovado, caso contrário "cultura não determinada". Sem hectares.

### Modo Treinamento (Laboratório de IA)

`Projeto → Dataset → Rotulagem → Validação → Treino → Avaliação → Aprovação → Uso`

Cada etapa mostra concluídas, pendentes, contagens, erros e próxima ação. Nada
é rotulado automaticamente e toda operação crítica pede confirmação.

## 3. Arquitetura do Laboratório de IA

Novo pacote `src/sentinel2_mt/laboratorio/`, sem dependência de Qt:

```text
laboratorio/
├── projetos.py      # ProjetoIA: tipo (deteccao|classificacao), esquema de classes versionado
├── datasets.py      # importa patches do catálogo, hash SHA-256, integridade, filtros
├── anotacoes_yolo.py# valida .txt YOLO (ids, 5 colunas, 0..1, caixas não degeneradas)
├── rotulos_classe.py# rótulo por imagem para classificação
├── divisao.py       # split por grupo espacial (cena/tile/bloco) e por safra
├── treino.py        # monta argumentos Ultralytics; executado por subprocesso
├── avaliacao.py     # lê métricas Ultralytics; P/R/F1 por classe, por região e safra
└── registro_modelos.py # registro SQLite: versão, tarefa, classes, SHA-256, dataset, config, métricas, estado
```

Princípios:

- **Dados científicos somente leitura.** Datasets do Laboratório referenciam
  os PNG/GeoTIFF por caminho e hash; exportações para o formato Ultralytics são
  cópias ou links em `data/laboratorio/<projeto>/<versao>/`.
- **Detecção e classificação não se misturam.** O tipo do projeto define o
  formato de rótulo; o validador rejeita o outro.
- **Split espacial.** Grupo padrão = `scene_id` + bloco de N×N patches
  vizinhos (derivado de `x`, `y` do `patch_id`); teste independente por região
  ou safra. A regra de `tools/preparar_dataset_culturas.py` (grupo em um único
  split) é reaproveitada.
- **Treino fora da GUI.** CLI nova (`--treinar <config>`), lançada por
  `QProcess`, emitindo linhas `[TREINO_PROGRESSO] {json}` por época. Cancelar
  envia término ao processo; retomada só com `last.pt` existente e
  `resume=True` do Ultralytics.
- **Aprovação explícita.** Estados `treinado → avaliado → aprovado → ativo`.
  Ativar grava novo manifesto; o `best.pt` atual nunca é sobrescrito e
  permanece como versão de rollback.

## 4. Plano incremental e critérios de aceite

| # | Entrega | Prioridade | Critérios objetivos de aceite |
| --- | --- | --- | --- |
| E1 | **Painel inicial** (feito) | P0 | página "Início" é a primeira; mostra regiões, cenas catalogadas/baixadas, recortes, última análise, modelos com integridade SHA-256, espaço em disco, tarefa atual; avisa `.part` órfão e catálogo ausente; cálculo fora da thread da GUI; não altera arquivos (teste); suíte completa verde |
| E2 | Região simplificada (feito) | P0 | busca de município offline por lista IBGE embutida; desenho de retângulo; importação GeoJSON (Shapefile opcional com dependência opcional); bbox derivada preserva CRS EPSG:4326; favoritos reutilizam `LocalConfigStore`; testes sem rede |
| E3 | Assistente de download (feito) | P0 | passos 1–7 navegáveis; tabela de cenas a partir de catalogação existente; progresso estruturado `[PROGRESSO] {json}` da CLI (bytes, total, velocidade); cancelar remove `.part`; retomada `Range` testada com servidor fake; limpeza de `.part` órfãos com confirmação |
| E4 | Resultados agrícolas (feito) | P0 | abas "Talhões (detecção)" e "Culturas (classificação)"; culturas exibem "não determinada" sem modelo aprovado; histórico filtrável; exportação CSV/JSON; nenhum texto menciona hectares |
| E5 | Datasets do Laboratório (feito) | P1 | criar projeto com tipo e esquema de classes versionado; importar patches do catálogo sem copiar GeoTIFF; SHA-256 e detecção de duplicatas; verificação de integridade; filtros por região, data, nuvem e estado de rotulagem |
| E6 | Rotulagem | P1 | exportar lote para anotador externo; detectar LabelImg/X-AnyLabeling instalados; importar `.txt`; validador YOLO com mensagens por linha; contagem rotulados/pendentes; tela de atribuição de classe para classificação |
| E7 | Preparação do treino | P2 | split por grupo espacial sem vazamento (teste: nenhum grupo em dois splits); relatório de desbalanceamento; revisão antes de iniciar; `data.yaml` gerado em pasta versionada |
| E8 | Execução e painel de treino | P2 | treino em processo separado; épocas, perdas, métricas, CPU/GPU/RAM (psutil/torch) e logs na GUI; cancelamento seguro; retomada só com checkpoint; testado com treinador fake |
| E9 | Avaliação e registro de modelos | P3 | métricas de detecção (P, R, mAP50, mAP50‑95, matriz) e classificação (accuracy, P/R/F1 por classe, por região/safra); exemplos FP/FN; registro com SHA-256, dataset e config; aprovação manual; ativação e rollback sem tocar no `best.pt` original |
| E10 | Fluxo assistido ponta a ponta | P4 | trilha visual das etapas com contagens, erros e próxima ação; operações críticas exigem confirmação |
| E11 | Desempenho e robustez | P4 | leitura por janelas medida em raster sintético grande; limite de RAM documentado; cache de resumos; logs rotativos |

Cada entrega deve: ficar em branch própria, manter CLI/TUI/GUI compatíveis,
adicionar testes sem rede, atualizar a documentação e passar na suíte completa.

## 5. Entrega E1 — o que mudou

- `src/sentinel2_mt/painel.py`: `coletar_resumo_painel()` (somente leitura,
  sem rede e sem Torch) e `listar_modelos_instalados()`.
- `src/gerar_config_gui.py`: página **Início** com quatro ações simples, oito
  indicadores, avisos e próximo passo; antiga "Visão geral" renomeada para
  **Operações**; navegação por título em vez de índice fixo; cálculo do resumo
  em `QThreadPool` e atualização ao concluir operações.
- `tests/test_painel.py` e novos casos em `tests/test_gui_qt.py`.

"Integridade conferida" significa apenas que o SHA-256 do peso coincide com o
manifesto aprovado. Não é evidência de acurácia nem de validação científica.

## 6. Entrega E2 — o que mudou

- `src/sentinel2_mt/area.py`: importação de GeoJSON (incluindo `crs` legado e
  reprojeção por `rasterio.warp.transform_bounds`) e de Shapefile pelo
  cabeçalho do `.shp` + `.prj`, sem novas dependências; busca de municípios sem
  acentos.
- `tools/gerar_municipios_ibge.py`: gera `assets/municipios_ibge.json` a partir
  das APIs de localidades e malhas do IBGE (executado manualmente, com rede).
- GUI: pesquisa unificada de municípios e favoritas, botão **Desenhar área**
  (sem Shift), **Importar limite…**, lista de **Regiões favoritas** e menu
  lateral sincronizado com a navegação feita por botões.
- Empacotamento Linux inclui `assets/municipios_ibge.json` quando existir.

Limitações conhecidas: a área usada continua sendo um retângulo; o contorno do
polígono não é desenhado no mapa nem usado para recortar resultados. Shapefile
compactado (`.zip`) e KML não são aceitos.

## 7. Entrega E3 — o que mudou

- `http.py`: parcial com nome fixo (`.NOME.part` + `.part.json`), retomada por
  `Range`/`If-Range`, descarte em HTTP 4xx/5xx, faixa divergente, conteúdo
  inválido ou servidor sem suporte; callback de progresso; listagem e remoção
  de parciais legados.
- `servico.py`/`cli.py`: `--cenas` (seleção por ID) e `--progresso-json`
  (eventos `cena_encontrada`, `cena`, `arquivo`); downloader injetável.
- `assistente_download.py`: modelo de cena, filtros, estimativa de espaço com
  origem declarada e mensagens de progresso em português simples.
- GUI: página **Baixar imagens** com três passos, prévia/miniatura, progresso,
  cancelamento e limpeza de temporários antigos com confirmação; Início aponta
  para o assistente; painel distingue parciais retomáveis de legados.

Critério do roadmap ajustado: cancelar **mantém** o parcial para retomada
(em vez de removê-lo), porque ele nunca é confundido com um arquivo final.
Validação real em 16/11/2026 (cena `S2-16D_V2_020018_20260322`, bandas B02,
B03, B04 e SCL, área de teste perto de Sorriso, pasta `data/validacao-e3/`):

| Etapa | Resultado |
| --- | --- |
| Busca com `--progresso-json` | 12 cenas; `eo:cloud_cover` presente; miniaturas em `data.inpe.br`, mesmo host do STAC; `file:size` ausente |
| 1ª execução, SIGTERM em B02 (como o Cancelar da GUI) | parado em 56,6 de 177,6 MB; `.B02.tif.part` e `.part.json` mantidos; nenhum `B02.tif` final |
| 2ª execução | `B02: retomado` a partir de 56,6 MB; B03/B04 baixados; código de saída 0 |
| Integridade | tamanhos idênticos ao `Content-Range`; 10560×10560, `int16`, EPSG:10857, 10 m, nodata 0; estatísticas do B02 retomado iguais às de um download sem interrupção; nenhum temporário restante |

A validação expôs duas falhas, corrigidas nesta entrega: o STAC do INPE
responde `502` de forma intermitente e o cliente não repetia a consulta; e o
download por `--cenas` continuava paginando o STAC depois de encontrar todas as
cenas, de modo que um `502` tardio fazia a execução terminar com erro e sem
gravar o catálogo. Agora consultas STAC repetem `429/502/503/504` com espera
crescente (`politica_retentativa_stac`) e a paginação para assim que todas as
cenas escolhidas foram processadas.

A TUI ainda não tem o assistente; ela continua usando as operações anteriores.

## 8. Entrega E4 — o que mudou

- `analise/resultados.py`: leitura de `resultado.json` com resolução de caminhos
  restrita às raízes de dataset e análises; itens por recorte com data, cena,
  nuvens, overlay e detecções; indicadores NDVI/EVI com escala documentada por
  coleção; exportação CSV/JSON atômica; filtro do histórico; ponto único
  `modelo_classificacao_aprovado()` (retorna `None` até a E9).
- GUI: abas **Talhões (detecção)** e **Culturas (classificação)**, navegação por
  recorte, tabela de detecções, exportações e histórico filtrável com
  **Abrir resultado**.

Escala dos índices conferida no STAC do INPE em 16/11/2026 (`eo:bands` de
`S2-16D-2`: NDVI/EVI `int16`, escala 0,0001, nodata -9999). Observação da
auditoria: os `multiband.tif` não gravam escala nem nodata por banda (usam
máscara por dataset); por isso a escala vem de uma tabela explícita e coleções
fora dela não exibem índices. Gravar `scales`/`offsets` no patch fica como
melhoria para revisão do agente `geospatial`, pois altera o contrato do dataset.

Não verificado: a tela não foi exercitada com uma análise real do `best.pt`
(os testes usam um `resultado.json` e um patch sintéticos), porque o catálogo
de patches local está ausente e gerar o dataset e analisar exige uma execução
longa.

## 9. Entrega E5 — o que mudou

- Nova seção YAML `laboratorio.pasta` (default `data/laboratorio`; pacote em
  `~/.local/share/sentinel2-mt/laboratorio`), com fallback para configurações
  antigas e validação de contenção na raiz.
- `laboratorio/projetos.py`: SQLite com projetos de tipo fixo, esquemas de
  classes versionados e imutáveis, imagens com SHA-256, safra, grade, região,
  estado de rotulagem, integridade e duplicatas.
- `laboratorio/datasets.py`: descoberta pelo catálogo ou, na falta dele, pelos
  `metadata.json`; importação sem cópia dos recortes do sistema; importação
  validada e copiada de imagens externas; verificação de integridade; filtros e
  miniaturas em cache.
- GUI: página **Laboratório de IA** com tarefas em segundo plano e progresso.

Validação com os dados locais (somente leitura, banco em pasta temporária):
281 recortes descobertos pela pasta (catálogo ausente) e hash em 0,3 s;
miniaturas em 2,2 s; integridade em 2,5 s, todos íntegros; nenhuma duplicata;
safra 2025/2026.

Não incluído nesta entrega: exportação para o formato de treino (vai para a E7,
junto com a separação espacial), rotulagem (E6). O critério "filtrar por data"
ficou coberto pela busca textual e pela safra, sem intervalo de datas dedicado.

Dívida técnica: `src/gerar_config_gui.py` passou de 3.500 linhas. Antes da E6,
convém extrair as páginas (Início, Baixar imagens, Resultados, Laboratório)
para módulos próprios, mantendo a lógica nos serviços.
