"""Consolida o corpus de prompts espalhado em data/*-prompts.json + data/prompts_unique.json.

Lê todas as fontes, normaliza cada entrada para
{text, status, ts_seconds, source_live, source_file}, deduplica por texto
normalizado (lower, sem acento, sem pontuação extra) e mescla fragmentos
`partial` contíguos do mesmo texto (ex.: os 3 fragmentos de 0DlH_bDds0Q viram 1
com ts inicial 3646 e ts_end 3659). Filtra vazios, ordena por ts e gera:

- data/corpus.json   -> JSON normalizado (lista de entradas)
- web/corpus.js      -> window.ElGatoCorpus = [...] (para a UI em file://)

Sem dependências novas: apenas stdlib.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
WEB_DIR = ROOT / "web"

DEFAULT_STATUS = "ok"

# source_live fixo quando o nome do arquivo não carrega o id da live.
LIVE_OVERRIDES = {
    "prompts_unique.json": "wbSKZ3SIu9M",
}

# Arquivos explicitamente ignorados (vazios/placeholder).
IGNORED_FILES = {
    "dAonwLC8tnQ-prompts.json",
}


def normalize_key(text: str) -> str:
    """Chave de deduplicação: lower, sem acento, sem pontuação, espaços colapsados."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def ts_num(value) -> "int | float | None":
    """Converte timestamp para int quando inteiro, senão float; None se ausente."""
    if value is None:
        return None
    f = float(value)
    if f.is_integer():
        return int(f)
    return f


def source_live_for(filename: str) -> str:
    """Deriva o id da live a partir do nome do arquivo (-prompts.json)."""
    if filename in LIVE_OVERRIDES:
        return LIVE_OVERRIDES[filename]
    base = filename
    if base.endswith("-prompts.json"):
        base = base[: -len("-prompts.json")]
    # remove sufixo de seção tipo "-7200" (dAonwLC8tnQ-7200-prompts.json)
    base = re.sub(r"-\d+$", "", base)
    return base


def source_files() -> list[Path]:
    """Arquivos-fonte na ordem determinística: *-prompts.json + prompts_unique.json."""
    files = sorted(DATA_DIR.glob("*-prompts.json"))
    files = [f for f in files if f.name not in IGNORED_FILES]
    unique = DATA_DIR / "prompts_unique.json"
    if unique.exists():
        files.append(unique)
    return files


def load_entries(path: Path) -> list[dict]:
    """Lê um arquivo JSON (lista) e normaliza cada item."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(data, list):
        return []

    live = source_live_for(path.name)
    entries = []
    for item in data:
        if not isinstance(item, dict):
            continue
        text = (item.get("text") or item.get("prompt") or "").strip()
        if not text:
            continue  # filtra vazios
        ts = ts_num(item.get("ts_seconds"))
        ts_end = item.get("ts_end_seconds")
        entries.append(
            {
                "text": text,
                "status": item.get("status") or DEFAULT_STATUS,
                "ts_seconds": ts,
                "ts_end_seconds": ts_num(ts_end) if ts_end is not None else None,
                "source_live": live,
                "source_file": path.name,
                "_key": normalize_key(text),
            }
        )
    return entries


def _collapse(group: list[dict]) -> dict:
    """Funde um grupo de entradas equivalentes num único dict normalizado."""
    first = group[0]
    timestamps = [g["ts_seconds"] for g in group if g.get("ts_seconds") is not None]
    ends = [g["ts_end_seconds"] for g in group if g.get("ts_end_seconds") is not None]
    status = "ok" if any(g["status"] == "ok" for g in group) else first["status"]
    text = max((g["text"] for g in group), key=len)
    return {
        "text": text,
        "status": status,
        "ts_seconds": min(timestamps) if timestamps else None,
        "ts_end_seconds": max(ends) if ends else None,
        "source_live": first["source_live"],
        "source_file": first["source_file"],
        "_key": normalize_key(text),
    }


def dedupe(entries: list[dict]) -> list[dict]:
    """Deduplica por texto normalizado e mescla fragmentos `partial` sobrepostos."""
    # estágio 1: chave exata (lower/sem acento/sem pontuação)
    by_key: dict[tuple[str, str], list[dict]] = {}
    order: list[tuple[str, str]] = []
    for e in entries:
        key = (e["source_live"], e["_key"])
        if key not in by_key:
            by_key[key] = []
            order.append(key)
        by_key[key].append(e)

    merged = [_collapse(by_key[k]) for k in order]

    # estágio 2: mescla fragmentos `partial` da mesma live quando um é subtexto do outro
    nonpartial = [e for e in merged if e["status"] != "partial"]
    partials = [e for e in merged if e["status"] == "partial"]
    partials.sort(key=lambda e: len(e["_key"]), reverse=True)

    anchors: list[dict] = []
    for e in partials:
        placed = False
        for a in anchors:
            if a["source_live"] != e["source_live"]:
                continue
            if e["_key"] in a["_key"] or a["_key"] in e["_key"]:
                a.update(_collapse([a, e]))
                placed = True
                break
        if not placed:
            anchors.append(dict(e))

    return nonpartial + anchors


def _clean(entry: dict) -> dict:
    """Remove chaves internas e devolve o formato público ordenado."""
    out = {
        "text": entry["text"],
        "status": entry["status"],
        "ts_seconds": entry["ts_seconds"],
        "source_live": entry["source_live"],
        "source_file": entry["source_file"],
    }
    if entry.get("ts_end_seconds") is not None:
        out["ts_end_seconds"] = entry["ts_end_seconds"]
    return out


def build() -> list[dict]:
    """Consolida o corpus a partir dos arquivos-fonte e retorna as entradas."""
    all_entries: list[dict] = []
    for path in source_files():
        all_entries.extend(load_entries(path))

    deduped = dedupe(all_entries)
    deduped.sort(key=lambda e: (e["ts_seconds"] is None, e["ts_seconds"] or 0))

    entries = [_clean(e) for e in deduped]

    # corpus.json
    corpus_path = DATA_DIR / "corpus.json"
    corpus_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # corpus.js
    js = (
        "/* Gerado por src/build_corpus.py — não edite manualmente.\n"
        "   Regenerar: python -m src.build_corpus */\n"
        "window.ElGatoCorpus = " + json.dumps(entries, ensure_ascii=False, indent=2) + ";\n"
    )
    (WEB_DIR / "corpus.js").write_text(js, encoding="utf-8")

    return entries


def main() -> int:
    entries = build()
    per_file: dict[str, int] = {}
    for e in entries:
        per_file[e["source_file"]] = per_file.get(e["source_file"], 0) + 1
    print(f"Corpus consolidado: {len(entries)} prompts")
    for name, count in sorted(per_file.items()):
        print(f"  - {name}: {count}")
    print("Gerado data/corpus.json e web/corpus.js")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
