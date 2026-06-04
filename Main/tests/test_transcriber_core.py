import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import transcriber


class TranscriberCoreTests(unittest.TestCase):
    def make_info(self):
        return {
            "id": "abc123xyz00",
            "title": "AgentTools for C++ Workflows",
            "channel": "Engineering Notes",
            "webpage_url": "https://www.youtube.com/watch?v=abc123xyz00",
            "upload_date": "20260530",
            "duration": 120,
            "subtitles": {
                "en": [
                    {
                        "ext": "json3",
                        "url": "https://captions.example/manual-en.json3",
                        "name": "English",
                    }
                ]
            },
            "automatic_captions": {
                "en-orig": [
                    {
                        "ext": "json3",
                        "url": "https://captions.example/auto-en-orig.json3",
                        "name": "English original",
                    }
                ],
                "lt": [
                    {
                        "ext": "json3",
                        "url": "https://captions.example/auto-lt.json3",
                        "name": "Lithuanian",
                    }
                ],
            },
        }

    def make_caption_json(self):
        return {
            "events": [
                {"tStartMs": 0, "segs": [{"utf8": "Hello "}, {"utf8": " ,world"}]},
                {"tStartMs": 1000, "segs": [{"utf8": "Hello, world"}]},
                {"tStartMs": 7200, "segs": [{"utf8": "AgentTools improve workflows."}]},
                {"tStartMs": 9200, "segs": [{"utf8": "POMDP planning can guide agents."}]},
            ]
        }

    def test_choose_track_prefers_manual_and_allows_explicit_track_key(self):
        info = self.make_info()

        automatic_track_key = transcriber.build_track_key(
            "automatic_captions", "en-orig", "json3", 0
        )

        self.assertEqual(transcriber.choose_track(info).source, "subtitles")
        self.assertEqual(transcriber.choose_track(info).lang, "en")
        self.assertEqual(transcriber.choose_track(info, automatic_track_key).lang, "en-orig")

        with self.assertRaises(transcriber.TranscriptionError) as raised:
            transcriber.choose_track(info, "missing-track")
        self.assertEqual(raised.exception.code, "invalid_track")

    def test_extract_clean_and_filter_segments(self):
        extracted = transcriber.extract_segments(self.make_caption_json())

        self.assertEqual(
            extracted,
            [
                (0.0, "Hello, world"),
                (7.2, "AgentTools improve workflows."),
                (9.2, "POMDP planning can guide agents."),
            ],
        )

        cleaned = transcriber.clean_segments(
            [
                (0.0, "Hello ,world"),
                (5.0, "hello world!!!"),
                (7.0, "Hello world"),
                (9.0, "Next segment"),
            ]
        )
        self.assertEqual(cleaned, [(0.0, "Hello, world"), (7.0, "Hello world"), (9.0, "Next segment")])

        self.assertEqual(
            transcriber.filter_segments_by_time_range(extracted, start_seconds=7.0, end_seconds=8.0),
            [(7.2, "AgentTools improve workflows.")],
        )

    def test_time_range_validation_errors(self):
        with self.assertRaises(transcriber.TranscriptionError) as raised:
            transcriber.validate_time_range(20, 10)
        self.assertEqual(raised.exception.code, "invalid_time_range")

    def test_topic_classifier_uses_updated_ai_and_game_dev_keywords(self):
        ai_topic = transcriber.classify_topic(
            {"title": "AgentTools and POMDP planning", "channel": "DevTools"},
            [(0, "Autonomous agents use reflection and action policy loops.")],
        )
        self.assertEqual(ai_topic["topic"], "ai/agents")
        self.assertIn("agents", ai_topic["tags"])

        game_topic = transcriber.classify_topic(
            {"title": "I built a spell simulator", "channel": "Canvas Lab"},
            [(0, "Visual programming for game-development and simulation.")],
        )
        self.assertEqual(game_topic["topic"], "game-dev")

    def test_resolve_topic_accepts_custom_topics_and_rejects_invalid_override(self):
        custom_topics = [
            {
                "topic": "research/papers",
                "label": "Research Papers",
                "tags": ["research"],
                "keywords": ["paper reading"],
            }
        ]

        resolved = transcriber.resolve_topic(
            {"title": "Paper reading workflow", "channel": ""},
            [(0, "A research note")],
            custom_topics=custom_topics,
        )
        self.assertEqual(resolved["topic"], "research/papers")
        self.assertIn("papers", resolved["tags"])

        manual = transcriber.resolve_topic({}, [], topic_override="research/papers", custom_topics=custom_topics)
        self.assertEqual(manual["topic_source"], "manual")

        with self.assertRaises(transcriber.TranscriptionError) as raised:
            transcriber.resolve_topic({}, [], topic_override="unknown/topic", custom_topics=custom_topics)
        self.assertEqual(raised.exception.code, "invalid_topic")

    def test_transcribe_url_writes_markdown_first_exports_without_network(self):
        info = self.make_info()

        with tempfile.TemporaryDirectory() as temp_dir:
            with patch("transcriber.get_video_info", return_value=info), patch(
                "transcriber.fetch_json", return_value=self.make_caption_json()
            ):
                result = transcriber.transcribe_url(
                    "https://www.youtube.com/watch?v=abc123xyz00",
                    temp_dir,
                    include_timestamps=True,
                    include_metadata=True,
                    paragraph_mode=False,
                    generate_study_notes=True,
                )

            output_path = Path(result["output_path"])
            self.assertEqual(output_path.suffix, ".md")
            self.assertTrue(output_path.exists())
            self.assertTrue(Path(result["txt_output_path"]).exists())
            self.assertTrue(Path(result["json_output_path"]).exists())
            self.assertTrue(Path(result["srt_output_path"]).exists())
            self.assertTrue(Path(result["vtt_output_path"]).exists())

            markdown = output_path.read_text(encoding="utf-8")
            self.assertIn("---\n", markdown)
            self.assertIn('topic: "ai/agents"', markdown)
            self.assertIn("## Study Notes", markdown)
            self.assertIn("## Transcript", markdown)

            json_payload = json.loads(Path(result["json_output_path"]).read_text(encoding="utf-8"))
            self.assertEqual(json_payload["metadata"]["schema_version"], transcriber.LIBRARY_SCHEMA_VERSION)
            self.assertEqual(json_payload["metadata"]["topic"], "ai/agents")
            self.assertEqual(len(json_payload["segments"]), 3)

            self.assertIn("WEBVTT", Path(result["vtt_output_path"]).read_text(encoding="utf-8"))
            self.assertIn("-->", Path(result["srt_output_path"]).read_text(encoding="utf-8"))

            library_path = Path(result["library_index_path"])
            library = json.loads(library_path.read_text(encoding="utf-8"))
            self.assertEqual(len(library), 1)
            self.assertEqual(library[0]["path"], result["output_rel_path"])

    def test_transcribe_url_sanitizes_video_id_before_writing_outputs(self):
        info = self.make_info()
        info["id"] = r"a\..\..\escape"
        info["webpage_url"] = "https://www.youtube.com/watch?v=unsafe"

        with tempfile.TemporaryDirectory() as temp_dir:
            output_root = Path(temp_dir).resolve()
            with patch("transcriber.get_video_info", return_value=info), patch(
                "transcriber.fetch_json", return_value=self.make_caption_json()
            ):
                result = transcriber.transcribe_url(
                    "https://www.youtube.com/watch?v=unsafe",
                    output_root,
                )

            for key in ("output_path", "txt_output_path", "json_output_path", "srt_output_path", "vtt_output_path"):
                output_path = Path(result[key]).resolve()
                output_path.relative_to(output_root)
                self.assertTrue(output_path.exists())
                self.assertNotIn("..", output_path.name)
                self.assertNotIn("\\", output_path.name)

    def test_fetch_json_rejects_non_http_caption_urls(self):
        with self.assertRaises(transcriber.TranscriptionError) as raised:
            transcriber.fetch_json("file:///tmp/caption.json3")

        self.assertEqual(raised.exception.code, "invalid_caption_url")

    def test_fetch_json_rejects_oversized_caption_body(self):
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self, size=-1):
                return b"a" * (transcriber.MAX_CAPTION_JSON_BYTES + 1)

        with patch.object(transcriber, "MAX_CAPTION_JSON_BYTES", 8), patch(
            "transcriber.urllib.request.urlopen", return_value=FakeResponse()
        ):
            with self.assertRaises(transcriber.TranscriptionError) as raised:
                transcriber.fetch_json("https://captions.example/too-large.json3")

        self.assertEqual(raised.exception.code, "subtitle_too_large")

    def test_extract_segments_rejects_excessive_segment_count(self):
        caption_json = {
            "events": [
                {"tStartMs": index * 1000, "segs": [{"utf8": f"Segment {index}"}]}
                for index in range(3)
            ]
        }

        with patch.object(transcriber, "MAX_CAPTION_SEGMENTS", 2):
            with self.assertRaises(transcriber.TranscriptionError) as raised:
                transcriber.extract_segments(caption_json)

        self.assertEqual(raised.exception.code, "subtitle_too_large")


if __name__ == "__main__":
    unittest.main()
