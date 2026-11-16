# Transição: detecção de talhões → classificação de culturas

## Estado real do projeto

- **Legado (preservado):** `src/sentinel2_mt/analise/models/best.pt`, `model_metadata.json` e fluxo `ServicoAnaliseAgricola` realizam **detecção** por bounding boxes. Não afirmar que esse peso distingue soja, milho ou algodão sem verificar suas classes/treinamento.
- **Novo experimento:** `tools/treinar_culturas_yolo.py` treina classificador RGB das classes `soja`, `milho`, `algodao` e `outras`, com amostras rotuladas e separação por talhão.
- **Nova inferência independente:** `tools/classificar_culturas.py` recebe **apenas um peso de classificação**, rejeita peso de detecção e nomes de classes incompatíveis, e gera CSV com classe, confiança e `revisao_humana=pendente`.
- **Não concluído:** integração dos resultados de culturas à GUI/mapa e ao relatório de região; delimitação georreferenciada por talhão e avaliação científica em safras independentes.

## Regra de apresentação

Não misturar caixas de detecção de talhões com resultados de classificação agrícola. Quando o classificador não estiver treinado/validado, mostrar explicitamente **cultura não determinada**. Não converter número de caixas ou pixels RGB em hectares.

## Exemplo — Ubuntu (depois de rotular e treinar)

```bash
.venv/bin/python tools/classificar_culturas.py \
  --modelo runs/culturas/piloto-rgb/weights/best.pt \
  --imagens data/dataset \
  --saida runs/culturas/resultado_culturas.csv \
  --limiar 0.65
```

`0.65` é limiar provisório, não confiança calibrada. Escolher limiar com dados de validação. A varredura de imagens é recursiva; arquivos JPEG de preview, quando presentes, não devem ser confundidos com os patches RGB homologados. Idealmente adaptar essa etapa para consumir apenas `catalogo/patches.csv`.

## Visão para a GUI

Região/intervalo escolhidos → catálogo STAC do INPE → patches aprovados → classificador validado → agrupamento espacial por talhão → mapa por classe de cultura com legenda e estado "indeterminada" → relatório citando modelo, safra e limitações.

**Critério de promoção:** só ativar classificação na interface após dataset auditado, treino executado, métricas independentes por classe e teste em região/safra externa.
