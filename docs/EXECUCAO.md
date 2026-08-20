# Execução

## 1. Atualizar dependências

```powershell
pip install -r requirements.txt
```

## 2. Baixar uma cena aprovada para teste

```powershell
python src\baixar_inpe_mt.py --baixar --max-itens 1
```

## 3. Gerar somente 20 patches para validar

```powershell
python src\gerar_patches.py --max-patches 20
```

Abra os arquivos `preview_rgb.jpg` dentro de `data/patches/`.

## 4. Conferir o catálogo

```text
catalogo/catalogo_patches.csv
```

Campos principais:

- `patch_id`
- `scene_id`
- `data`
- `cloud_shadow_pct`
- `valid_data_pct`
- coordenadas WGS84
- `preview`
- `label`
- `observacao`

## 5. Aumentar gradualmente

Depois de validar os 20 patches:

```powershell
python src\gerar_patches.py --max-patches 200
```

Para processar todos os patches elegíveis já baixados:

```powershell
python src\gerar_patches.py
```

Não é recomendado liberar todo o estado antes de validar visualmente uma amostra e medir o volume esperado.
