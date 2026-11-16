# Validação da E6 — Rotulagem com LabelImg

Data: 16/11/2026 · Branch: `feat/laboratorio-rotulagem` · Máquina: Linux (GNOME/Wayland,
XWayland), GPU NVIDIA · Responsável pela execução: Claude Code, a pedido do usuário.

Resumo: o fluxo **Laboratório → lote → LabelImg → YOLO → importação → revisão**
funciona com o LabelImg real, desde que ele rode em **Python 3.9** isolado. A
validação encontrou e corrigiu um risco real (LabelImg salva em Pascal VOC por
padrão) e confirmou que os dados científicos e o `best.pt` não foram alterados.
A única anotação criada é técnica, em um laboratório de teste separado.

## 1. Estado real do repositório (inspecionado, não assumido)

| Item | Resultado |
| --- | --- |
| Branch local x remota | `feat/laboratorio-rotulagem` em `8697c00`, igual a `origin` |
| Alterações não versionadas no início | apenas `catalogo/patches.csv` e o `.lock` do catálogo |
| PRs | #18 mesclado na `main`; #19 (E5) aberto contra `main`; #20 (E6) aberto contra a branch da E5 |
| Suíte no início | **272 testes, OK** (os 269 do relatório anterior + 3 regressões das correções para Windows feitas depois) |
| Suíte no fim | **273 testes, OK, 0 pulados, 0 warnings Python** (+1 regressão desta validação) |

Nenhum push, merge ou alteração na `main` foi feito nesta validação.

## 2. Instalação isolada do LabelImg

Tentativas, em ordem:

1. `pipx install labelImg` com o Python do sistema (3.14): instala, mas **não abre**
   (`ModuleNotFoundError: No module named 'distutils'`). O código instalado também
   chama `drawRect`/`drawLine` com floats, que falham no Python 3.10+ (PyQt5).
2. `pipx install --python 3.9 --fetch-missing-python labelImg`: **falhou**; a release
   atual do python-build-standalone não publica mais o Python 3.9 (fim de vida).
3. **`uv tool install --python 3.9.25 labelImg`** (com `uv` instalado via `pipx`):
   **funcionou**.

| Componente | Versão | Ambiente |
| --- | --- | --- |
| uv | 0.13.8 | `pipx` |
| Python | 3.9.25 (cpython, uv) | `~/.local/share/uv/tools/labelimg` |
| LabelImg | 1.8.6 | idem |
| PyQt5 / Qt | 5.15.11 / 5.15.14 | idem |
| lxml | 6.1.3 | idem |

Downloads: o Python 3.9 (86 MB descompactado) e as wheels de PyQt5/lxml. A `.venv`
do aplicativo foi conferida depois: **sem PyQt5**, PySide6 6.12.0 intacto. Nenhum
código do LabelImg foi modificado.

## 3. Execução real do LabelImg

| Verificação | Evidência |
| --- | --- |
| A janela abre no display e permanece aberta | `labelImg` real ficou em execução por 6 s sem erro (XWayland) |
| Botão "Abrir no LabelImg" do app inicia o programa real | processo `.../uv/tools/labelimg/bin/python .../labelImg` vivo após 2 min, com os argumentos `<lote>/images <lote>/classes.txt <lote>/labels` |
| Imagem real carregada | 5 imagens na lista; arquivo aberto 512×512 (`labelimg-aberto.png`) |
| Classes do projeto | `label_hist = ["teste_validacao"]`, vindo do `classes.txt` do lote |
| Formato YOLO disponível | formatos PASCAL_VOC, YOLO e CREATE_ML; inicial **PASCAL_VOC**; após trocar, botão mostra **YOLO** |
| Desenho e gravação | 1 caixa desenhada no canvas real; `paintEvent` executado sem erro no Python 3.9 (`labelimg-anotado.png`) |

Limitação declarada: a caixa foi desenhada por **eventos de mouse programados**
no canvas real do LabelImg (arrastar de 96,128 até 320,288 px), não à mão. O
desenho manual pelo usuário continua sendo o próximo passo.

## 4. Fluxo completo com 5 recortes reais de Mato Grosso

Isolamento: laboratório de teste em `data/laboratorio-teste-e6/` (config própria),
projeto **"TESTE E6 - validação técnica (não usar em treino)"**, tipo detecção,
classe neutra **`teste_validacao`**. Nenhuma classe agrícola foi atribuída. O
projeto real "Talhões MT" (`data/laboratorio/`) não foi tocado.

Recortes (escolhidos de forma determinística entre os 198 que intersectam a bbox
de MT; dados de 23/04/2026, 100 % de pixels válidos):

| Recorte | Cena | Nuvem |
| --- | --- | --- |
| `…_0aec669ef88a_x009728_y007168_512` | 014015 | 2,12 % |
| `…_87173291640f_x008192_y006144_512` | 014015 | 1,49 % |
| `…_122b978a5e7f_x008704_y006144_512` | 014016 | 0,03 % |
| `…_58e9935c5d34_x007168_y002560_512` | 014016 | 9,75 % |
| `…_9d06109de9fe_x009728_y005120_512` | 014016 | 2,99 % |

