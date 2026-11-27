"""Testes focados do modo visão, sem serviços remotos."""
from __future__ import annotations

import json
import pathlib
import stat
import sys
import tempfile
import textwrap
import types
import unittest
from typing import Sequence
from unittest import mock

from PIL import Image, ImageDraw, ImageFont

from src import download, extract, pipeline, vision


def make_frame(path: pathlib.Path, text: str, block=None) -> str:
    image = Image.new("RGB", (640, 120), (20, 20, 20))
    draw = ImageDraw.Draw(image)
    draw.text((20, 45), text, fill=(240, 240, 240))
    if block is not None:
        x, y, w, h = block
        draw.rectangle((x, y, x + w, y + h), fill=(240, 240, 240))
    image.save(path, quality=95)
    return str(path)


def chat_font(size: int = 18):
    for name in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow antigo sem fonte escalável padrão
        return ImageFont.load_default()


def make_chat_frame(
    path: pathlib.Path,
    prompt: str,
    answer: Sequence[str],
    countdown: str = "6 s",
    *,
    bubble: tuple = (260, 420, 610, 470),
    include_bubble: bool = True,
) -> str:
    """Frame sintético do modo histórico: resposta/cursor fora da bolha."""
    width, height = 640, 600
    image = Image.new("RGB", (width, height), (24, 24, 24))
    draw = ImageDraw.Draw(image)
    font = chat_font(18)
    small = chat_font(14)

    if include_bubble:
        draw.rounded_rectangle((260, 20, 610, 70), radius=8, fill=(36, 36, 36))
        draw.text((275, 38), "prompt antigo no topo", font=font, fill=(235, 235, 235))
    for index, line in enumerate(answer):
        draw.text((40, 120 + index * 24), line, font=font, fill=(190, 190, 190))
    if include_bubble:
        x0, y0, x1, y1 = bubble
        draw.rounded_rectangle((x0, y0, x1, y1), radius=8, fill=(36, 36, 36))
        draw.text((x0 + 14, y0 + 15), prompt, font=font, fill=(238, 238, 238))
    draw.text(
        (40, 500),
        f"Usando claude free: Ver comando {countdown}",
        font=small,
        fill=(160, 160, 160),
    )
    draw.rounded_rectangle((20, 540, 620, 580), radius=12, fill=(48, 48, 48))
    draw.text((40, 552), "Responder", font=font, fill=(120, 120, 120))
    image.save(path, quality=95)
    return str(path)


def write_fake_opencode(path: pathlib.Path, *, fail: bool = False) -> str:
    script = textwrap.dedent(
        f"""\
        #!/usr/bin/env python3
        import json
        import pathlib
        import re
        import sys

        if {fail!r}:
            sys.stderr.write("falha simulada")
            raise SystemExit(3)

        message = sys.argv[-1]
        ids = re.findall(r"^\\d+\\. (img_\\d+)$", message, re.MULTILINE)
        payload = [
            {{"id": image_id, "status": "ok", "text": f"prompt {{index}}"}}
            for index, image_id in enumerate(ids, 1)
        ]
        print(json.dumps({{"type": "text", "part": {{"text": json.dumps(payload, ensure_ascii=False)}}}}))
        """
    )
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


class TimestampTests(unittest.TestCase):
    def test_fps_and_offset(self):
        self.assertEqual(vision.frame_timestamp(0, 1, 100), 100)
        self.assertEqual(vision.frame_timestamp(3, 0.5, 100), 106)
        self.assertEqual(vision.frame_timestamp(2, 2, 10), 11)
        self.assertEqual(vision.frame_timestamp(1, 3, 0), 0.333)


