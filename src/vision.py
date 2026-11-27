"""Extração de prompts por visão via OpenCode CLI.

O modo visão é opt-in (`--vision-model`). Ele reutiliza os frames já recortados
pelo FFmpeg (rodapé 88%–100%), agrupa frames consecutivos quase idênticos,
envia pequenos lotes de imagens para `opencode run --format json --file ...` e
grava um JSON atômico com as ocorrências consolidadas.

No modo `--vision-chat-history`, a deduplicação localiza a última bolha do
usuário e compara só o recorte dela (o OCR recebe esse recorte ampliado), com
fallback para o frame inteiro quando a bolha não é localizada com confiança.

O texto lido das imagens é sempre tratado como dado: nunca é executado,
interpretado como instrução ou reenviado em outro prompt.
"""
from __future__ import annotations

import json
import logging
import os
import pathlib
import re
import subprocess
import tempfile
import unicodedata
from typing import Dict, List, Optional, Sequence

from PIL import Image, ImageChops, ImageFilter

log = logging.getLogger(__name__)

VALID_STATUSES = ("ok", "empty", "partial", "illegible")
# Espelha a heurística de src/extract.py (barra escura = barra de prompt).
DARK_THRESHOLD = 60
DARK_RATIO = 0.45
CHANGED_PIXEL_DELTA = 25
THUMB_WIDTH = 640
# Limiares conservadores: uma edição curta no texto muda alguns pixels e deve
# abrir um novo grupo em vez de ser absorvida por um grupo quase igual.
DEFAULT_MEAN_THRESHOLD = 1.0
DEFAULT_CHANGED_RATIO = 0.0005

# No modo histórico a comparação usa só a última bolha escura do usuário.
# A bolha tem fundo ~36 em tema escuro, contra ~24 da página; a faixa de cinza
# abaixo isola esse fundo sem depender de OCR nem de heurística textual.
CHAT_BUBBLE_MIN_GRAY = 30
CHAT_BUBBLE_MAX_GRAY = 50
CHAT_BUBBLE_ROW_RATIO = 0.06
CHAT_BUBBLE_MERGE_GAP = 8
CHAT_BUBBLE_RIGHT_MARGIN_RATIO = 0.13
CHAT_BUBBLE_LEFT_MARGIN_RATIO = 0.10
CHAT_BUBBLE_BOTTOM_RATIO = 0.90
CHAT_BUBBLE_MIN_WIDTH_RATIO = 0.05
CHAT_BUBBLE_MIN_HEIGHT_RATIO = 0.015
CHAT_BUBBLE_MAX_AREA_RATIO = 0.40
CHAT_CROP_PAD = 8
CHAT_CROP_SNAP = 4
CHAT_CROP_SCALE = 2
# A imagem de comparação ancora uma janela de largura fixa na borda direita da
# bolha. Assim, jitter de 1-4px na detecção não muda a escala do conteúdo, mas
# edições reais (inclusive um dígito) continuam visíveis. A janela de 86% da
# largura cobre a largura usual e se estende à esquerda para bolhas mais largas.
CHAT_COMPARE_WINDOW_RATIO = 0.86
CHAT_COMPARE_GRID = 16
CHAT_COMPARE_WIDTH = 640
CHAT_COMPARE_STEP = 8
CHAT_COMPARE_MIN_HEIGHT = 64
CHAT_COMPARE_MAX_HEIGHT = 512
CHAT_COMPARE_BLUR = 1.0
# A normalização da bolha tem ruído de escala menor que o frame inteiro; o
# limite de pixels alterados (0.05%) continua sendo o detector de edições
# pequenas, como a troca de um dígito.
CHAT_COMPARE_MEAN_THRESHOLD = 1.5

_RESAMPLE = getattr(Image, "Resampling", Image).BILINEAR
_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.IGNORECASE | re.DOTALL)


class VisionError(RuntimeError):
    """Falha explícita do modo visão (modelo, comando ou saída inválida)."""