| Etapa | Resultado |
| --- | --- |
| Criar projeto de detecção | criado; tela mostra "rótulos: anotações YOLO com caixas" e "Esquema de classes v1: teste_validacao" |
| Importar recortes | 281 encontrados no `catalogo/patches.csv`, nenhum arquivo copiado ou alterado |
| Preparar lote (somente os 5 selecionados) | `lote-atual/` com 5 PNG copiados, `classes.txt`, `labels/classes.txt`, `lote.json` |
| Abrir no LabelImg | programa real iniciado pelo app com os argumentos corretos |
| Salvar YOLO | `labels/<recorte>.txt` = `0 0.404297 0.403320 0.437500 0.310547` |
| Importar no Laboratório | "1 imagem rotulada; 0 com erro; 4 ainda sem anotação" |
| Conferência | conteúdo no banco **idêntico** ao `.txt` do LabelImg; classe 0 → `teste_validacao` (esquema v1); coordenadas em [0,1]; caixa = x1 95, y1 127, x2 319, y2 286 px; estado **rotulada**; as outras 4 **pendentes** |
| Reabrir para revisão | lote refeito com "Incluir já rotuladas"; o `.txt` voltou para `labels/`; LabelImg real recarregou **1 caixa `teste_validacao`** em 95,127–319,286 px (`labelimg-revisao.png`) |

## 5. Erros encontrados e correções

| Achado | Tipo | Ação |
| --- | --- | --- |
| LabelImg 1.8.6 não abre no Python 3.14 e falha ao desenhar no 3.10+ | ambiente | instalado em Python 3.9 isolado via `uv`; README atualizado com a receita validada |
| Um `.xml` (Pascal VOC, sem caixas) apareceu em `labels/` às 18:58:45, enquanto a janela aberta pelo app estava na tela; o LabelImg abre em PascalVOC e o app ignorava esse arquivo em silêncio | **risco real de uso** | importação agora conta imagens só com `.xml` e avisa: "salvas só em Pascal VOC… clique no botão de formato até mostrar YOLO e salve de novo"; teste `test_avisa_quando_o_labelimg_salvou_so_em_pascal_voc` |
| Salvar sem caixas grava `.txt` vazio, importado como "rotulada sem talhões" | comportamento do formato YOLO | mantido (é a convenção YOLO para negativos); documentado. O `.txt` vazio gerado por uma tentativa de teste sem caixa foi apagado antes da importação |
| `QTest.mouseMove` não gera eventos no XWayland | só do roteiro de teste | eventos de movimento enviados diretamente ao canvas |
| `pgrep -af labelImg` casou com o próprio shell e o encerrou | só do roteiro de teste | trocado por padrão com colchetes; nenhum processo do LabelImg ficou órfão |

## 6. Integridade dos dados

| Verificação | Resultado |
| --- | --- |
| Pastas de patch com `metadata.json` | 281 |
| PNG e `multiband.tif` legíveis | **281/281**, 0 problemas |
| Georreferência lida do GeoTIFF (bounds, CRS, tamanho) = `metadata.json` | **281/281** |
| `catalogo/patches.csv` | **estava ausente** no início da sessão; foi **reconstruído hoje às 18:17** a partir dos `metadata.json` (status `APROVADO` porque o SCL consta em `quality_processing.used_layers`), sem regravar rasters. Arquivo não versionado |
| Catálogo x metadados | 0 de 281 linhas divergentes (patch_id, cena, data, coleção, CRS, bbox, tamanho, bandas, nuvem, válidos); nenhuma coordenada inventada: o bbox é o `bounds` do metadata, que confere com o GeoTIFF |
| Arquivos em `data/dataset` e `data/sentinel2` modificados após o início da sessão (15:30) | **0** |
| `best.pt` | SHA-256 `413e950522b7428c…` = manifesto; sem diferenças em relação à `main` |
| Rótulos anteriores | não existiam: colunas `label` do catálogo vazias; projeto real "Talhões MT" com 0 rotuladas e 0 anotações |
| Hashes no laboratório x arquivos atuais | 0 divergências (real e teste) |

Observação: mais cedo nesta sessão, a pedido do usuário, foi removido um
temporário legado não retomável (`.B03.tif.iy2xq9ea.part.tif`, 91 MB) de
`data/sentinel2/2026-04-23/S2-16D_V2_014017_20260423/`. Nenhum arquivo final foi
apagado.

## 7. Pendências

- **Rotulagem manual real** (à mão, com critério visual) ainda não foi feita.
  Atribuir `talhao` exige evidência visual; não use o laboratório de teste para
  treino.
- O laboratório de teste `data/laboratorio-teste-e6/` foi mantido para conferência.
  Ele pode ser apagado depois, e não interfere no projeto real.
- As correções desta validação estão **commitadas localmente**, sem push, conforme
  as regras; o PR #20 não as contém até um push autorizado.
- O build Windows do PR #19 estava em andamento; o merge foi suspenso por esta
  instrução.

## 8. Como iniciar a rotulagem manual

1. Instalação única do LabelImg (já feita nesta máquina):

   ```bash
   pipx install uv
   uv tool install --python 3.9 labelImg
   ```

2. Abra o aplicativo e use o Laboratório:

   ```bash
   cd "/home/pedro-henrique/Área de trabalho/Faculdade_2026.2/Laboratorio_de_Programacao/Trabalhos/sentinel2-mt-downloader"
   python iniciar_gui.py
   ```

   Em **Laboratório de IA**, escolha o projeto **Talhões MT**, selecione as imagens,
   marque **Somente as selecionadas**, clique em **Preparar lote** e depois em
   **Abrir no LabelImg**. No LabelImg, troque o formato para **YOLO**, desenhe com
   **W**, salve com **Ctrl+S**, avance com **D**. Volte ao app e clique em
   **Importar anotações**.

3. Comando direto do LabelImg para um lote já preparado (o app usa exatamente
   estes argumentos):

   ```bash
   L="data/laboratorio/projetos/1/rotulagem/lote-atual"
   labelImg "$L/images" "$L/classes.txt" "$L/labels"
   ```

Evidências visuais: `reports/validacao-labelimg-e6/labelimg-aberto.png`,
`labelimg-anotado.png` e `labelimg-revisao.png`.
