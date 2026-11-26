"""CLI pipeline: download + extract -> data/prompts.json"""
from __future__ import annotations
import argparse
import json
import logging
import pathlib
import sys

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


def main(argv=None):
    p = argparse.ArgumentParser(
        description="El Gato Prompts - extrai prompts do rodapé de live do YouTube",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--url", required=True, help="URL do YouTube (ex: https://www.youtube.com/live/wbSKZ3SIu9M)")
    p.add_argument("--out", default="data/prompts.json", help="saída JSON")
    p.add_argument("--data-dir", default="data", help="diretório de dados")
    p.add_argument("--frames-dir", default="data/frames", help="diretório de frames")
    p.add_argument("--sample", type=int, default=None, help="segundos do início para teste rápido (ex: 300 = 5min). Se omitido baixa/demuxa tudo")
    p.add_argument("--limit-frames", type=int, default=None, help="limita N frames (fps=1, então N segundos)")
    p.add_argument("--fps", type=float, default=1, help="fps de extração (0.2 = 1 a cada 5s, otimizado)")
    p.add_argument("--get-url", action="store_true", help="apenas resolve stream URL sem baixar (debug)")
    p.add_argument("--skip-download", action="store_true", help="pula download, usa vídeo local")
    p.add_argument("--video", default=None, help="path de vídeo local (se --skip-download)")
    p.add_argument("--no-ocr", action="store_true", help="pula OCR, só salva frames")
    args = p.parse_args(argv)

    # resolve limitFrames vs sample
    sample = args.sample
    limit_frames = args.limit_frames
    # se --sample dado sem limit, fps=1 => limit implícito = sample
    # mas deixamos ffmpeg -t sample cuidar; limit_frames é extra cap
    if sample is not None and limit_frames is None:
        # não precisa limitar por frames; ffmpeg -t já limita
        pass

    video_path = args.video
    info = None

    if not args.skip_download:
        from .download import download, get_stream_url

        if args.get_url:
            url = get_stream_url(args.url)
            print(url)
            return 0
        log.info("Baixando: %s", args.url)
        res = download(args.url, out_dir=args.data_dir)
        video_path = res.get("video_path")
        info = res.get("info")
        if not video_path:
            log.error("Download falhou ou vídeo não encontrado")
            return 1
        log.info("Vídeo: %s", video_path)
    else:
        if not video_path:
            # tenta achar qualquer mp4 em data_dir
            cands = list(pathlib.Path(args.data_dir).glob("*.mp4")) + list(pathlib.Path(args.data_dir).glob("*.mkv")) + list(pathlib.Path(args.data_dir).glob("*.webm"))
            if cands:
                video_path = str(cands[0])
            else:
                log.error("--skip-download requer --video ou arquivo em %s", args.data_dir)
                return 1
        log.info("Usando vídeo local: %s", video_path)

    from .extract import extract_frames, extract_prompts

    log.info("Extraindo frames fps=%d sample=%s limit_frames=%s", args.fps, sample, limit_frames)
    try:
        frames = extract_frames(video_path, out_dir=args.frames_dir, fps=args.fps, sample_seconds=sample, limit_frames=limit_frames)
    except Exception as e:
        log.error("extract_frames falhou: %s", e)
        return 1
    log.info("Frames: %d", len(frames))

    log.info("OCR / detecção de prompts...")
    results = extract_prompts(frames=frames, frames_dir=args.frames_dir, out_path=args.out, use_ocr=not args.no_ocr)
    log.info("Done: %d prompts -> %s", len(results), args.out)

    # resumo
    with_candidates = [r for r in results if r.get("is_candidate")]
    with_text = [r for r in results if r.get("text")]
    print(json.dumps({"video": video_path, "frames": len(frames), "candidates": len(with_candidates), "with_text": len(with_text), "out": args.out}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
