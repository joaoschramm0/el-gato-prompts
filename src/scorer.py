"""Termômetro de similaridade: TF-IDF char_wb baseline, opcional sentence-transformers, opcional opencode-go API."""
from __future__ import annotations
import os
import logging
from typing import List, Dict, Optional

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

log = logging.getLogger(__name__)

# cache de modelo sentence-transformers
_ST_MODEL = None


def _embed_tfidf(texts: List[str]) -> np.ndarray:
    if not texts:
        return np.zeros((0, 0))
    # char_wb captura similaridade de prompts com typos/variações
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=1)
    mat = vec.fit_transform(texts)
    arr = mat.toarray().astype(float)
    # L2 normalize para cosine via dot
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1
    return arr / norms


def _embed_st(texts: List[str]) -> Optional[np.ndarray]:
    global _ST_MODEL
    try:
        import importlib.util

        if importlib.util.find_spec("sentence_transformers") is None:
            return None
        from sentence_transformers import SentenceTransformer

        if _ST_MODEL is None:
            # local padrão: Qwen 0.6B (melhor PT-BR, 600MB, 1024 dim) com fallback MiniLM 80MB
            local_model = os.environ.get("LOCAL_EMBED_MODEL", "Qwen/Qwen3-Embedding-0.6B")
            try:
                _ST_MODEL = SentenceTransformer(local_model, trust_remote_code=True)
            except Exception:
                _ST_MODEL = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
        embs = _ST_MODEL.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return np.array(embs, dtype=float)
    except Exception as e:
        log.debug("sentence-transformers embed failed: %s", e)
        return None


def _embed_opencode(texts: List[str]) -> Optional[np.ndarray]:
    url = os.environ.get("OPENCODE_API_URL", "").strip() or os.environ.get("OPENCODE_BASE_URL", "").strip()
    if not url:
        return None
# opencode Go: https://opencode.ai/go/inference/openai/v1/embeddings (Cloudflare exige User-Agent)
    url = url.rstrip("/")
    if "inference" not in url:
        use_go = os.environ.get("OPENCODE_USE_GO", "1") == "1"
        base = "/go/inference/openai" if use_go else "/inference/openai"
        url = url + base
    try:
        import urllib.request
        import json

        model = os.environ.get("OPENCODE_EMBED_MODEL", "qwen3-embedding-8b")
        api_key = os.environ.get("OPENCODE_API_KEY", "").strip() or os.environ.get("OPENAI_API_KEY", "").strip()
        payload = json.dumps({"model": model, "input": texts}).encode()
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        }
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        for suffix in ["/v1/embeddings", "/embeddings"]:
            endpoint = url.rstrip("/") + suffix
            try:
                req = urllib.request.Request(endpoint, data=payload, headers=headers)
                with urllib.request.urlopen(req, timeout=30) as r:
                    data = json.loads(r.read().decode())
                    if "data" in data:
                        vecs = [d["embedding"] for d in sorted(data["data"], key=lambda x: x.get("index", 0))]
                    elif "embeddings" in data:
                        vecs = data["embeddings"]
                    elif "embedding" in data:
                        vecs = [data["embedding"]]
                    else:
                        continue
                    arr = np.array(vecs, dtype=float)
                    norms = np.linalg.norm(arr, axis=1, keepdims=True)
                    norms[norms == 0] = 1
                    return arr / norms
            except Exception as e:
                log.debug("opencode %s failed: %s", endpoint, e)
                continue
        return None
    except Exception as e:
        log.debug("opencode embed error: %s", e)
        return None


