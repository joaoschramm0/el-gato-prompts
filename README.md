# El Gato Prompts — MVP Local

Pipeline local para extrair prompts do rodapé de lives do Claude Code no YouTube e medidor de caos para avaliar agressividade e pedidos mal formulados.

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

## Modo visão via OpenCode (opt-in)

Com `--vision-model`, o Python chama o FFmpeg para recortar os frames, agrupa
recortes consecutivos quase iguais e envia um representante de cada grupo ao
agente de transcrição no OpenCode.

Dependências adicionais:

- OpenCode CLI instalado/autenticado (`opencode run --help`) com um modelo de
  visão, como `opencode-go/qwen3.8-flash`.
- Agente local `.opencode/agents/vision-ocr.md` (incluído no repo): sem
  ferramentas, para que o texto das imagens nunca vire instrução executável.
- `ffmpeg`. Para baixar seções do YouTube, use `pip install -U 'yt-dlp[default]'`
  e tenha Node.js ou Deno no PATH.
  A seção remota depende de a URL permitir seek; em live ainda em andamento o
  yt-dlp pode recusar o corte (falha explícita, sem baixar a live inteira).
- Nenhuma dependência Python nova (Pillow já é usada).

```bash
# URL: baixa só a seção [--start, --start+--sample) via yt-dlp,
# em vez de baixar a live inteira
python -m src.pipeline \
  --url "https://www.youtube.com/live/r0ZCtDkt3RA" \
  --vision-model opencode-go/qwen3.8-flash --vision-chat-history \
  --start 7785 --sample 60 \
  --out data/r0ZCtDkt3RA-prompts.json

# vídeo local: aceita offset e amostragem do mesmo jeito
python -m src.pipeline \
  --url local --skip-download --video data/live.mp4 \
  --vision-model opencode-go/qwen3.8-flash \
  --start 600 --sample 120 --out data/prompts_vision.json
```

Comportamento:

- Cada execução guarda os recortes numa subpasta própria sob
  `data/frames_vision` ou sob o `--frames-dir` informado.
- O recorte padrão pega o rodapé (88%–100%). `--vision-chat-history` recorta
  a coluna central e lê a última mensagem do usuário visível no histórico.
- No modo histórico, cada frame localiza a última bolha escura alinhada à
  direita. Se a localização for inequívoca, a deduplicação compara apenas uma
  janela ancorada nessa bolha (resposta, cursor, contador e scroll ficam fora)
  e o OCR recebe um recorte localizado em 2x (campo `crop_frame`). Sem bolha
  confiável (cena cortada, bolha ambígua ou à esquerda), o frame inteiro é
  usado como fallback conservador; isso pode abrir um grupo a mais, nunca
  fundir prompts distintos.
- Timestamps são `start + índice/fps` (não “índice como segundo”).
- Grupos comparam cada frame com a referência do grupo. Os limiares
  `--vision-mean-threshold` (padrão 1.0) e `--vision-changed-ratio` (padrão
  0.0005) são conservadores para distinguir edições curtas. Na comparação
  localizada da bolha, o limite de média sobe para 1.5 (ruído de escala),
  mas a fração de pixels alterados continua em 0.0005 para pegar trocas de
  número e edições de um dígito.
- Só grupos com barra escura são enviados por padrão; use
  `--vision-include-non-dark` para analisar todos.
- Cada chamada anexa até `--vision-batch-size` imagens (padrão 4) e usa
  `opencode run --agent vision-ocr --model MODELO --format json --file ...`.
  Se uma chamada exceder o tempo limite, o código divide aquele lote.
  IDs e schema são validados; falha de comando/modelo/schema aborta com código
  não-zero e **não** sobrescreve o JSON existente com corpus vazio.
- Saída padrão: `data/prompts_vision.json`, lista de ocorrências consolidadas:

```json
[
  {
    "text": "crie um app de gatos",
    "status": "ok",
    "ts_seconds": 3600,
    "frame": "data/frames_vision/run-exemplo/frame_000001.jpg",
    "frame_index": 0,
    "ts_end_seconds": 3612,
    "frame_end": "data/frames_vision/run-exemplo/frame_000013.jpg",
    "frame_count": 13,
    "group_ids": ["img_000001"],
    "crop_frame": "data/frames_vision/run-exemplo/_vision_bubbles/img_000001.jpg",
    "model": "opencode-go/qwen3.8-flash",
    "fps": 1.0,
    "start_seconds": 3600.0
  }
]
```

