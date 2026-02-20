# Azulejo Vectorization Pack (auto-generated)

Status: draft vectors generated from all uploaded photos.

## Outputs

- `*.tile.png` → recorte central do módulo (tile unit)
- `*.tile.svg` → vetor editável (cores separadas por formas)
- `report.json` → metadados do processamento
- `families.json` → agrupamento sugerido por modelo

Directory:
`/home/openclaw/.openclaw/workspace/artifacts/azulejo`

## Pipeline usado (autônomo)

1. Detectar linhas de rejunte (Hough lines)
2. Recortar módulo central de cada foto
3. Quantizar cores (kmeans)
4. Vetorizar máscaras por cor com Potrace
5. Compor SVG final por camadas

Script:
`/home/openclaw/.openclaw/workspace/scripts/azulejo_vectorize.py`

## Ferramentas recomendadas para acabamento final

1. Adobe Illustrator (Image Trace + edição manual)
2. Inkscape (Trace Bitmap + node editing)
3. Vectorizer.AI (vetorização inicial rápida)
4. Vector Magic (pré-impressão)
5. Potrace + OpenCV (lote/automação)

## Próximas ações sugeridas

- Revisar e corrigir simetria de cada `tile.svg`
- Padronizar tamanho físico (ex: 20x20 cm ou 15x15 cm)
- Definir paletas finais em spot colors
- Exportar pacote final: SVG + PDF/X-1a para gráfica
