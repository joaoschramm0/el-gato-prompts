# El Gato Prompts — MVP Local

Pipeline 100% local para extrair prompts do rodapé de live do Claude Code no YouTube e termômetro de similaridade (0-100).

**Live alvo:** https://www.youtube.com/live/wbSKZ3SIu9M (4h21) — ROI do prompt é barra rodapé com texto + footer `Opus 5.5 Extra Ultracode Ignorar permissões`

## Requisitos

- Python 3.14
- ffmpeg instalado (`ffmpeg -version`)
- pip

## Instalação

```bash
pip install -r requirements.txt

# opcional OCR (um dos dois; se nenhum, pipeline salva frames e loga aviso sem quebrar)
pip install easyocr            # recomendado, offline após 1º download de modelo
# ou
pip install pytesseract        # requer tesseract-ocr no OS

# opcional embeddings melhores (se não instalado, usa TF-IDF char_wb baseline free)
pip install sentence-transformers
```

## Uso rápido (sem baixar 4h)

```bash
# teste 5 minutos (não baixa live completa)
python -m src.pipeline --url "https://www.youtube.com/live/wbSKZ3SIu9M" --sample 300

# limitar por frames (fps=1, então 60 = 60s)
python -m src.pipeline --url "https://www.youtube.com/live/wbSKZ3SIu9M" --limit-frames 60

# com vídeo já baixado
python -m src.pipeline --url "https://www.youtube.com/live/wbSKZ3SIu9M" --skip-download --video data/wbSKZ3SIu9M.mp4 --sample 300

# só resolver URL do stream (debug)
python -m src.pipeline --url "https://www.youtube.com/live/wbSKZ3SIu9M" --get-url

# pular OCR (só extrai frames)
python -m src.pipeline --url "..." --sample 60 --no-ocr
```

Saída: `data/prompts.json` + `data/frames/frame_*.jpg`

## Módulos

- `src/download.py` — `yt_dlp.YoutubeDL` com `format bv*[height<=720]+ba`, `outtmpl data/%(id)s.%(ext)s`, `writeinfojson`; helpers `get_info/get_duration/get_title/get_stream_url`, `download(url, get_url_only=)`
- `src/extract.py` — `ffmpeg -vf fps=1,crop=w=iw:h=ih*0.12:x=0:y=ih*0.82,scale=2*iw:2*ih:flags=neighbor`, detecção barra escura + OCR (EasyOCR→Tesseract, degradação graciosa)
- `src/scorer.py` — `embed(texts)` e `score(prompt_user, corpus) -> {score 0-100, closest_prompt, all_scores}`
  - baseline: TF-IDF `char_wb 3-5` + cosine (sklearn, free, sem API)
  - se `sentence_transformers` instalado: `all-MiniLM-L6-v2`
  - se `OPENCODE_API_URL` setado: `qwen3-embedding-8b` via API opencode-go
- `src/pipeline.py` — CLI `python -m src.pipeline --url URL --sample 300 --limit-frames 60 --out data/prompts.json`

## Termômetro de similaridade

```python
from src.scorer import score
res = score("crie um app de gatos", ["prompt A", "prompt B"])
print(res["score"], res["closest_prompt"])
# ou CLI
python -m src.scorer --prompt "meu prompt" --corpus-file data/prompts.json
```

## Notas

- Pipeline não exige API key. OCR/embeddings são opcionais com fallback.
- Para live completa (4h) remova `--sample`/`--limit-frames` (irá baixar ~720p + extrair ~15k frames).