class VisionTimeout(VisionError):
    """Tempo limite de uma chamada ao OpenCode."""


def frame_timestamp(frame_index: int, fps: float, start_seconds: float = 0.0):
    """Timestamp real do frame: offset + índice/fps (não índice como segundo)."""
    if fps <= 0:
        raise ValueError("fps deve ser > 0")
    value = round(float(start_seconds) + frame_index / float(fps), 3)
    return int(value) if float(value).is_integer() else value


def _thumbnail(image_path: str) -> Image.Image:
    try:
        with Image.open(image_path) as im:
            img = im.convert("L")
            if img.width > THUMB_WIDTH:
                height = max(1, round(img.height * THUMB_WIDTH / img.width))
                img = img.resize((THUMB_WIDTH, height), _RESAMPLE)
            return img.copy()
    except Exception as e:  # noqa: BLE001 - vira falha explícita do modo visão
        raise VisionError(f"não foi possível ler o frame {image_path}: {e}") from e


def _diff_metrics(ref: Image.Image, img: Image.Image):
    if ref.size != img.size:
        return 255.0, 1.0
    diff = ImageChops.difference(ref, img)
    hist = diff.histogram()
    total = ref.width * ref.height
    if total <= 0:
        return 255.0, 1.0
    mean = sum(i * count for i, count in enumerate(hist)) / total
    changed = sum(hist[CHANGED_PIXEL_DELTA + 1:]) / total
    return mean, changed


def is_dark_bar(
    image_path: str,
    dark_threshold: int = DARK_THRESHOLD,
    dark_ratio: float = DARK_RATIO,
) -> bool:
    """Heurística equivalente a extract.is_dark_bar, sem depender de OpenCV."""
    try:
        with Image.open(image_path) as im:
            gray = im.convert("L")
            hist = gray.histogram()
            total = gray.width * gray.height
        if total <= 0:
            return False
        return sum(hist[:dark_threshold]) / total > dark_ratio
    except Exception as e:  # noqa: BLE001
        raise VisionError(f"não foi possível avaliar a barra escura em {image_path}: {e}") from e


def locate_last_user_bubble(image_path: str) -> Optional[tuple]:
    """Localiza a última bolha escura do usuário, alinhada à direita.

    Retorna `(x0, y0, x1, y1)` (x1/y1 exclusivos) só quando a bolha é
    inequívoca. Casas de ferramenta, respostas, rodapé e a caixa "Responder"
    ficam de fora pelos limites de margem/altura. Em cena ambígua devolve
    `None`, e o chamador volta ao agrupamento conservador pelo frame inteiro.
    """
    try:
        with Image.open(image_path) as im:
            gray = im.convert("L")
            width, height = gray.size
            mask = gray.point(
                lambda value: (
                    255
                    if CHAT_BUBBLE_MIN_GRAY <= value <= CHAT_BUBBLE_MAX_GRAY
                    else 0
                )
            )
            densities = list(mask.resize((1, height), Image.BOX).getdata())
    except Exception as e:  # noqa: BLE001
        raise VisionError(f"não foi possível localizar a bolha em {image_path}: {e}") from e

    if width <= 0 or height <= 0:
        return None

    min_row = max(1, round(255 * CHAT_BUBBLE_ROW_RATIO))
    bands: List[List[int]] = []
    start: Optional[int] = None
    for y, density in enumerate(densities):
        if density >= min_row and start is None:
            start = y
        elif density < min_row and start is not None:
            bands.append([start, y - 1])
            start = None
    if start is not None:
        bands.append([start, height - 1])

    merged: List[List[int]] = []
    for y0, y1 in bands:
        if merged and y0 - merged[-1][1] - 1 <= CHAT_BUBBLE_MERGE_GAP:
            merged[-1][1] = y1
        else:
            merged.append([y0, y1])

    candidates = []
    for y0, y1 in merged:
        if y1 - y0 + 1 < CHAT_BUBBLE_MIN_HEIGHT_RATIO * height:
            continue
        bbox = mask.crop((0, y0, width, y1 + 1)).getbbox()
        if not bbox:
            continue
        x0, top, x1, bottom = bbox
        top += y0
        bottom += y0
        right_margin = width - x1
        if right_margin > CHAT_BUBBLE_RIGHT_MARGIN_RATIO * width:
            continue
        if x0 < CHAT_BUBBLE_LEFT_MARGIN_RATIO * width:
            continue
        if x1 - x0 < CHAT_BUBBLE_MIN_WIDTH_RATIO * width:
            continue
        if bottom > CHAT_BUBBLE_BOTTOM_RATIO * height:
            continue
        if (x1 - x0) * (bottom - top) > CHAT_BUBBLE_MAX_AREA_RATIO * width * height:
            continue
        # Bolha cortada pela borda da imagem: leitura incompleta, sem confiança.
        if x0 <= 0 or x1 >= width - 1 or top <= 0:
            continue
        candidates.append((x0, top, x1, bottom))

    if not candidates:
        return None
    candidates.sort(key=lambda box: (box[3], box[2]))
    last = candidates[-1]
    # Duas bolhas coladas na mesma faixa: não há como escolher com segurança.
    if any(
        other is not last and other[3] >= last[3] - CHAT_BUBBLE_MERGE_GAP
        for other in candidates
    ):
        return None
    return last