`crop_frame` só aparece quando a bolha foi localizada com confiança; nesse
caso é a imagem enviada ao modelo. O resumo da CLI inclui `calls`, o número
efetivo de chamadas ao OpenCode (incluindo divisões por timeout).

Limitações da localização: ela assume o tema escuro observado (fundo da bolha
em cinza ~30–50), margem direita de até 13% e bolha começando depois de 10%
da largura. Fora desses limites — ou se a bolha estiver cortada por uma borda —
o frame inteiro é usado. A janela de comparação cobre 86% da largura ancorada
à direita e se estende quando necessário para incluir o início de bolhas largas.

O JSON guarda os prompts com texto, com status `ok` ou `partial`.
A transcrição preserva o texto visível e exclui os elementos da interface.
No modo histórico, o timestamp registra quando o prompt foi visto no trecho.
Nesse modo, variações de leitura de uma mesma mensagem em frames consecutivos
podem ser agrupadas, mantendo o texto da primeira leitura.

Teste focado sem serviços remotos:

```bash
python -m unittest discover -s tests -v
```

## Módulos

- `src/download.py` — `yt_dlp.YoutubeDL` com `format bv*[height<=720]+ba`, `outtmpl data/%(id)s.%(ext)s`, `writeinfojson`; helpers `get_info/get_duration/get_title/get_stream_url`, `download(url, get_url_only=)` e `download_section(url, start_seconds=, duration=)` para baixar só uma seção (modo visão remoto)
- `src/extract.py` — FFmpeg com recorte do rodapé ou da coluna do chat, amostragem, detecção de barra escura e OCR (EasyOCR → Tesseract)
- `src/vision.py` — modo visão: agrupamento conservador de frames, lotes via `opencode run --format json`, validação de IDs/status, consolidação de repetições e JSON atômico
- `src/scorer.py` — `embed(texts)` e `score(prompt_user, corpus) -> {score 0-100, closest_prompt, all_scores}`
  - baseline: TF-IDF `char_wb 3-5` + cosine (sklearn, free, sem API)
  - se `sentence_transformers` instalado: `all-MiniLM-L6-v2`
  - se `OPENCODE_API_URL` setado: `qwen3-embedding-8b` via API opencode-go
- `src/pipeline.py` — CLI `python -m src.pipeline --url URL --sample 300 --limit-frames 60 --out data/prompts.json`; `--vision-model MODELO` ativa o modo visão
- `src/build_corpus.py` — consolida o corpus (ver abaixo) em `data/corpus.json` + `web/corpus.js`

## Corpus consolidado

`data/corpus.json` (e `web/corpus.js`) agrupam todos os prompts já extraídos —
dos `data/*-prompts.json` (modo visão) e de `data/prompts_unique.json` (OCR da
live) — normalizados para `{text, status, ts_seconds, source_live, source_file}`,
deduplicados por texto normalizado (lower, sem acento, sem pontuação extra),
com fragmentos `partial` do mesmo texto mesclados, vazios filtrados e ordenados
por timestamp. Cada entrada carrega o id da live (`source_live`) e o arquivo de
origem.

Regenerar (sem dependências novas, só stdlib):

```bash
python -m src.build_corpus
```

Atualmente o corpus tem **39 prompts**: 34 do OCR da live `wbSKZ3SIu9M` (2 dos
35 viraram 1 na dedup por acento), 1 validado de `r0ZCtDkt3RA`, 1 de
`VuGxO7s_2GA`, 2 de `dAonwLC8tnQ` @2h, e os 3 fragmentos de `0DlH_bDds0Q`
mesclados em 1.

## Medidor de caos

Abra `web/index.html` no navegador. A nota combina regras locais de
agressividade (xingamentos, cobrança, gritos) e de formulação do pedido
(contexto insuficiente, escopo vago, instruções contraditórias). O Claude
simulado reage aos sinais encontrados. A partir de 82%, toca o OOOO CRAUD!
e a tela entra em pane.

## Ferramenta de similaridade (Python)

```python
from src.scorer import score
res = score("crie um app de gatos", ["prompt A", "prompt B"])
print(res["score"], res["closest_prompt"])
# ou CLI
python -m src.scorer --prompt "meu prompt" --corpus-file data/prompts.json
```

## Notas

- Pipeline padrão não exige API key. OCR/embeddings são opcionais com fallback.
- O modo visão é opt-in e requer OpenCode CLI + modelo de visão (pode ser local ou remoto, conforme o provider configurado).
- Para live completa (4h) remova `--sample`/`--limit-frames` (irá baixar ~720p + extrair ~15k frames).
