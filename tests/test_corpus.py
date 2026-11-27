"""Testes do consolidador de corpus (src/build_corpus.py), sem serviços remotos."""
from __future__ import annotations

import unittest

from src import build_corpus


class NormalizeKeyTests(unittest.TestCase):
    def test_lower_strip_accents_and_punctuation(self):
        self.assertEqual(
            build_corpus.normalize_key("  Árvore, já consertou? "),
            "arvore ja consertou",
        )
        self.assertEqual(build_corpus.normalize_key("nítido"), "nitido")

    def test_collapses_whitespace(self):
        self.assertEqual(
            build_corpus.normalize_key("a\t  b\n\nc"),
            "a b c",
        )


class SourceLiveTests(unittest.TestCase):
    def test_strips_section_suffix(self):
        self.assertEqual(
            build_corpus.source_live_for("dAonwLC8tnQ-7200-prompts.json"),
            "dAonwLC8tnQ",
        )
        self.assertEqual(
            build_corpus.source_live_for("dAonwLC8tnQ-prompts.json"),
            "dAonwLC8tnQ",
        )
        self.assertEqual(
            build_corpus.source_live_for("0DlH_bDds0Q-prompts.json"),
            "0DlH_bDds0Q",
        )

    def test_unique_uses_override(self):
        self.assertEqual(
            build_corpus.source_live_for("prompts_unique.json"),
            "wbSKZ3SIu9M",
        )


class DedupeTests(unittest.TestCase):
    def test_merges_partial_fragments_into_one(self):
        entries = [
            {
                "text": "de tacar hate no jogo do El Gato, não está ficando ruim uma bosta",
                "status": "partial",
                "ts_seconds": 3646,
                "ts_end_seconds": 3647,
                "source_live": "0DlH_bDds0Q",
                "source_file": "0DlH_bDds0Q-prompts.json",
                "_key": build_corpus.normalize_key(
                    "de tacar hate no jogo do El Gato, não está ficando ruim uma bosta"
                ),
            },
            {
                "text": "n de tacar hate no jogo do El Gato, não está ficando ruim uma bosta",
                "status": "partial",
                "ts_seconds": 3648,
                "ts_end_seconds": 3648,
                "source_live": "0DlH_bDds0Q",
                "source_file": "0DlH_bDds0Q-prompts.json",
                "_key": build_corpus.normalize_key(
                    "n de tacar hate no jogo do El Gato, não está ficando ruim uma bosta"
                ),
            },
            {
                "text": "n de tacar hate no jogo do El Gato, não está ficando ruim uma bosta",
                "status": "partial",
                "ts_seconds": 3650,
                "ts_end_seconds": 3659,
                "source_live": "0DlH_bDds0Q",
                "source_file": "0DlH_bDds0Q-prompts.json",
                "_key": build_corpus.normalize_key(
                    "n de tacar hate no jogo do El Gato, não está ficando ruim uma bosta"
                ),
            },
        ]
        result = build_corpus.dedupe(entries)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["ts_seconds"], 3646)
        self.assertEqual(result[0]["ts_end_seconds"], 3659)
        self.assertEqual(result[0]["status"], "partial")

    def test_keeps_distinct_prompts_separate(self):
        entries = [
            {
                "text": "mostra a lista dos pacotes",
                "status": "ok",
                "ts_seconds": 14775,
                "ts_end_seconds": None,
                "source_live": "wbSKZ3SIu9M",
                "source_file": "prompts_unique.json",
                "_key": build_corpus.normalize_key("mostra a lista dos pacotes"),
            },
            {
                "text": "mostra a lista dos pacotes pra comprar",
                "status": "ok",
                "ts_seconds": 9150,
                "ts_end_seconds": None,
                "source_live": "wbSKZ3SIu9M",
                "source_file": "prompts_unique.json",
                "_key": build_corpus.normalize_key("mostra a lista dos pacotes pra comprar"),
            },
        ]
        result = build_corpus.dedupe(entries)
        self.assertEqual(len(result), 2)

    def test_exact_duplicate_collapses_keeping_earliest_ts(self):
        entries = [
            {
                "text": "nítido",
                "status": "ok",
                "ts_seconds": 11850,
                "ts_end_seconds": None,
                "source_live": "wbSKZ3SIu9M",
                "source_file": "prompts_unique.json",
                "_key": build_corpus.normalize_key("nítido"),
            },
            {
                "text": "nitido",
                "status": "ok",
                "ts_seconds": 14100,
                "ts_end_seconds": None,
                "source_live": "wbSKZ3SIu9M",
                "source_file": "prompts_unique.json",
                "_key": build_corpus.normalize_key("nitido"),
            },
        ]
        result = build_corpus.dedupe(entries)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["ts_seconds"], 11850)


if __name__ == "__main__":
    unittest.main()
