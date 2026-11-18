# Limpeza inteligente e novas imagens agrícolas — projeto 2 "Talhões MT — cobertura estadual"

Data: 2026-11-17. Código: worktree `feat/cobertura-mato-grosso` (sem commit). Dados: `sentinel2-mt-downloader/data/`.

## 1. Auditoria e backup

- Backup verificável: `data/backups/antes-triagem-20261117-153602/` (banco via API de backup do SQLite, lotes de rotulagem, catálogo e diários, 100 `metadata.json`), com `manifesto-sha256.json`. 160 cópias, 0 divergências.
- Ao final, conferência contra o manifesto: 301 rasters/PNG do piloto e `best.pt` inalterados; catálogo, diários e `cobertura.json` do piloto inalterados; as 8 anotações do banco são idênticas às do backup; `pragma integrity_check` = ok.

## 2. Triagem das 100 imagens do piloto (`piloto-001`)

Feita por inspeção visual de cada PNG em folhas de contato numeradas. A fração de cultivo e árvore do ESA WorldCover 2021 serviu só de apoio. Decisões e motivos estão em `data/amostras-mt/piloto-001-triagem.json`; a folha com as decisões é `triagem-piloto-001.jpg`.

| Triagem | Qtde | Situação |
| --- | --- | --- |
| Protegida (rotulada) | 8 | ativa; não pode ser arquivada nem removida |
| Aprovada | 23 | ativa |
| Revisar | 16 | ativa, marcada (pastagem × cultivo incerto, poucos talhões) |
| Descartar → arquivada | 53 | arquivamento lógico (floresta 27, cerrado 12, área úmida/rio 7, pastagem 5, talhão só na borda 1, sem feições 1) |

Nenhum arquivo foi apagado. O catálogo científico não mudou. Imagens sem `.txt` não foram tratadas como negativas.

## 3. Arquivamento lógico (código + testes)

- `RepositorioLaboratorio`: migração aditiva (`triagem`, `triagem_motivo`, `arquivada`, `arquivada_em`, `motivo_arquivamento`, tabela `historico_imagens`), `registrar_triagem`, `arquivar_imagens`, `restaurar_imagens` e `historico_imagens`, cada uma numa transação. Imagem rotulada é recusada e a operação inteira é cancelada. `remover_imagens` também recusa imagem rotulada.
- `preparar_lote` ignora imagens arquivadas e descartadas. O lote anterior **não é mais apagado**: vai para `lotes-anteriores/<data>`. A troca é recusada (`LoteComPendencias`) se houver `.txt` não importado ou diferente do banco.
- Interface do Laboratório: filtro Situação (Ativas, Aprovadas, Triagem: revisar, Protegidas, Arquivadas, Todas) e botões **Aprovar**, **Marcar inadequada e arquivar…** (pede o motivo) e **Restaurar**. O detalhe da imagem mostra a triagem e o motivo do arquivamento.
- Validação numa **cópia** do banco real: a imagem rotulada foi recusada e a transação preservada; arquivar e restaurar funcionaram nos dois sentidos; o banco original ficou com hash inalterado.

## 4–6. Novas imagens (lote `agricola-001`)

