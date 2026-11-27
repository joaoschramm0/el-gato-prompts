"""ffmpeg extraction: 1fps, crop rodapé 0.82-0.94, upscale 2x, dark-bar + OCR fallback"""
from __future__ import annotations
import subprocess
import pathlib
import json
import logging
import shutil
import re
from typing import List, Dict, Optional

from PIL import Image

log = logging.getLogger(__name__)

# ROI vertical do rodapé (validado no sample_mid: prompt bar em 88-100%)
CROP_Y0 = 0.88
CROP_Y1 = 1.00  # altura 0.12 pega prompt + Opus 5.5 Extra
CROP_H = CROP_Y1 - CROP_Y0  # 0.12
FOOTER_MARKER = "opus"  # para detectar footer "Opus 5.5 Extra Ultracode..."


def _ffmpeg_exists() -> bool:
    return shutil.which("ffmpeg") is not None


def extract_frames(
    video_path: str,
    out_dir: str = "data/frames",
    fps: int = 1,
    sample_seconds: Optional[int] = None,
    limit_frames: Optional[int] = None,
    start_seconds: Optional[float] = None,
    chat_history: bool = False,
) -> List[str]:
    """Extrai frames via ffmpeg com crop+upscale. Retorna lista de paths.

    start_seconds faz seek no vídeo de entrada (offset real). Com fps=1 cada
    frame representa 1 segundo; o timestamp absoluto é start + índice/fps.
    """
    if not _ffmpeg_exists():
        raise RuntimeError("ffmpeg não encontrado no PATH")

    p = pathlib.Path(video_path)
    if not p.exists():
        raise FileNotFoundError(video_path)

    out = pathlib.Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    # limpa frames antigos? não - apenas sobrescreve
    # filter: fps=1, crop rodapé, upscale 2x neighbor (preserva pixel art / texto)
    crop = f"crop=w=iw:h=ih*{CROP_H:.3f}:x=0:y=ih*{CROP_Y0:.3f}"
    if chat_history:
        crop = "crop=w=iw*0.43:h=ih*0.92:x=iw*0.29:y=ih*0.03"
    vf = f"fps={fps},{crop},scale=2*iw:2*ih:flags=neighbor"

    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if start_seconds is not None and start_seconds > 0:
        # seek de entrada: o primeiro frame extraído corresponde a start_seconds
        cmd += ["-ss", f"{float(start_seconds):.3f}"]
    cmd += ["-i", str(p), "-vf", vf]
    if sample_seconds is not None:
        cmd += ["-t", str(sample_seconds)]
    if limit_frames is not None:
        cmd += ["-vframes", str(limit_frames)]
    # output pattern
    pattern = str(out / "frame_%06d.jpg")
    cmd += [pattern]

    log.info("ffmpeg: %s", " ".join(cmd))
    subprocess.run(cmd, check=True)
    frames = sorted(out.glob("frame_*.jpg"))
    return [str(f) for f in frames]


def is_dark_bar(image_path: str, dark_thresh: int = 60, dark_ratio: float = 0.45) -> bool:
    """Heurística: barra escura no rodapé - % de pixels escuros > dark_ratio."""
    import cv2  # import tardio: mantém o módulo testável sem OpenCV

    img = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
    if img is None:
        return False
    # considera escuro se < thresh
    dark = (img < dark_thresh).mean()
    return dark > dark_ratio


def _ocr_tesseract(image_path: str) -> str:
    try:
        import pytesseract  # type: ignore

        # psm 6 = bloco uniforme de texto
        text = pytesseract.image_to_string(Image.open(image_path), lang="por+eng", config="--psm 6")
        return text.strip()
    except Exception as e:
        log.debug("tesseract fail %s: %s", image_path, e)
        return ""


def _ocr_easyocr(image_path: str) -> str:
    try:
        import easyocr  # type: ignore

        # lazy singleton
        if not hasattr(_ocr_easyocr, "_reader"):
            _ocr_easyocr._reader = easyocr.Reader(["pt", "en"], gpu=False, verbose=False)  # type: ignore
        res = _ocr_easyocr._reader.readtext(image_path, detail=0, paragraph=True)
        return " ".join(res).strip()
    except Exception as e:
        log.debug("easyocr fail %s: %s", image_path, e)
        return ""


def ocr_image(image_path: str) -> str:
    """Tenta EasyOCR -> Tesseract -> vazio (degradação graciosa)."""
    # tenta easyocr se instalado
    text = ""
    try:
        import importlib.util

        if importlib.util.find_spec("easyocr") is not None:
            text = _ocr_easyocr(image_path)
            if text:
                return text
    except Exception:
        pass
    try:
        import importlib.util

        if importlib.util.find_spec("pytesseract") is not None:
            text = _ocr_tesseract(image_path)
            if text:
                return text
    except Exception:
        pass
    return ""


