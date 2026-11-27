"""CLI pipeline: download + extract -> data/prompts.json"""
from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import sys
import tempfile

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)


def main(argv=None):
    p = argparse.ArgumentParser(
        description="El Gato Prompts - extrai prompts do rodapé de live do YouTube",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--url", required=True, help="URL do YouTube (ex: https://www.youtube.com/live/wbSKZ3SIu9M)")
    p.add_argument("--out", default=None, help="saída JSON (padrão: data/prompts.json; modo visão: data/prompts_vision.json)")
    p.add_argument("--data-dir", default="data", help="diretório de dados")
    p.add_argument("--frames-dir", default=None, help="diretório de frames (padrão: data/frames; modo visão: data/frames_vision)")
    p.add_argument("--sample", type=int, default=None, help="segundos a processar a partir de --start (ex: 300 = 5min). No modo visão remoto baixa só a seção")
    p.add_argument("--start", type=float, default=0.0, help="offset inicial em segundos (seek no vídeo e base dos timestamps)")
    p.add_argument("--limit-frames", type=int, default=None, help="limita N frames")
    p.add_argument("--fps", type=float, default=1, help="fps de extração (0.2 = 1 a cada 5s, otimizado)")
    p.add_argument("--get-url", action="store_true", help="apenas resolve stream URL sem baixar (debug)")
    p.add_argument("--skip-download", action="store_true", help="pula download, usa vídeo local")
    p.add_argument("--video", default=None, help="path de vídeo local (se --skip-download)")
    p.add_argument("--no-ocr", action="store_true", help="pula OCR, só salva frames")

    vision = p.add_argument_group("modo visão (opt-in)")
    vision.add_argument("--vision-model", default=None, help="modelo de visão (ex: provider/model). Ativa o modo visão")
    vision.add_argument("--vision-batch-size", type=int, default=4, help="imagens por chamada ao OpenCode")
    vision.add_argument("--vision-mean-threshold", type=float, default=1.0, help="diferença média máxima para considerar frames iguais")
    vision.add_argument("--vision-changed-ratio", type=float, default=0.0005, help="fração máxima de pixels alterados para considerar frames iguais")
    vision.add_argument("--vision-timeout", type=float, default=240.0, help="timeout de cada chamada opencode run (segundos)")
    vision.add_argument("--vision-agent", default="vision-ocr", help="agente OpenCode sem ferramentas")
    vision.add_argument("--vision-include-non-dark", action="store_true", help="analisa também frames sem barra escura")
    vision.add_argument("--vision-chat-history", action="store_true", help="recorta a coluna central e lê a última mensagem do usuário no histórico")
    vision.add_argument("--opencode-bin", default=os.environ.get("OPENCODE_BIN", "opencode"), help="binário do OpenCode")

    args = p.parse_args(argv)

    if args.fps <= 0:
        log.error("--fps deve ser > 0")
        return 2
    if args.start < 0:
        log.error("--start deve ser >= 0")
        return 2
    if args.sample is not None and args.sample <= 0:
        log.error("--sample deve ser > 0")
        return 2
    if args.vision_model is not None:
        if not args.vision_model.strip():
            log.error("--vision-model não pode ser vazio")
            return 2
        if args.vision_batch_size <= 0:
            log.error("--vision-batch-size deve ser > 0")
            return 2
        if args.vision_timeout <= 0:
            log.error("--vision-timeout deve ser > 0")
            return 2
        if args.vision_mean_threshold < 0 or args.vision_changed_ratio < 0:
            log.error("limiares do modo visão devem ser >= 0")
            return 2

    use_vision = bool(args.vision_model)
    if args.vision_chat_history and not use_vision:
        p.error("--vision-chat-history requer --vision-model")
    out_path = args.out or ("data/prompts_vision.json" if use_vision else "data/prompts.json")
    frames_dir = args.frames_dir or ("data/frames_vision" if use_vision else "data/frames")

    video_path = args.video
    info = None
    clip_from_section = False

    if not args.skip_download:
        from .download import download, get_stream_url

        if args.get_url:
            url = get_stream_url(args.url)
            print(url)
            return 0

        if use_vision and args.sample is not None:
            from .download import download_section

            log.info(
                "Baixando seção para visão: start=%ss sample=%ss (sem baixar a live inteira)",
                args.start,
                args.sample,
            )
            try:
                res = download_section(
                    args.url,
                    out_dir=args.data_dir,
                    start_seconds=args.start,
                    duration=args.sample,
                )
            except Exception as e:  # noqa: BLE001 - falha explícita na CLI
                log.error("Download da seção falhou: %s", e)
                return 1
            clip_from_section = True
        else:
            if use_vision:
                log.warning(
                    "Modo visão sem --sample: baixando a live inteira. "
                    "Use --sample N (e opcionalmente --start S) para amostrar."
                )
            log.info("Baixando: %s", args.url)
            try:
                res = download(args.url, out_dir=args.data_dir)
            except Exception as e:  # noqa: BLE001
                log.error("Download falhou: %s", e)
                return 1

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

    # O arquivo baixado em seção já começa em --start; seek adicional seria duplo.
    seek_seconds = 0.0 if clip_from_section else args.start

    from .extract import extract_frames, extract_prompts

    log.info("Extraindo frames fps=%s start=%s sample=%s limit_frames=%s", args.fps, seek_seconds, args.sample, args.limit_frames)
    try:
        if use_vision:
            pathlib.Path(frames_dir).mkdir(parents=True, exist_ok=True)
            frames_dir = tempfile.mkdtemp(prefix="run-", dir=frames_dir)
        frames = extract_frames(
            video_path,
            out_dir=frames_dir,
            fps=args.fps,
            sample_seconds=args.sample,
            limit_frames=args.limit_frames,
            start_seconds=seek_seconds,
            chat_history=args.vision_chat_history,
        )
    except Exception as e:  # noqa: BLE001
        log.error("extract_frames falhou: %s", e)
        return 1
    log.info("Frames: %d", len(frames))

    if use_vision:
        from .vision import VisionError, extract_vision_prompts

        if args.no_ocr:
            log.warning("--no-ocr é ignorado no modo visão")
        log.info("Visão: agrupando e transcrevendo com %s...", args.vision_model)
        try:
            summary = extract_vision_prompts(
                frames=frames,
                model=args.vision_model,
                out_path=out_path,
                fps=args.fps,
                start_seconds=args.start,
                batch_size=args.vision_batch_size,
                opencode_bin=args.opencode_bin,
                agent=args.vision_agent,
                timeout=args.vision_timeout,
                mean_threshold=args.vision_mean_threshold,
                changed_ratio=args.vision_changed_ratio,
                include_non_dark=args.vision_include_non_dark,
                chat_history=args.vision_chat_history,
            )
        except VisionError as e:
            # Falha do modelo/comando/schema: não sobrescreve o JSON existente
            # nem grava um corpus vazio no lugar.
            log.error("Extração por visão falhou: %s", e)
            return 2
        except Exception as e:  # noqa: BLE001
            log.error("Extração por visão falhou: %s", e)
            return 2

        entries = summary["entries"]
        with_text = [entry for entry in entries if entry.get("text")]
        print(json.dumps({
            "video": video_path,
            "mode": "vision",
            "model": args.vision_model,
            "frames": len(frames),
            "groups": summary["groups"],
            "candidates": summary["candidates"],
            "calls": summary.get("calls", 0),
            "occurrences": len(entries),
            "with_text": len(with_text),
            "out": out_path,
        }, indent=2, ensure_ascii=False))
        return 0

    log.info("OCR / detecção de prompts...")
    results = extract_prompts(
        frames=frames,
        frames_dir=frames_dir,
        out_path=out_path,
        use_ocr=not args.no_ocr,
        fps=args.fps,
        start_seconds=args.start,
    )
    log.info("Done: %d prompts -> %s", len(results), out_path)

    # resumo
    with_candidates = [r for r in results if r.get("is_candidate")]
    with_text = [r for r in results if r.get("text")]
    print(json.dumps({
        "video": video_path,
        "frames": len(frames),
        "candidates": len(with_candidates),
        "with_text": len(with_text),
        "out": out_path,
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