def embed(texts: List[str]) -> np.ndarray:
    """
    Retorna matriz (n, dim) L2-normalizada.
    Prioridade: opencode-go API (se OPENCODE_API_URL) > sentence-transformers > TF-IDF.
    """
    if not texts:
        return np.zeros((0, 0))
    # limpa vazios
    cleaned = [t.strip() if t else "" for t in texts]

    # 1. opencode-go
    embs = _embed_opencode(cleaned)
    if embs is not None and embs.shape[0] == len(texts):
        log.info("embed: usando opencode-go (%s)", os.environ.get("OPENCODE_EMBED_MODEL", "qwen3-embedding-8b"))
        return embs
    # 2. sentence-transformers
    embs = _embed_st(cleaned)
    if embs is not None and embs.shape[0] == len(texts):
        log.info("embed: usando sentence-transformers/all-MiniLM-L6-v2")
        return embs
    # 3. TF-IDF baseline (sempre funciona)
    log.info("embed: usando TF-IDF char_wb baseline (free, sem API)")
    # TF-IDF precisa fit conjunto; para score() vamos fit em conjunto prompt+corpus
    # aqui embed isolado: fit só nos texts
    return _embed_tfidf(cleaned)


def score(prompt_user: str, corpus: List[str]) -> Dict:
    """
    Compara prompt_user contra corpus.
    Retorna {score 0-100, closest_prompt, closest_index, all_scores: [{prompt, score}]}
    Score = cosine * 100 (máx do corpus).
    Usa embedding conjunto para TF-IDF funcionar (fit em corpus+query).
    """
    if not corpus:
        return {"score": 0.0, "closest_prompt": None, "closest_index": None, "all_scores": []}
    if not prompt_user or not prompt_user.strip():
        return {"score": 0.0, "closest_prompt": None, "closest_index": None, "all_scores": []}

    # tenta embeddings avançados primeiro (opencode / ST) - independentes
    # se falhar cai para TF-IDF conjunto
    embs_op = _embed_opencode([prompt_user] + corpus)
    if embs_op is not None and embs_op.shape[0] == len(corpus) + 1:
        q = embs_op[0:1]
        c = embs_op[1:]
        sims = (q @ c.T)[0]  # já normalizado
    else:
        embs_st = _embed_st([prompt_user] + corpus)
        if embs_st is not None and embs_st.shape[0] == len(corpus) + 1:
            q = embs_st[0:1]
            c = embs_st[1:]
            sims = (q @ c.T)[0]
        else:
            # TF-IDF conjunto (fit em todos para vocabulário compartilhado)
            all_texts = [prompt_user] + corpus
            mat = _embed_tfidf(all_texts)
            q = mat[0:1]
            c = mat[1:]
            if q.shape[1] == 0:
                sims = np.zeros(len(corpus))
            else:
                sims = cosine_similarity(q, c)[0]
                # cosine_similarity já normalizado, mas TF-IDF mat já normalizada => ok
                # clip
                sims = np.clip(sims, -1, 1)

    all_scores = [{"prompt": corpus[i], "score": float(round(float(sims[i]) * 100, 2))} for i in range(len(corpus))]
    all_scores.sort(key=lambda x: x["score"], reverse=True)
    best = all_scores[0] if all_scores else None
    return {
        "score": best["score"] if best else 0.0,
        "closest_prompt": best["prompt"] if best else None,
        "closest_index": corpus.index(best["prompt"]) if best and best["prompt"] in corpus else None,
        "all_scores": all_scores,
    }


def score_batch(prompts: List[str], corpus: List[str]) -> List[Dict]:
    return [score(p, corpus) for p in prompts]


if __name__ == "__main__":
    import argparse
    import json

    p = argparse.ArgumentParser(description="Termômetro de similaridade")
    p.add_argument("--prompt", required=True, help="prompt do usuário")
    p.add_argument("--corpus", nargs="*", default=[], help="prompts do corpus (ou --corpus-file)")
    p.add_argument("--corpus-file", default=None, help="json com lista de prompts ou objetos {text}")
    args = p.parse_args()
    corpus = list(args.corpus)
    if args.corpus_file:
        data = json.loads(open(args.corpus_file, encoding="utf-8").read())
        if isinstance(data, list):
            for item in data:
                if isinstance(item, str):
                    corpus.append(item)
                elif isinstance(item, dict):
                    corpus.append(item.get("text") or item.get("prompt") or json.dumps(item, ensure_ascii=False))
        elif isinstance(data, dict) and "prompts" in data:
            corpus.extend(data["prompts"])
    res = score(args.prompt, corpus)
    print(json.dumps(res, ensure_ascii=False, indent=2))
