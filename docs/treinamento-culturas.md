# Treinamento experimental de culturas (YOLO)

Este piloto **nao altera** o detector de talhoes existente. O codigo prepara um classificador RGB com quatro classes: soja, milho, algodao e outras. Ainda nao representa um modelo treinado ou validado.

## 1. Preparar ambiente (Ubuntu Linux)

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install ultralytics
```

No Ubuntu, utilize `.venv/bin/python` nos comandos. O peso de classificacao inicial sera baixado pela biblioteca na primeira execucao. Isso exige internet.

## 2. Adquirir e auditar rotulos

Fonte candidata: [MapBiomas Brasil](https://brasil.mapbiomas.org/) (agricultura e mapas anuais/por safra). Verifique licenca, legenda e disponibilidade da cultura/ano/regiao antes de exportar. Rotulos geograficos devem ser cruzados com rasters Sentinel-2 da **mesma safra e periodo**; nao use o mapa como se fosse imagem RGB ja rotulada. Conferir manualmente amostras e evitar pixels de culturas misturadas, bordas, nuvens e sombras.

Estrutura esperada:
```text
data/culturas/
  train/
    soja/       milho/       algodao/       outras/
  val/
    soja/       milho/       algodao/       outras/
```
Insira imagens PNG/JPG verificadas em cada pasta. **Nao distribua patches vizinhos ou do mesmo talhao entre train e val:** separe por talhoes/regioes e, para testar generalizacao temporal, por safras. Reserve ainda um conjunto de teste independente. O script nao baixa nem inventa imagens ou rotulos.

## 3. Verificar e executar

```bash
.venv/bin/python tools/treinar_culturas_yolo.py --data data/culturas --dry-run
.venv/bin/python tools/treinar_culturas_yolo.py --data data/culturas --epochs 30 --batch 8 --device cpu
```

A saida em `runs/culturas/` inclui logs, pesos, graficos Ultralytics e `proveniencia.json`. Valide no teste independente com matriz de confusao, precision/recall/F1 por classe e erro entre safras, **antes de qualquer resultado cientifico**. Treino RGB e apenas baseline: soja/milho/algodao podem exigir classificacao espectral e multitemporal.

## Hermes (futuro)

Hermes pode invocar o script por subprocesso **somente com aprovacao**, coletar métricas e comparar experimentos. Nao esta integrado neste PR. O detector existente (best.pt) continua sendo usado pela analise atual.