def extract_prompts(
    video_path: Optional[str] = None,
    frames: Optional[List[str]] = None,
    frames_dir: str = "data/frames",
    out_path: str = "data/prompts.json",
    use_ocr: bool = True,
    save_no_ocr: bool = True,
    fps: float = 1.0,
    start_seconds: float = 0.0,
) -> List[Dict]:
    """
    A partir de frames já extraídos (ou video_path), detecta candidatos via dark-bar
    e extrai texto via OCR. Se OCR não disponível, salva frames e loga.
    Retorna lista de {frame, text, is_dark_bar, ts}.

    O timestamp é derivado de start_seconds + índice/fps (fps=1 e start=0
    preservam o comportamento antigo: ts = índice).
    """
    if fps <= 0:
        raise ValueError("fps deve ser > 0")

    def timestamp(index: int):
        value = round(float(start_seconds) + index / float(fps), 3)
        return int(value) if float(value).is_integer() else value

    if frames is None:
        if video_path is None:
            # tenta usar frames já em disco
            frames = sorted([str(p) for p in pathlib.Path(frames_dir).glob("frame_*.jpg")])
            if not frames:
                raise ValueError("Nenhum frame encontrado; passe video_path ou gere frames antes")
        else:
            frames = extract_frames(video_path, out_dir=frames_dir, start_seconds=start_seconds)

    # checa OCR disponível
    ocr_available = False
    try:
        import importlib.util

        ocr_available = importlib.util.find_spec("easyocr") is not None or importlib.util.find_spec("pytesseract") is not None
    except Exception:
        pass

    if not ocr_available:
        log.warning("EasyOCR/Tesseract não instalado - salvando frames sem OCR. Instale: pip install easyocr  # ou pytesseract + tesseract-ocr")

    results: List[Dict] = []
    seen_texts = set()
    prev_hash = None
    import hashlib

    for idx, fp in enumerate(frames):
        # dedup por hash de imagem (pula OCR se frame idêntico ao anterior) - economiza ~60%
        try:
            with open(fp, "rb") as handle:
                h = hashlib.md5(handle.read()).hexdigest()
            if h == prev_hash:
                # duplica anterior sem OCR
                prev = results[-1] if results else None
                entry = {
                    "frame": fp,
                    "frame_index": int(idx),
                    "ts_seconds": timestamp(idx),
                    "is_dark_bar": bool(prev["is_dark_bar"]) if prev else False,
                    "is_candidate": bool(prev["is_candidate"]) if prev else False,
                    "text": str(prev["text"]) if prev else "",
                    "dedup_hash": True,
                }
                results.append(entry)
                continue
            prev_hash = h
        except Exception:
            pass
        dark = is_dark_bar(fp)
        text = ""
        if use_ocr and ocr_available:
            # só OCR se dark bar (prompt bar) - filtra jogo sem prompt
            if dark:
                text = ocr_image(fp)
                text = re.sub(r"\s+", " ", text).strip()
        is_candidate = dark or (len(text) > 12) or (FOOTER_MARKER in text.lower())
        # deduplica por texto (evita repetir mesmo prompt em N frames)
        dedup_key = text.lower()[:80] if text else None
        if dedup_key and dedup_key in seen_texts:
            # ainda registra mas marca duplicado
            pass
        if text:
            seen_texts.add(dedup_key)

        entry = {
            "frame": fp,
            "frame_index": int(idx),
            "ts_seconds": timestamp(idx),
            "is_dark_bar": bool(dark),
            "is_candidate": bool(is_candidate),
            "text": str(text),
        }
        # se OCR off mas dark bar, ainda salva como candidato sem texto
        if save_no_ocr and not ocr_available and dark:
            entry["is_candidate"] = True
        results.append(entry)

    # filtra apenas candidatos com texto ou dark bar se quiser, mas salva tudo por padrão
    # salva JSON
    pathlib.Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    # apenas candidatos com texto > 5 ou dark bar
    candidates = [r for r in results if r["is_candidate"] and (len(r["text"]) > 5 or r["is_dark_bar"])]
    # se nenhum candidato com texto, salva todos com aviso
    to_save = candidates if candidates else results
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(to_save, f, ensure_ascii=False, indent=2)
    log.info("Prompts extraídos: %d candidatos / %d frames -> %s", len(candidates), len(results), out_path)
    if not ocr_available:
        log.warning("OCR não disponível: frames em %s, instale easyocr/pytesseract para extrair texto", frames_dir)
    return to_save


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Extrai prompts do rodapé via ffmpeg+OCR")
    p.add_argument("--video", required=True, help="path do mp4")
    p.add_argument("--out", default="data/prompts.json")
    p.add_argument("--frames-dir", default="data/frames")
    p.add_argument("--sample", type=int, default=None, help="segundos para amostrar")
    p.add_argument("--limit-frames", type=int, default=None)
    p.add_argument("--start", type=float, default=0.0, help="offset inicial em segundos")
    p.add_argument("--fps", type=float, default=1.0, help="fps de extração")
    args = p.parse_args()
    frames = extract_frames(args.video, out_dir=args.frames_dir, sample_seconds=args.sample, limit_frames=args.limit_frames, start_seconds=args.start)
    res = extract_prompts(frames=frames, frames_dir=args.frames_dir, out_path=args.out, fps=args.fps, start_seconds=args.start)
    print(f"{len(res)} prompts -> {args.out}")