- **Referência de priorização:** ESA WorldCover 10 m v200 (**2021**, CC BY 4.0), lida dos COGs públicos. O ano difere das imagens (2026), e isso está registrado em cada `metadata.json` (`referencia_uso_solo`). A MapBiomas não tem download anônimo direto (403).
- **Candidatas:** 170 pontos com cultivo ≥ 40 % e árvore ≤ 50 % no mapa de 0,05°. São 7 por município prioritário (12 municípios) e o restante intercalado nas 5 regiões intermediárias. Os pontos ficam a ≥ 12 km entre si e a ≥ 8 km das amostras existentes (`agricola-001-candidatas.json`).
- **Coleta:** INPE BDC STAC `S2_L2A-1`, de 01/06 a 30/09/2026, janelas 512×512 de B02/B03/B04 a 10 m, SCL a 20 m, nuvem e sombra ≤ **2 %** por recorte. Foram avaliadas 121 candidatas: **120 aceitas** e 1 falha de coleta (HTTP 502 do STAC; não é imagem).
- **Validação independente** (relê os arquivos gravados): 120/120 sem problema. Nuvem/sombra média de 0,03 % e máxima de 0,27 %; mínimo de pixels válidos de 100 %. Bandas B02/B03/B04 em uint16, nodata 0, 10 m, 512×512; CRS EPSG:32721 (96) e EPSG:32722 (24); 34 tiles MGRS. Datas: jun 1, jul 28, ago 68, set 23. Sem PNG duplicado, 0 sobreposições com outros lotes (distância mínima de 11,1 km) e 0 dentro do lote. Catálogo `patches.csv` = arquivos.
- **Inspeção visual** (`triagem-agricola-001.jpg`): todas as 120 mostram talhões agrícolas. **115 aprovadas**; **5 a revisar** (#30, #83, #84, #93 e #114: talhões muito grandes ou de baixo contraste, com limites internos pouco visíveis). **0 rejeitadas** (nenhum recorte só de floresta).

Distribuição das 115 aprovadas:

- Por região intermediária: Cuiabá 35, Sinop 32, Rondonópolis 22, Barra do Garças 19, Cáceres 7.
- Por município prioritário: 7 em cada um, exceto Querência (6).
- Grupo de diversidade: 32.

## 7. Integração ao projeto 2

As 115 aprovadas foram adicionadas com triagem "aprovada". As 5 a revisar **não** foram integradas. Contagens do projeto 2:

| Total | Ativas | Arquivadas | Aprovadas | Revisar (triagem) | Protegidas/rotuladas | Pendentes ativas | Duplicatas |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 215 | 162 | 53 | 138 | 16 | 8 | 154 | 0 |

## Novo lote de 20 para o LabelImg

- Na primeira tentativa, a proteção bloqueou a troca do lote. O arquivo `…_8bc1281faf1c_…_512.txt` tinha sido editado às 15:56, depois da importação das 15:31, e o LabelImg (processo 944883) estava aberto no lote.
- Com autorização do usuário, o processo foi encerrado. O arquivo continuou idêntico ao das 15:56.
- A anotação editada foi importada. Só ela mudou no banco: 1 caixa válida. A versão anterior continua no backup. Continuam 8 imagens rotuladas e 8 anotações.
- O lote anterior foi movido para `data/laboratorio/projetos/2/rotulagem/lotes-anteriores/20261117T204828Z/`. Os 9 arquivos de rótulo (8 `.txt` e `classes.txt`) foram conferidos por SHA-256 e estão idênticos. As 12 imagens desse lote sem anotação continuam pendentes e **não** são negativas.
- O novo lote está em `data/laboratorio/projetos/2/rotulagem/lote-atual/` e tem 20 imagens aprovadas do `agricola-001`: uma por grupo (12 municípios prioritários e 5 regiões intermediárias), mais as 3 seguintes com maior fração de cultivo. A lista está em `data/amostras-mt/agricola-001-lote-labelimg-1.json`.
- A conferência no LabelImg 1.8.6 real abriu e percorreu as imagens sem desenhar nem salvar (`labelimg-lote-agricola.png`). As 20 imagens abriram em 512×512, com 0 caixas, classe `talhao`, formato YOLO e pasta de saída `labels`. A conferência não alterou nenhum arquivo do lote (SHA-256 igual antes e depois).

Para abrir o lote:

```bash
cd "<sentinel2-mt-downloader>" && labelImg data/laboratorio/projetos/2/rotulagem/lote-atual/images data/laboratorio/projetos/2/rotulagem/lote-atual/classes.txt data/laboratorio/projetos/2/rotulagem/lote-atual/labels
```

## Regras

Nenhum dado apagado, nenhum rótulo sobrescrito, nenhuma anotação automática, `best.pt` inalterado, sem treinamento, sem commit, push ou merge.

## Testes

`python -m unittest discover -s tests -t .` no worktree: **300 testes, OK**. Os testes novos são `tests/test_triagem.py` (8), `tests/test_agricola.py` (4) e um teste de GUI de arquivar/restaurar.

## Treino curto de teste (protótipo)

- **Anotações:** importadas as do 2º lote (20 imagens, 157 caixas, 0 erros; backup antes em `data/backups/antes-importacao-lote2-20261117-171609/`). O projeto ficou com 28 imagens rotuladas e 217 caixas. Cerca de 10 imagens têm talhões visíveis sem caixa (`lote2-caixas.jpg`).
- **Pasta nova:** `data/laboratorio/projetos/2/treinos/prototipo-20261117-212050`, com dataset, `manifesto.json`, `resultado.json`, execução e avaliações.
- **Divisão espacial do E7** (`feat/laboratorio-treino`, grupos de grade+bloco 4 sem vazamento): 19 treino / 6 validação / 3 teste.
- **Pesos iniciais:** cópia do `best.pt` da aplicação (YOLO26n, 22 classes; cabeça reajustada para 1 classe). Nenhum peso foi baixado. O `best.pt` original segue inalterado (SHA `413e9505…`).
- **Treino:** 40 épocas, imgsz 512, batch 8, GTX 1650. A melhor época foi a 34.

| Split | Precisão | Revocação | mAP50 | mAP50-95 |
| --- | --- | --- | --- | --- |
| Validação (6) | 0,218 | 0,375 | 0,126 | 0,032 |
| Teste (3) | 0,348 | 0,348 | 0,238 | 0,082 |

Leitura (`prototipo-predicoes.jpg`):

- O modelo já encontra talhões retangulares e pivôs nas áreas de lavoura densa (ex.: `ec4e8358976a`, `53ad2a4a76f7`).
- Ainda gera caixas sobrepostas para o mesmo talhão.
- Erra em paisagem mista (`fedecd8ee975`, recortes pequenos e fragmentados).
- As métricas são **pouco confiáveis**: 6 imagens de validação, e várias rotuladas com só 3–4 caixas, o que faz acertos reais contarem como falsos positivos.
- **É um protótipo e não deve substituir nenhum modelo.**

## Correção dos rótulos e treino de avaliação (2026-11-18)

- **Correção:** o lote de correção (19 imagens) foi importado: 17 importadas, 0 erros. Backup antes em `data/backups/antes-importacao-correcao-20261118-104238/`.
- **Imagens retiradas a pedido do usuário (11):** foram retiradas porque têm caixa cobrindo vários talhões. O critério foi caixa com mais de 15 % da imagem, confirmada visualmente: `0911c182aad8`, `86d968cc52af`, `531674866ec8`, `058b1d62a769`, `74bb22b0a50f`, `c20db35a69d9`, `a34c17775cf2`, `5687cc99e7b7`, `de41ab0db9b5`, `26b9620f8a2d`, `5d45a4064d15`.
  - Usada a nova ação `retirar_rotuladas`: a imagem fica arquivada e em "revisar", e a anotação continua no banco, sem alteração.
  - O botão Restaurar devolve o estado "rotulada". A importação e a troca de lote ignoram imagens retiradas.
  - Backup em `data/backups/antes-retirada-20261118-103327/`.
- **Projeto 2:** 17 imagens rotuladas ativas, 172 caixas, 151 ativas, 64 arquivadas.
- **Treino:** em `data/laboratorio/projetos/2/treinos/treino-20261118-144347`, com os mesmos pesos iniciais do protótipo (cópia do `best.pt`) e a mesma divisão espacial do E7 (11 treino / 4 validação / 2 teste).
  - Configurado para 100 épocas com parada antecipada (paciência 30): parou na 63, e a melhor foi a 37.

| Split | Precisão | Revocação | mAP50 | mAP50-95 |
| --- | --- | --- | --- | --- |
| Validação (4) | 0,302 | 0,342 | 0,285 | 0,123 |
| Teste (2) | 0,299 | 0,211 | 0,144 | 0,085 |

Leitura (`treino-predicoes.jpg`):

- Na validação, o mAP50 subiu de 0,126 (protótipo, rótulos com caixas grandes) para 0,285.
- No teste, que tem só 2 imagens e é um conjunto diferente, caiu de 0,238 para 0,144. Com 2 a 4 imagens por avaliação, a variação é grande e os números servem só como indicação.
- O modelo localiza os talhões, mas, com confiança ≥ 0,25, gera muitas caixas sobrepostas para o mesmo talhão. Também dispara em vegetação nativa (`0e20b31256d0`, que tem caixas de provável pastagem no rótulo).
- O `best.pt` da aplicação está inalterado (SHA `413e9505…`).

### Limitações e próximos passos

- Só 17 imagens rotuladas: o próprio diagnóstico recomenda ao menos 20 por classe, e o ideal são 100 ou mais.
- Há imagens com talhões sem caixa, o que o modelo aprende como "não talhão".
- As 11 retiradas podem voltar depois de corrigidas (Restaurar e reimportar). Ainda há 95 imagens agrícolas aprovadas esperando rotulagem.