def _snapped_bbox(bbox, width: int, height: int) -> tuple:
    """Expande a bbox para uma grade fixa; absorve jitter de 1-3px da detecção."""
    x0, y0, x1, y1 = bbox
    step = CHAT_CROP_SNAP
    x0 = max(0, (x0 // step) * step)
    y0 = max(0, (y0 // step) * step)
    x1 = min(width, ((x1 + step) // step) * step)
    y1 = min(height, ((y1 + step) // step) * step)
    return x0, y0, x1, y1


def _chat_bubble_crop_image(image_path: str, bbox, scale: int = 1) -> Image.Image:
    """Recorta a bolha com margem; usada tanto para comparar quanto para OCR."""
    with Image.open(image_path) as im:
        image = im.convert("RGB")
        x0, y0, x1, y1 = _snapped_bbox(bbox, image.width, image.height)
        pad = CHAT_CROP_PAD
        crop = image.crop(
            (
                max(0, x0 - pad),
                max(0, y0 - pad),
                min(image.width, x1 + pad),
                min(image.height, y1 + pad),
            )
        )
    if scale > 1:
        crop = crop.resize(
            (crop.width * scale, crop.height * scale), _RESAMPLE
        )
    return crop


def _bubble_compare_box(bbox, width: int, height: int) -> tuple:
    """Janela fixa ancorada à direita; absorve jitter de borda sem reescalar."""
    x0, y0, x1, y1 = bbox
    grid = CHAT_COMPARE_GRID
    pad = CHAT_CROP_PAD
    # A borda direita é arredondada para uma grade: variações de 1-4px na
    # detecção não mudam a largura da janela. O topo usa o padding exato para
    # não capturar a linha de resposta imediatamente acima da bolha.
    right = min(width, ((x1 + grid - 1) // grid) * grid)
    window_width = min(width, max(1, round(width * CHAT_COMPARE_WINDOW_RATIO)))
    left = max(0, min(right - window_width, x0 - pad))
    top = max(0, y0 - pad)
    bottom = min(height, y1 + pad)
    return left, top, right, bottom


def _bubble_comparison_image(image_path: str, bbox) -> Image.Image:
    """Imagem normalizada da bolha para deduplicação, imune ao resto da tela."""
    try:
        with Image.open(image_path) as im:
            gray = im.convert("L")
            crop = gray.crop(_bubble_compare_box(bbox, gray.width, gray.height))
    except Exception as e:  # noqa: BLE001
        raise VisionError(f"não foi possível recortar a bolha em {image_path}: {e}") from e
    if crop.width <= 0 or crop.height <= 0:
        raise VisionError(f"recorte de bolha vazio em {image_path}")
    target_height = round(
        crop.height * CHAT_COMPARE_WIDTH / crop.width / CHAT_COMPARE_STEP
    ) * CHAT_COMPARE_STEP
    target_height = max(
        CHAT_COMPARE_MIN_HEIGHT, min(CHAT_COMPARE_MAX_HEIGHT, target_height)
    )
    normalized = crop.resize((CHAT_COMPARE_WIDTH, target_height), _RESAMPLE)
    return normalized.filter(ImageFilter.GaussianBlur(CHAT_COMPARE_BLUR))


def attach_chat_crops(groups: Sequence[Dict], crop_dir: str) -> None:
    """Grava o recorte localizado só dos grupos representantes (melhor OCR)."""
    try:
        pathlib.Path(crop_dir).mkdir(parents=True, exist_ok=True)
    except OSError as e:
        log.warning(
            "Visão: não foi possível criar %s (%s); usando frames inteiros",
            crop_dir,
            e,
        )
        return
    for group in groups:
        bbox = group.get("bubble_bbox")
        if not bbox:
            continue
        out_path = pathlib.Path(crop_dir) / f"{group['id']}.jpg"
        try:
            crop = _chat_bubble_crop_image(
                group["frame"], bbox, scale=CHAT_CROP_SCALE
            )
            crop.save(out_path, quality=95)
        except Exception as e:  # noqa: BLE001 - fallback para o frame inteiro
            log.warning(
                "Visão: falha ao recortar a bolha de %s (%s); usando frame inteiro",
                group["frame"],
                e,
            )
            continue
        group["vision_frame"] = str(out_path)


def group_chat_frames(
    frames: Sequence[str],
    fps: float,
    start_seconds: float = 0.0,
    mean_threshold: float = DEFAULT_MEAN_THRESHOLD,
    changed_ratio: float = DEFAULT_CHANGED_RATIO,
    is_candidate=None,
    crop_dir: Optional[str] = None,
) -> List[Dict]:
    """Agrupa pelo conteúdo da última bolha do usuário no modo histórico.

    Resposta, cursor e contador ficam fora do recorte. Se a bolha não for
    localizada com confiança, aquele frame usa o frame inteiro (fallback
    conservador, que pode abrir um grupo a mais, nunca fundir prompts).
    """
    if not frames:
        raise ValueError("nenhum frame para agrupar")
    bboxes = [locate_last_user_bubble(frame) for frame in frames]
    groups = group_frames(
        frames,
        fps=fps,
        start_seconds=start_seconds,
        mean_threshold=mean_threshold,
        changed_ratio=changed_ratio,
        is_candidate=is_candidate,
        bubble_bboxes=bboxes,
    )
    if crop_dir is None:
        crop_dir = str(pathlib.Path(frames[0]).parent / "_vision_bubbles")
    attach_chat_crops(groups, crop_dir)
    return groups


def group_frames(
    frames: Sequence[str],
    fps: float,
    start_seconds: float = 0.0,
    mean_threshold: float = DEFAULT_MEAN_THRESHOLD,
    changed_ratio: float = DEFAULT_CHANGED_RATIO,
    is_candidate=None,
    bubble_bboxes: Optional[Sequence[Optional[tuple]]] = None,
) -> List[Dict]:
    """Agrupa frames consecutivos quase iguais.

    Cada frame é comparado com o frame de referência do grupo atual (não com o
    anterior), evitando que pequenas diferenças se acumulem. Quando o frame não
    é candidato (ex.: barra escura ausente), o grupo atual é encerrado, então
    prompts separados por gameplay não são fundidos por engano.

    `bubble_bboxes` ativa a comparação localizada da última bolha do usuário;
    entradas `None` caem no frame inteiro, de forma conservadora.
    """
    if fps <= 0:
        raise ValueError("fps deve ser > 0")
    if bubble_bboxes is not None and len(bubble_bboxes) != len(frames):
        raise ValueError("bubble_bboxes e frames com tamanhos diferentes")

    groups: List[Dict] = []
    current: Optional[Dict] = None
    reference: Optional[Image.Image] = None

    def close_current():
        nonlocal current, reference
        if current is not None:
            current["frame_count"] = len(current["frame_indices"])
            groups.append(current)
        current = None
        reference = None

    for index, frame_path in enumerate(frames):
        if is_candidate is not None and not is_candidate(frame_path):
            close_current()
            continue

        bbox = bubble_bboxes[index] if bubble_bboxes is not None else None
        thumb = (
            _bubble_comparison_image(frame_path, bbox)
            if bbox is not None
            else _thumbnail(frame_path)
        )
        if current is not None and reference is not None:
            mean, changed = _diff_metrics(reference, thumb)
            mean_limit = mean_threshold
            if bbox is not None and current.get("bubble_bbox") is not None:
                mean_limit = max(mean_limit, CHAT_COMPARE_MEAN_THRESHOLD)
            if mean <= mean_limit and changed <= changed_ratio:
                current["frame_indices"].append(index)
                current["last_frame"] = frame_path
                current["last_frame_index"] = index
                current["ts_end_seconds"] = frame_timestamp(index, fps, start_seconds)
                continue

        close_current()
        current = {
            "id": f"img_{len(groups) + 1:06d}",
            "ordinal": len(groups),
            "frame": frame_path,
            "frame_index": index,
            "ts_seconds": frame_timestamp(index, fps, start_seconds),
            "ts_end_seconds": frame_timestamp(index, fps, start_seconds),
            "last_frame": frame_path,
            "last_frame_index": index,
            "frame_indices": [index],
            "bubble_bbox": bbox,
        }
        reference = thumb

    close_current()
    return groups


def build_vision_message(ids: Sequence[str], chat_history: bool = False) -> str:
    """Mensagem do usuário: mapeia explicitamente posição da imagem -> ID."""
    if not ids:
        raise ValueError("lote de visão sem IDs")
    mapping = "\n".join(f"{i}. {gid}" for i, gid in enumerate(ids, 1))
    instruction = "Leia apenas o texto do usuário na caixa de digitação do rodapé."
    if chat_history:
        instruction = (
            "Em cada imagem, leia somente a última mensagem enviada pelo usuário: "
            "a bolha escura de mensagem do usuário mais abaixo, alinhada à direita. "
            "Ignore respostas do Claude, chamadas de ferramentas e a caixa Responder."
        )
    return (
        f"{instruction}\n\nImagens anexadas, na ordem:\n"
        f"{mapping}\n\n"
        "Retorne exatamente um objeto JSON por imagem, na mesma ordem e com os "
        "mesmos IDs. Responda apenas o JSON, sem texto extra."
    )


def build_opencode_command(
    model: str,
    image_paths: Sequence[str],
    message: str,
    opencode_bin: str = "opencode",
    agent: str = "vision-ocr",
) -> List[str]:
    """Monta `opencode run` com flags antes da mensagem posicional."""
    if not model or not str(model).strip():
        raise ValueError("modelo de visão vazio")
    if not image_paths:
        raise ValueError("nenhuma imagem no lote")
    cmd = [opencode_bin, "run"]
    if agent:
        cmd += ["--agent", agent]
    cmd += ["--model", str(model).strip(), "--format", "json", "--archived"]
    for image_path in image_paths:
        cmd += ["--file", str(pathlib.Path(image_path).resolve())]
    cmd += ["--", message]
    return cmd


def parse_opencode_jsonl(stdout: str) -> str:
    """Extrai o texto final dos eventos JSONL do `opencode run --format json`."""
    events: List[Dict] = []
    ignored: List[str] = []
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            ignored.append(line)
            continue
        if isinstance(event, dict):
            events.append(event)
        else:
            ignored.append(line)

    if not events:
        detail = f" (linhas não-JSON: {ignored[:2]!r})" if ignored else ""
        raise VisionError(f"opencode não retornou eventos JSON{detail}")

    for event in events:
        if event.get("type") == "error":
            error = event.get("error")
            if isinstance(error, dict):
                message = error.get("message") or json.dumps(error, ensure_ascii=False)
            else:
                message = str(error)
            raise VisionError(f"opencode reportou erro: {message}")
        if event.get("type") == "tool_use":
            part = event.get("part") or {}
            raise VisionError(
                "o modelo tentou usar ferramenta no modo visão "
                f"({part.get('tool', 'desconhecida')}); transcrição recusada"
            )

    texts: List[str] = []
    for event in events:
        if event.get("type") == "text":
            part = event.get("part") or {}
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text)
    if not texts:
        raise VisionError("opencode não retornou texto final")
    return "\n".join(texts)


def extract_json_array(text: str) -> List:
    """Encontra o primeiro array JSON válido, tolerando cercas markdown."""
    raw = text.strip()
    fenced = _FENCE_RE.match(raw)
    if fenced:
        raw = fenced.group(1).strip()
    try:
        data = json.loads(raw)
        if isinstance(data, list):
            return data
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    for index, char in enumerate(raw):
        if char != "[":
            continue
        try:
            data, _ = decoder.raw_decode(raw[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            return data
    raise VisionError(f"saída do modelo sem JSON array válido: {raw[:300]!r}")


def validate_batch_output(payload: List, expected_ids: Sequence[str]) -> List[Dict]:
    """Valida IDs/status/texto e devolve na ordem esperada."""
    expected = list(expected_ids)
    if not isinstance(payload, list):
        raise VisionError("saída do modelo não é uma lista JSON")
    if len(payload) != len(expected):
        raise VisionError(
            f"lote com {len(expected)} imagens retornou {len(payload)} objetos"
        )

    got: Dict[str, Dict] = {}
    for item in payload:
        if not isinstance(item, dict):
            raise VisionError("item da saída do modelo não é um objeto")
        image_id = item.get("id")
        if not isinstance(image_id, str) or not image_id.strip():
            raise VisionError("objeto sem id válido")
        image_id = image_id.strip()
        if image_id in got:
            raise VisionError(f"id duplicado na saída: {image_id}")
        if image_id not in expected:
            raise VisionError(f"id desconhecido na saída: {image_id}")

        status = item.get("status")
        if not isinstance(status, str):
            raise VisionError(f"id {image_id}: status ausente")
        status = status.strip().lower()
        if status not in VALID_STATUSES:
            raise VisionError(f"id {image_id}: status inválido {status!r}")

        text = item.get("text")
        if text is None:
            text = ""
        if not isinstance(text, str):
            raise VisionError(f"id {image_id}: texto não é string")
        text = text.strip()

        if status in ("empty", "illegible") and text:
            raise VisionError(f"id {image_id}: status {status} exige texto vazio")
        if status in ("ok", "partial") and not text:
            raise VisionError(f"id {image_id}: status {status} exige texto")
        got[image_id] = {"status": status, "text": text}

    missing = [gid for gid in expected if gid not in got]
    if missing:
        raise VisionError(f"ids ausentes na saída: {missing}")
    return [got[gid] for gid in expected]


def run_opencode_batch(
    ids: Sequence[str],
    image_paths: Sequence[str],
    model: str,
    opencode_bin: str = "opencode",
    agent: str = "vision-ocr",
    timeout: Optional[float] = 240.0,
    cwd: Optional[str] = None,
    chat_history: bool = False,
) -> List[Dict]:
    """Executa um lote no OpenCode; qualquer falha vira VisionError."""
    if len(ids) != len(image_paths):
        raise ValueError("IDs e imagens do lote com tamanhos diferentes")
    message = build_vision_message(ids, chat_history)
    cmd = build_opencode_command(model, image_paths, message, opencode_bin, agent)
    project_root = str(pathlib.Path(__file__).resolve().parents[1])
    log.info(
        "Visão/OpenCode: %d imagem(ns) modelo=%s lote=%s",
        len(image_paths),
        model,
        list(ids),
    )
    log.debug("comando: %s", cmd)
    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd or project_root,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as e:
        raise VisionError(f"binário do OpenCode não encontrado: {opencode_bin!r}") from e
    except subprocess.TimeoutExpired as e:
        raise VisionTimeout(f"opencode run excedeu {timeout}s no lote {list(ids)}") from e

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-1200:]
        raise VisionError(f"opencode run falhou (exit {proc.returncode}): {tail}")

    text = parse_opencode_jsonl(proc.stdout)
    payload = extract_json_array(text)
    return validate_batch_output(payload, ids)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().casefold()


def _chat_transcriptions_match(left: str, right: str) -> bool:
    """Tolera variações típicas de OCR de uma bolha já enviada."""
    def words(text):
        decomposed = unicodedata.normalize("NFD", _normalize_text(text))
        plain = "".join(char for char in decomposed if not unicodedata.combining(char))
        plain = re.sub(r"([a-z])\1{2,}", r"\1\1\1", plain)
        return re.sub(r"!+", "!", plain).split()

    def lost_exclamation(word, variant):
        if word.endswith("!"):
            return variant in (word[:-1] + "l", word[:-1] + "ll", word[:-1] + "lll")
        return len(word) == 2 and word.isalpha() and variant == word + "l"

    left_words, right_words = words(left), words(right)
    if len(left_words) != len(right_words):
        return False
    return all(
        a == b or lost_exclamation(a, b) or lost_exclamation(b, a)
        for a, b in zip(left_words, right_words)
    )


def consolidate_occurrences(
    groups: Sequence[Dict],
    transcriptions: Dict[str, Dict],
    model: str,
    fps: float,
    start_seconds: float,
    chat_history: bool = False,
) -> List[Dict]:
    """Consolida repetições consecutivas preservando a primeira ocorrência."""
    entries: List[Dict] = []
    previous_last_frame_index: Optional[int] = None

    for group in groups:
        group_id = group["id"]
        result = transcriptions.get(group_id)
        if result is None:
            raise VisionError(f"resultado ausente para o grupo {group_id}")

        entry = {
            "text": result["text"],
            "status": result["status"],
            "ts_seconds": group["ts_seconds"],
            "frame": group["frame"],
            "frame_index": group["frame_index"],
            "ts_end_seconds": group["ts_end_seconds"],
            "frame_end": group["last_frame"],
            "frame_count": group["frame_count"],
            "group_ids": [group_id],
            "model": model,
            "fps": fps,
            "start_seconds": start_seconds,
        }
        if group.get("vision_frame"):
            entry["crop_frame"] = group["vision_frame"]

        if (
            entries
            and previous_last_frame_index is not None
            and group["frame_index"] == previous_last_frame_index + 1
        ):
            previous = entries[-1]
            previous_text, current_text = previous["text"], entry["text"]
            same_text = (
                _chat_transcriptions_match(previous_text, current_text)
                if chat_history else _normalize_text(previous_text) == _normalize_text(current_text)
            )
            same = (
                previous["frame_index"] + previous["frame_count"] == entry["frame_index"]
                and previous["status"] == entry["status"]
                and same_text
            )
            if same:
                previous["ts_end_seconds"] = entry["ts_end_seconds"]
                previous["frame_end"] = entry["frame_end"]
                previous["frame_count"] += entry["frame_count"]
                previous["group_ids"].append(group_id)
                previous_last_frame_index = group["last_frame_index"]
                continue

        entries.append(entry)
        previous_last_frame_index = group["last_frame_index"]

    return entries


def transcribe_groups(
    groups: Sequence[Dict],
    model: str,
    batch_size: int = 4,
    opencode_bin: str = "opencode",
    agent: str = "vision-ocr",
    timeout: Optional[float] = 240.0,
    chat_history: bool = False,
    stats: Optional[Dict] = None,
) -> Dict[str, Dict]:
    if batch_size <= 0:
        raise ValueError("batch_size deve ser > 0")
    results: Dict[str, Dict] = {}
    total = len(groups)
    if stats is not None:
        stats.setdefault("calls", 0)
    def transcribe_batch(batch):
        ids = [group["id"] for group in batch]
        # No modo histórico envia o recorte localizado da bolha; o frame
        # inteiro fica só como fallback quando a localização falhou.
        images = [group.get("vision_frame") or group["frame"] for group in batch]
        if stats is not None:
            stats["calls"] += 1
        try:
            batch_results = run_opencode_batch(
                ids,
                images,
                model,
                opencode_bin=opencode_bin,
                agent=agent,
                timeout=timeout,
                chat_history=chat_history,
            )
        except VisionTimeout:
            if len(batch) == 1:
                raise
            midpoint = len(batch) // 2
            log.warning("Visão: dividindo lote de %d imagens após timeout", len(batch))
            transcribe_batch(batch[:midpoint])
            transcribe_batch(batch[midpoint:])
            return
        results.update(zip(ids, batch_results))

    for offset in range(0, total, batch_size):
        batch = groups[offset:offset + batch_size]
        log.info("Visão: lote %d–%d de %d", offset + 1, offset + len(batch), total)
        transcribe_batch(batch)
    return results


def write_json_atomic(out_path: str, data) -> None:
    """Grava JSON completo por replace atômico; não deixa arquivo parcial."""
    path = pathlib.Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        mask = os.umask(0)
        os.umask(mask)
        os.chmod(tmp_name, 0o666 & ~mask)
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def extract_vision_prompts(
    frames: Sequence[str],
    model: str,
    out_path: str = "data/prompts_vision.json",
    fps: float = 1.0,
    start_seconds: float = 0.0,
    batch_size: int = 4,
    opencode_bin: str = "opencode",
    agent: str = "vision-ocr",
    timeout: Optional[float] = 240.0,
    mean_threshold: float = DEFAULT_MEAN_THRESHOLD,
    changed_ratio: float = DEFAULT_CHANGED_RATIO,
    include_non_dark: bool = False,
    chat_history: bool = False,
) -> Dict:
    """Agrupa, transcreve por visão, consolida e grava o JSON atomicamente."""
    if not frames:
        raise VisionError("nenhum frame para o modo visão")
    if not model or not str(model).strip():
        raise VisionError("--vision-model é obrigatório no modo visão")
    if fps <= 0:
        raise VisionError("--fps deve ser > 0")
    if batch_size <= 0:
        raise VisionError("--vision-batch-size deve ser > 0")
    if mean_threshold < 0 or changed_ratio < 0:
        raise VisionError("limiares de comparação devem ser >= 0")

    candidate = None if include_non_dark else is_dark_bar
    group_fn = group_chat_frames if chat_history else group_frames
    groups = group_fn(
        frames,
        fps=fps,
        start_seconds=start_seconds,
        mean_threshold=mean_threshold,
        changed_ratio=changed_ratio,
        is_candidate=candidate,
    )
    candidates = groups
    log.info(
        "Visão: %d frame(s), %d grupo(s), %d candidato(s)",
        len(frames),
        len(groups),
        len(candidates),
    )

    stats: Dict = {"calls": 0}
    if not candidates:
        log.warning(
            "Visão: nenhum frame candidato (barra escura); será gravado JSON vazio. "
            "Use --vision-include-non-dark para analisar todos os grupos."
        )
        transcriptions: Dict[str, Dict] = {}
    else:
        transcriptions = transcribe_groups(
            candidates,
            model,
            batch_size=batch_size,
            opencode_bin=opencode_bin,
            agent=agent,
            timeout=timeout,
            chat_history=chat_history,
            stats=stats,
        )

    entries = consolidate_occurrences(
        candidates,
        transcriptions,
        model=model,
        fps=fps,
        start_seconds=start_seconds,
        chat_history=chat_history,
    )
    entries = [entry for entry in entries if entry["text"]]
    write_json_atomic(out_path, entries)
    return {
        "entries": entries,
        "groups": len(groups),
        "candidates": len(candidates),
        "calls": stats["calls"],
        "out": out_path,
    }