class GroupingTests(unittest.TestCase):
    def test_groups_identical_and_conservative_on_edit(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            a = make_frame(tmp / "a.jpg", "crie um jogo de gatos")
            a2 = make_frame(tmp / "a2.jpg", "crie um jogo de gatos")
            b = make_frame(tmp / "b.jpg", "agora faça um carro")
            a3 = make_frame(tmp / "a3.jpg", "crie um jogo de gatos")
            edited = make_frame(tmp / "edited.jpg", "crie um jogo de gatos", block=(500, 20, 24, 24))

            groups = vision.group_frames([a, a2, b, a3], fps=1, start_seconds=100)
            self.assertEqual(len(groups), 3)
            self.assertEqual(groups[0]["frame_count"], 2)
            self.assertEqual(groups[0]["ts_seconds"], 100)
            self.assertEqual(groups[1]["ts_seconds"], 102)
            self.assertEqual(groups[2]["ts_seconds"], 103)
            self.assertEqual(groups[0]["frame"], a)
            self.assertEqual(groups[0]["last_frame"], a2)

            edited_groups = vision.group_frames([a, edited], fps=1)
            self.assertEqual(len(edited_groups), 2, "edição curta não pode ser absorvida")

    def test_candidate_breaks_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            a = make_frame(tmp / "a.jpg", "prompt")
            b = make_frame(tmp / "b.jpg", "prompt")
            groups = vision.group_frames(
                [a, b], fps=1, is_candidate=lambda path: path.endswith("a.jpg")
            )
            self.assertEqual(len(groups), 1)

    def test_dark_bar(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            dark = make_frame(tmp / "dark.jpg", "texto")
            light = tmp / "light.jpg"
            Image.new("RGB", (640, 120), (250, 250, 250)).save(light)
            self.assertTrue(vision.is_dark_bar(dark))
            self.assertFalse(vision.is_dark_bar(str(light)))


class ChatHistoryBubbleTests(unittest.TestCase):
    def test_localizes_last_right_aligned_bubble_ignoring_answer(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            first = make_chat_frame(
                tmp / "a.jpg",
                "passe para o nivel 8",
                ["primeira resposta", "com duas linhas"],
                "6 s",
            )
            second = make_chat_frame(
                tmp / "b.jpg",
                "passe para o nivel 8",
                ["resposta totalmente diferente", "e com contador novo"],
                "1 min 5 s",
            )
            bbox_a = vision.locate_last_user_bubble(first)
            bbox_b = vision.locate_last_user_bubble(second)
            self.assertEqual(bbox_a, bbox_b)
            self.assertEqual(bbox_a, (260, 420, 611, 471))

    def test_no_confident_bubble_falls_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            frames = [
                make_chat_frame(
                    tmp / f"sem_bolha_{index}.jpg",
                    "",
                    [f"só resposta {index}, sem bolha do usuário"],
                    include_bubble=False,
                )
                for index in range(2)
            ]
            self.assertIsNone(vision.locate_last_user_bubble(frames[0]))
            groups = vision.group_chat_frames(
                frames, fps=1, crop_dir=str(tmp / "crops")
            )
            self.assertTrue(groups)
            self.assertTrue(all(group["bubble_bbox"] is None for group in groups))
            self.assertTrue(all("vision_frame" not in group for group in groups))

    def test_same_prompt_with_changing_answer_is_one_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            positions = [
                (260, 420, 610, 470),
                (260, 400, 610, 450),
                (260, 440, 610, 490),
                (260, 380, 610, 430),
            ]
            frames = [
                make_chat_frame(
                    tmp / f"frame_{index:06d}.jpg",
                    "passe para o nivel 8",
                    [f"resposta {index}", "linha extra que muda"],
                    f"{6 + index} s",
                    bubble=positions[index - 1],
                )
                for index in range(1, 5)
            ]
            groups = vision.group_chat_frames(
                frames, fps=1, start_seconds=100, crop_dir=str(tmp / "crops")
            )
            self.assertEqual(len(groups), 1)
            self.assertEqual(groups[0]["frame_count"], 4)
            self.assertEqual(groups[0]["ts_seconds"], 100)
            self.assertEqual(groups[0]["ts_end_seconds"], 103)
            self.assertTrue(groups[0]["vision_frame"].endswith("img_000001.jpg"))
            self.assertTrue(pathlib.Path(groups[0]["vision_frame"]).exists())

    def test_number_and_distinct_prompt_split_conservatively(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            same = make_chat_frame(
                tmp / "a.jpg", "passe para o nivel 8", ["resposta A"], "6 s"
            )
            same_answer_changed = make_chat_frame(
                tmp / "b.jpg",
                "passe para o nivel 8",
                ["resposta B bem diferente", "e mais longa"],
                "35 s",
            )
            number_changed = make_chat_frame(
                tmp / "c.jpg",
                "passe para o nivel 9",
                ["resposta B bem diferente", "e mais longa"],
                "36 s",
            )
            distinct = make_chat_frame(
                tmp / "d.jpg", "agora crie outro jogo", ["resposta C"], "37 s"
            )
            groups = vision.group_chat_frames(
                [same, same_answer_changed, number_changed, distinct],
                fps=1,
                start_seconds=50,
                crop_dir=str(tmp / "crops"),
            )
            self.assertEqual(
                [(group["frame_index"], group["last_frame_index"]) for group in groups],
                [(0, 1), (2, 2), (3, 3)],
            )


class OpenCodeParsingTests(unittest.TestCase):
    def test_timeout_splits_only_the_expired_batch(self):
        groups = [{"id": str(index), "frame": f"{index}.jpg"} for index in range(4)]
        attempted = []

        def transcribe(ids, *args, **kwargs):
            attempted.append(ids)
            if ids == ["2", "3"]:
                raise vision.VisionTimeout("tempo limite")
            return [{"status": "ok", "text": image_id} for image_id in ids]

        with mock.patch.object(vision, "run_opencode_batch", side_effect=transcribe):
            result = vision.transcribe_groups(groups, "fake/model", batch_size=2)

        self.assertEqual(list(result), ["0", "1", "2", "3"])
        self.assertEqual(attempted, [["0", "1"], ["2", "3"], ["2"], ["3"]])

    def test_parse_jsonl_text_and_diagnostics(self):
        payload = [{"id": "img_000001", "status": "ok", "text": "prompt"}]
        stdout = "\n".join([
            json.dumps({"type": "step_start", "part": {}}),
            "evento não-json ignorado",
            json.dumps({"type": "text", "part": {"text": json.dumps(payload)}}),
            json.dumps({"type": "step_finish", "part": {}}),
        ])
        self.assertEqual(json.loads(vision.parse_opencode_jsonl(stdout)), payload)

    def test_error_event_raises(self):
        stdout = json.dumps({
            "type": "error",
            "error": {"message": "sem saldo", "status": 402},
        })
        with self.assertRaisesRegex(vision.VisionError, "sem saldo"):
            vision.parse_opencode_jsonl(stdout)

    def test_tool_use_raises(self):
        stdout = json.dumps({"type": "tool_use", "part": {"tool": "bash"}})
        with self.assertRaisesRegex(vision.VisionError, "ferramenta"):
            vision.parse_opencode_jsonl(stdout)

    def test_extract_json_array_from_fence(self):
        fenced = "```json\n[{\"id\": \"x\", \"status\": \"empty\", \"text\": \"\"}]\n```"
        self.assertEqual(vision.extract_json_array(fenced)[0]["id"], "x")

    def test_command_order_and_files(self):
        cmd = vision.build_opencode_command(
            "provider/model", ["a.jpg", "b.jpg"], "mensagem", agent="vision-ocr"
        )
        self.assertEqual(cmd[0:2], ["opencode", "run"])
        self.assertLess(cmd.index("--model"), cmd.index("mensagem"))
        self.assertEqual(cmd[-1], "mensagem")
        self.assertEqual(cmd.count("--file"), 2)
        self.assertEqual(cmd[cmd.index("--file") + 1], str(pathlib.Path("a.jpg").resolve()))


class ValidationTests(unittest.TestCase):
    def test_valid_payload(self):
        out = vision.validate_batch_output(
            [
                {"id": "b", "status": "ok", "text": "dois"},
                {"id": "a", "status": "empty", "text": ""},
            ],
            ["a", "b"],
        )
        self.assertEqual([item["status"] for item in out], ["empty", "ok"])

    def test_missing_unknown_duplicate_and_inconsistent(self):
        with self.assertRaisesRegex(vision.VisionError, "retornou 1 objetos"):
            vision.validate_batch_output(
                [{"id": "a", "status": "ok", "text": "x"}], ["a", "b"]
            )
        with self.assertRaisesRegex(vision.VisionError, "desconhecido"):
            vision.validate_batch_output(
                [{"id": "x", "status": "ok", "text": "x"}], ["a"]
            )
        with self.assertRaisesRegex(vision.VisionError, "duplicado"):
            vision.validate_batch_output(
                [
                    {"id": "a", "status": "ok", "text": "x"},
                    {"id": "a", "status": "ok", "text": "x"},
                ],
                ["a", "a"],
            )
        with self.assertRaisesRegex(vision.VisionError, "exige texto vazio"):
            vision.validate_batch_output(
                [{"id": "a", "status": "empty", "text": "não"}], ["a"]
            )
        with self.assertRaisesRegex(vision.VisionError, "exige texto"):
            vision.validate_batch_output(
                [{"id": "a", "status": "ok", "text": ""}], ["a"]
            )


class ConsolidationTests(unittest.TestCase):
    def test_chat_merges_ocr_variations_but_preserves_numeric_edits(self):
        self.assertTrue(vision._chat_transcriptions_match(
            "Ajuste o pé! agoraaa!!!", "Ajuste o pêl agoraaaa!"
        ))
        self.assertTrue(vision._chat_transcriptions_match(
            "Ajuste o pé agoraaa!!!", "Ajuste o pêl agoraaalll"
        ))
        self.assertFalse(vision._chat_transcriptions_match(
            "Ajuste o pé para 8!", "Ajuste o pé para 9!"
        ))

    def test_merges_only_adjacent_ordinals(self):
        groups = [
            {"id": "img_000001", "ordinal": 0, "frame": "a", "frame_index": 0,
             "last_frame_index": 1, "ts_seconds": 10, "ts_end_seconds": 11,
             "last_frame": "a", "frame_count": 2},
            {"id": "img_000002", "ordinal": 1, "frame": "b", "frame_index": 2,
             "last_frame_index": 3, "ts_seconds": 12, "ts_end_seconds": 13,
             "last_frame": "b", "frame_count": 2},
            {"id": "img_000003", "ordinal": 2, "frame": "c", "frame_index": 5,
             "last_frame_index": 6, "ts_seconds": 14, "ts_end_seconds": 15,
             "last_frame": "c", "frame_count": 2},
        ]
        transcriptions = {
            group["id"]: {"status": "ok", "text": "mesmo prompt"} for group in groups
        }
        entries = vision.consolidate_occurrences(groups, transcriptions, "m", 1, 10)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["group_ids"], ["img_000001", "img_000002"])
        self.assertEqual(entries[0]["ts_end_seconds"], 13)
        self.assertEqual(entries[0]["frame_count"], 4)
        self.assertEqual(entries[1]["ts_seconds"], 14)

    def test_non_candidate_gap_prevents_merge(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            a1 = make_frame(tmp / "a1.jpg", "mesmo prompt")
            skipped = make_frame(tmp / "skipped.jpg", "gameplay")
            a2 = make_frame(tmp / "a2.jpg", "mesmo prompt")
            groups = vision.group_frames(
                [a1, skipped, a2],
                fps=1,
                start_seconds=10,
                is_candidate=lambda path: path.endswith(("a1.jpg", "a2.jpg")),
            )
            transcriptions = {
                group["id"]: {"status": "ok", "text": "mesmo prompt"} for group in groups
            }
            entries = vision.consolidate_occurrences(groups, transcriptions, "m", 1, 10)
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0]["ts_seconds"], 10)
            self.assertEqual(entries[1]["ts_seconds"], 12)


class ExtractFrameTests(unittest.TestCase):
    def test_start_seek_and_sample_ffmpeg_command(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            video = tmp / "video.mp4"
            video.write_bytes(b"fake")
            frames_dir = tmp / "frames"
            with mock.patch.object(extract.shutil, "which", return_value="/usr/bin/ffmpeg"), \
                 mock.patch.object(extract.subprocess, "run") as run:
                frames = extract.extract_frames(
                    str(video),
                    out_dir=str(frames_dir),
                    fps=0.5,
                    sample_seconds=30,
                    start_seconds=120,
                )
            self.assertEqual(frames, [])
            cmd = run.call_args.args[0]
            self.assertLess(cmd.index("-ss"), cmd.index("-i"))
            self.assertEqual(cmd[cmd.index("-ss") + 1], "120.000")
            self.assertGreater(cmd.index("-t"), cmd.index("-i"))
            self.assertEqual(cmd[cmd.index("-t") + 1], "30")
            vf = cmd[cmd.index("-vf") + 1]
            self.assertIn("fps=0.5", vf)
            self.assertIn("crop=w=iw:h=ih*0.120:x=0:y=ih*0.880", vf)

    def test_ocr_timestamp_uses_fps_and_offset_without_cv2(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            f1 = tmp / "frame_000001.jpg"
            f2 = tmp / "frame_000002.jpg"
            f1.write_bytes(b"a")
            f2.write_bytes(b"b")
            out = tmp / "prompts.json"
            with mock.patch.object(extract, "is_dark_bar", return_value=True):
                result = extract.extract_prompts(
                    frames=[str(f1), str(f2)],
                    out_path=str(out),
                    use_ocr=False,
                    fps=0.5,
                    start_seconds=100,
                )
            self.assertEqual([item["ts_seconds"] for item in result], [100, 102])


class DownloadSectionTests(unittest.TestCase):
    def test_section_options_and_path(self):
        captured = {}

        class FakeYDL:
            def __init__(self, opts):
                captured.update(opts)

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def extract_info(self, url, download=True):
                self.url = url
                self.download = download
                return {"id": "vid", "ext": "mp4"}

        fake_yt_dlp = types.ModuleType("yt_dlp")
        fake_yt_dlp.YoutubeDL = FakeYDL

        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            (tmp / "vid.section_12_72.mp4").write_bytes(b"video")
            with mock.patch.dict(sys.modules, {"yt_dlp": fake_yt_dlp}):
                result = download.download_section(
                    "https://example.invalid/live",
                    out_dir=str(tmp),
                    start_seconds=12,
                    duration=60,
                )
            self.assertTrue(result["video_path"].endswith("vid.section_12_72.mp4"))
            self.assertEqual(result["clip_start_seconds"], 12.0)
            ranges = captured["download_ranges"]({}, None)
            self.assertEqual(ranges, [{"start_time": 12.0, "end_time": 72.0}])
            self.assertTrue(captured["force_keyframes_at_cuts"])
            self.assertIn("section_12_72", captured["outtmpl"])


class PipelineCliTests(unittest.TestCase):
    def test_help_lists_vision_flags(self):
        import subprocess

        repo = pathlib.Path(__file__).resolve().parents[1]
        proc = subprocess.run(
            [sys.executable, "-m", "src.pipeline", "--help"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("--vision-model", proc.stdout)
        self.assertIn("--vision-batch-size", proc.stdout)
        self.assertIn("--start", proc.stdout)


class PipelineVisionWiringTests(unittest.TestCase):
    def test_pipeline_vision_path_with_fake_opencode(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            source = tmp / "source"
            source.mkdir()
            frames = [
                make_frame(source / "frame_000001.jpg", "primeiro prompt"),
                make_frame(source / "frame_000002.jpg", "primeiro prompt"),
                make_frame(source / "frame_000003.jpg", "segundo prompt"),
            ]
            fake = write_fake_opencode(tmp / "fake-opencode.py")
            frames_dir = tmp / "frames_vision"
            out = tmp / "prompts_vision.json"
            with mock.patch.object(extract, "extract_frames", return_value=frames):
                code = pipeline.main([
                    "--url", "local",
                    "--skip-download",
                    "--video", str(tmp / "dummy.mp4"),
                    "--vision-model", "fake/model",
                    "--opencode-bin", fake,
                    "--frames-dir", str(frames_dir),
                    "--out", str(out),
                    "--start", "10",
                    "--sample", "5",
                ])
            self.assertEqual(code, 0)
            entries = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0]["ts_seconds"], 10)
            self.assertEqual(entries[1]["ts_seconds"], 12)


class EndToEndFakeTests(unittest.TestCase):
    def test_vision_pipeline_with_fake_opencode(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            frames = [
                make_frame(tmp / "frame_000001.jpg", "primeiro prompt"),
                make_frame(tmp / "frame_000002.jpg", "primeiro prompt"),
                make_frame(tmp / "frame_000003.jpg", "segundo prompt"),
            ]
            fake = write_fake_opencode(tmp / "fake-opencode.py")
            out = tmp / "prompts_vision.json"
            summary = vision.extract_vision_prompts(
                frames,
                model="fake/model",
                out_path=str(out),
                fps=1,
                start_seconds=50,
                batch_size=2,
                opencode_bin=fake,
                agent="",
            )
            self.assertEqual(summary["groups"], 2)
            self.assertEqual(summary["candidates"], 2)
            entries = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[0]["text"], "prompt 1")
            self.assertEqual(entries[0]["ts_seconds"], 50)
            self.assertEqual(entries[0]["frame_count"], 2)
            self.assertEqual(entries[1]["text"], "prompt 2")
            self.assertEqual(entries[1]["ts_seconds"], 52)
            self.assertEqual(entries[1]["status"], "ok")
            self.assertEqual(entries[0]["model"], "fake/model")

    def test_chat_history_sends_localized_crop_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            frames = [
                make_chat_frame(
                    tmp / "a.jpg", "passe para o nivel 8", ["resposta A"], "6 s"
                ),
                make_chat_frame(
                    tmp / "b.jpg",
                    "passe para o nivel 8",
                    ["resposta totalmente diferente"],
                    "35 s",
                ),
                make_chat_frame(
                    tmp / "c.jpg", "passe para o nivel 9", ["resposta B"], "36 s"
                ),
                make_chat_frame(
                    tmp / "d.jpg", "agora crie outro jogo", ["resposta C"], "37 s"
                ),
            ]
            texts = {
                "img_000001": "passe para o nivel 8",
                "img_000002": "passe para o nivel 9",
                "img_000003": "agora crie outro jogo",
            }
            calls = []

            def fake_batch(ids, images, *args, **kwargs):
                calls.append((list(ids), list(images)))
                return [
                    {"status": "ok", "text": texts[image_id]} for image_id in ids
                ]

            out = tmp / "prompts_vision.json"
            with mock.patch.object(
                vision, "run_opencode_batch", side_effect=fake_batch
            ):
                summary = vision.extract_vision_prompts(
                    frames,
                    model="fake/model",
                    out_path=str(out),
                    fps=1,
                    start_seconds=200,
                    batch_size=4,
                    chat_history=True,
                )

            self.assertEqual(summary["groups"], 3)
            self.assertEqual(summary["calls"], 1)
            self.assertEqual(len(calls), 1)
            sent_ids, sent_images = calls[0]
            self.assertEqual(sent_ids, ["img_000001", "img_000002", "img_000003"])
            self.assertTrue(
                all(pathlib.Path(path).exists() for path in sent_images)
            )
            self.assertTrue(
                all(
                    pathlib.Path(path).parent.name == "_vision_bubbles"
                    for path in sent_images
                )
            )
            entries = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(len(entries), 3)
            self.assertEqual([entry["ts_seconds"] for entry in entries], [200, 202, 203])
            self.assertTrue(all(entry["crop_frame"] for entry in entries))

    def test_failure_does_not_overwrite_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            frames = [make_frame(tmp / "frame_000001.jpg", "prompt")]
            fake = write_fake_opencode(tmp / "fake-opencode.py", fail=True)
            out = tmp / "prompts_vision.json"
            out.write_text("JSON ANTIGO", encoding="utf-8")
            with self.assertRaises(vision.VisionError):
                vision.extract_vision_prompts(
                    frames,
                    model="fake/model",
                    out_path=str(out),
                    opencode_bin=fake,
                    agent="",
                )
            self.assertEqual(out.read_text(encoding="utf-8"), "JSON ANTIGO")


if __name__ == "__main__":
    unittest.main()
