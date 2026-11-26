"""yt-dlp wrapper: URL -> mp4 + info.json"""
from __future__ import annotations
import json
import pathlib
import logging
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_FMT = "bv*[height<=720]+ba/b[height<=720] / bv*+ba/b"


def _ydl_opts(outtmpl: str = "data/%(id)s.%(ext)s", get_url: bool = False) -> dict:
    opts: dict = {
        "format": DEFAULT_FMT,
        "outtmpl": outtmpl,
        "writeinfojson": True,
        "writethumbnail": False,
        "noplaylist": True,
        "quiet": False,
        "no_warnings": True,
    }
    if get_url:
        opts["simulate"] = True
        opts["skip_download"] = True
    return opts


def get_info(url: str) -> dict:
    """Extrai info sem baixar (title, duration, id)."""
    import yt_dlp

    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "simulate": True, "skip_download": True}) as ydl:
        info = ydl.extract_info(url, download=False)
        # se for playlist/live wrapper, pega primeira entry
        if "entries" in info:
            info = next(iter(info["entries"]))
        return info


def get_duration(url: str) -> Optional[float]:
    try:
        return get_info(url).get("duration")
    except Exception as e:
        log.warning("get_duration failed: %s", e)
        return None


def get_title(url: str) -> Optional[str]:
    try:
        return get_info(url).get("title")
    except Exception as e:
        log.warning("get_title failed: %s", e)
        return None


def get_stream_url(url: str) -> Optional[str]:
    """Retorna URL direta do stream (para --get-url / streaming sem baixar)."""
    import yt_dlp

    with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "format": DEFAULT_FMT, "simulate": True, "skip_download": True}) as ydl:
        info = ydl.extract_info(url, download=False)
        if "entries" in info:
            info = next(iter(info["entries"]))
        # url direta
        direct = info.get("url")
        if direct:
            return direct
        # fallback: requested_formats
        fmts = info.get("requested_formats") or info.get("formats") or []
        for f in fmts:
            if f.get("url"):
                return f["url"]
        return None


def download(
    url: str,
    out_dir: str = "data",
    outtmpl: Optional[str] = None,
    get_url_only: bool = False,
) -> dict:
    """
    Baixa video -> out_dir. Retorna dict com {video_path, info_path, info}.
    Se get_url_only=True apenas resolve URL sem baixar.
    """
    import yt_dlp

    pathlib.Path(out_dir).mkdir(parents=True, exist_ok=True)
    if get_url_only:
        stream = get_stream_url(url)
        info = get_info(url)
        return {"stream_url": stream, "info": info, "video_path": None, "info_path": None}

    if outtmpl is None:
        outtmpl = str(pathlib.Path(out_dir) / "%(id)s.%(ext)s")

    opts = _ydl_opts(outtmpl=outtmpl)
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if "entries" in info:
            info = next(iter(info["entries"]))
        vid_id = info.get("id", "video")
        ext = info.get("ext", "mp4")
        # tenta achar arquivo baixado
        video_path = pathlib.Path(out_dir) / f"{vid_id}.{ext}"
        # yt-dlp pode salvar com outra ext; procura por id.*
        if not video_path.exists():
            candidates = list(pathlib.Path(out_dir).glob(f"{vid_id}.*"))
            # exclui json
            candidates = [c for c in candidates if c.suffix != ".json" and c.suffix != ".webp" and c.suffix != ".jpg"]
            if candidates:
                video_path = candidates[0]
            else:
                video_path = None  # type: ignore
        info_path = pathlib.Path(out_dir) / f"{vid_id}.info.json"
        if not info_path.exists():
            # fallback: escreve info manualmente
            info_path.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"video_path": str(video_path) if video_path else None, "info_path": str(info_path), "info": info}


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser(description="Download YouTube via yt-dlp")
    p.add_argument("url", help="YouTube URL")
    p.add_argument("--out-dir", default="data")
    p.add_argument("--get-url", action="store_true", help="apenas printa stream URL sem baixar")
    args = p.parse_args()
    res = download(args.url, out_dir=args.out_dir, get_url_only=args.get_url)
    print(json.dumps({k: v for k, v in res.items() if k != "info"}, indent=2, ensure_ascii=False))
