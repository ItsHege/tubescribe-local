import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app
from transcriber import TranscriptionError


class AppHelperTests(unittest.TestCase):
    def test_settings_preserve_secret_and_public_settings_hide_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "local_settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "study_guide_provider": "api",
                        "study_guide_profile_id": "openai",
                        "model_profiles": [
                            {
                                "id": "openai",
                                "name": "OpenAI",
                        "kind": "openai_compatible",
                        "base_url": "https://api.openai.com/v1",
                        "model": "gpt-4.1-mini",
                        "study_guide_max_sources": 12,
                        "study_guide_input_chars": 130000,
                        "study_guide_output_tokens": 2000,
                        "api_key": "stored-placeholder-value",
                    }
                ],
                        "custom_topics": [],
                    }
                ),
                encoding="utf-8",
            )

            with patch.object(app, "LOCAL_SETTINGS_PATH", settings_path):
                saved = app.save_settings_from_payload(
                    {
                        "study_guide_provider": "api",
                        "study_guide_profile_id": "openai",
                        "model_profiles": [
                        {
                            "id": "openai",
                            "name": "OpenAI Updated",
                            "base_url": "https://api.openai.com/v1/",
                            "model": "gpt-4.1-mini",
                            "study_guide_max_sources": "12",
                            "study_guide_input_chars": "130000",
                            "study_guide_output_tokens": "2000",
                            "api_key": "",
                        }
                    ],
                    }
                )

                self.assertEqual(saved["model_profiles"][0]["api_key"], "stored-placeholder-value")
                self.assertEqual(saved["model_profiles"][0]["base_url"], "https://api.openai.com/v1")
                self.assertEqual(saved["model_profiles"][0]["study_guide_max_sources"], 12)
                self.assertEqual(saved["model_profiles"][0]["study_guide_input_chars"], 130000)
                self.assertEqual(saved["model_profiles"][0]["study_guide_output_tokens"], 2000)

                public = app.public_settings(saved)
                self.assertTrue(public["model_profiles"][0]["api_key_set"])
                self.assertNotIn("api_key", public["model_profiles"][0])
                self.assertEqual(public["model_profiles"][0]["study_guide_input_chars"], 130000)

    def test_settings_reject_invalid_base_url(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "local_settings.json"
            with patch.object(app, "LOCAL_SETTINGS_PATH", settings_path):
                with self.assertRaises(TranscriptionError) as raised:
                    app.save_settings_from_payload(
                        {
                            "model_profiles": [
                                {
                                    "id": "bad",
                                    "name": "Bad",
                                    "base_url": "localhost:11434/v1",
                                    "model": "local-model",
                                    "api_key": "",
                                }
                            ]
                        }
                    )
                self.assertEqual(raised.exception.code, "invalid_settings")

    def test_custom_topic_sanitization_and_canonical_topic_aliases(self):
        custom_topics = app.sanitize_custom_topics(
            [
                {
                    "topic": "Research / Papers",
                    "label": "Research Papers",
                    "tags": ["Research", "papers", "Research"],
                    "keywords": ["Paper Reading", "paper reading", "LLM + notes!"],
                    "source": "api-classification",
                    "created_at": "2026-05-30T00:00:00Z",
                },
                {"topic": "other"},
                "not-a-topic",
            ]
        )

        self.assertEqual(len(custom_topics), 1)
        self.assertEqual(custom_topics[0]["topic"], "research/papers")
        self.assertEqual(custom_topics[0]["tags"], ["research", "papers"])
        self.assertIn("llm + notes", custom_topics[0]["keywords"])

        agent_classification = app.normalize_topic_classification(
            {
                "topic": "agent",
                "label": "Agent",
                "tags": ["AI", "Agents"],
                "confidence": "0.93",
                "summary": "Useful agent workflow.",
                "keywords": ["AgentTools"],
            },
            custom_topics=[],
        )
        self.assertEqual(agent_classification["topic"], "ai/agents")
        self.assertEqual(agent_classification["confidence"], 0.93)

        cpp_classification = app.normalize_topic_classification(
            {"topic": "C++", "label": "C++", "tags": ["Programming"]},
            custom_topics=[],
        )
        self.assertEqual(cpp_classification["topic"], "programming/cpp")

    def test_resolve_output_file_stays_inside_outputs_and_text_extensions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "outputs"
            output_dir.mkdir()
            settings_path = Path(temp_dir) / "local_settings.json"
            settings_path.write_text(json.dumps({"output_dir": str(output_dir)}), encoding="utf-8")
            safe_file = output_dir / "ai" / "agents" / "note.md"
            safe_file.parent.mkdir(parents=True)
            safe_file.write_text("# Note", encoding="utf-8")

            with patch.object(app, "LOCAL_SETTINGS_PATH", settings_path):
                self.assertEqual(app.resolve_output_file("ai/agents/note.md"), safe_file.resolve())
                self.assertEqual(app.to_output_relative_path(safe_file), "ai/agents/note.md")

                with self.assertRaises(ValueError):
                    app.resolve_output_file("../secret.md")

                with self.assertRaises(ValueError):
                    app.resolve_output_file("ai/agents/note.exe")

    def test_rebuild_library_index_from_markdown_and_sidecars(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "outputs"
            source_dir = output_dir / "ai" / "agents"
            source_dir.mkdir(parents=True)
            markdown_path = source_dir / "agent-note_TEST_en_transcript.md"
            markdown_path.write_text(
                """---
schema_version: 2
title: "Agent Note"
channel: "Local Channel"
url: "https://example.test/watch?v=TEST"
video_id: "TEST"
language: "en"
source: "automatic_captions"
topic: "ai/agents"
topic_source: "auto"
created_at: "2026-05-31T10:00:00Z"
segments_count: 2
tags:
  - ai
  - agents
---

# Agent Note

## Summary

Agents can use tools and inspect results.

## Transcript

[00:00:01] Agent workflows combine planning and tool use.
""",
                encoding="utf-8",
            )
            for suffix in (".txt", ".srt", ".vtt"):
                markdown_path.with_suffix(suffix).write_text("sidecar", encoding="utf-8")
            markdown_path.with_suffix(".json").write_text(
                json.dumps({"schema_version": 2, "metadata": {"duration_seconds": 12}, "segments": []}),
                encoding="utf-8",
            )
            (output_dir / "README.md").write_text("# Not a transcript", encoding="utf-8")
            (output_dir / "library.json").write_text(
                json.dumps(
                    [
                        {
                            "title": "Stale",
                            "path": "missing/stale.md",
                            "created_at": "2026-05-30T00:00:00Z",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            settings_path = Path(temp_dir) / "local_settings.json"
            settings_path.write_text(json.dumps({"output_dir": str(output_dir)}), encoding="utf-8")

            with patch.object(app, "LOCAL_SETTINGS_PATH", settings_path):
                result = app.rebuild_library_index()
                library = json.loads((output_dir / "library.json").read_text(encoding="utf-8"))

        self.assertEqual(result["entries_count"], 1)
        self.assertEqual(result["removed_stale_count"], 1)
        self.assertEqual(result["skipped_count"], 1)
        self.assertEqual(library[0]["path"], "ai/agents/agent-note_TEST_en_transcript.md")
        self.assertEqual(library[0]["txt_path"], "ai/agents/agent-note_TEST_en_transcript.txt")
        self.assertEqual(library[0]["json_path"], "ai/agents/agent-note_TEST_en_transcript.json")
        self.assertEqual(library[0]["summary"], "Agents can use tools and inspect results.")
        self.assertEqual(library[0]["duration_seconds"], 12)

    def test_library_entries_drop_unsafe_source_urls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "outputs"
            source_dir = output_dir / "ai"
            source_dir.mkdir(parents=True)
            markdown_path = source_dir / "unsafe-url_TEST_en_transcript.md"
            markdown_path.write_text(
                """---
title: "Unsafe URL"
url: "javascript:alert(1)"
video_id: "TEST"
language: "en"
topic: "ai/agents"
---

# Unsafe URL

## Transcript

Caption text.
""",
                encoding="utf-8",
            )
            settings_path = Path(temp_dir) / "local_settings.json"
            settings_path.write_text(json.dumps({"output_dir": str(output_dir)}), encoding="utf-8")

            with patch.object(app, "LOCAL_SETTINGS_PATH", settings_path):
                app.rebuild_library_index()
                decorated = app.load_library_entries()

        self.assertEqual(decorated[0]["url"], "")

    def test_topic_classification_schema_validator_rejects_bad_json(self):
        valid = app.parse_topic_classification_content(
            json.dumps(
                {
                    "topic": "ai/agents",
                    "label": "AI / Agents",
                    "tags": ["ai", "agents"],
                    "confidence": 0.9,
                    "summary": "Agent workflow overview.",
                    "rationale": "The transcript discusses agents.",
                    "keywords": ["agents"],
                }
            )
        )
        self.assertEqual(valid["topic"], "ai/agents")

        with self.assertRaises(TranscriptionError) as raised:
            app.parse_topic_classification_content(
                json.dumps(
                    {
                        "topic": "ai/agents",
                        "label": "AI / Agents",
                        "tags": ["ai", "agents"],
                        "confidence": "high",
                        "summary": "Agent workflow overview.",
                        "rationale": "The transcript discusses agents.",
                        "keywords": ["agents"],
                    }
                )
            )

        self.assertEqual(raised.exception.code, "api_provider_invalid_response")

    def test_api_study_guide_payload_is_compact_for_local_models(self):
        source_docs = []
        for index in range(8):
            source_docs.append(
                {
                    "entry": {
                        "title": f"Long Source {index}",
                        "channel": "Local",
                        "url": "https://example.test/video",
                        "topic": "ai/agents",
                        "tags": ["ai", "agents", "local-model"],
                        "summary": "A concise source summary for the prompt.",
                    },
                    "text": "Agent workflows use tools, memory, and review loops. " * 300,
                }
            )

        payload = app.build_api_source_payload(source_docs)
        self.assertLess(len(payload), 30000)
        self.assertIn("Existing summary:", payload)
        self.assertIn("Transcript excerpt:", payload)
        self.assertIn("...", payload)

        code, message = app.classify_api_provider_http_error("Context size has been exceeded.")
        self.assertEqual(code, "api_provider_context_limit")
        self.assertIn("context window", message)

        self.assertEqual(
            app.api_study_guide_source_limit({"study_guide_max_sources": 3}, 8),
            3,
        )
        self.assertEqual(
            app.api_study_guide_source_limit({"study_guide_max_sources": 20}, 8),
            8,
        )

    def test_model_profile_connection_test_uses_saved_profile_without_transcript(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings_path = Path(temp_dir) / "local_settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "model_profiles": [
                            {
                                "id": "local-test",
                                "name": "Local Test",
                                "kind": "openai_compatible",
                                "base_url": "http://localhost:11434/v1",
                                "model": "test-model",
                                "api_key": "secret-key",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            captured = {}

            class FakeResponse:
                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def read(self):
                    return json.dumps(
                        {
                            "choices": [
                                {
                                    "message": {
                                        "content": '{"ok": true, "capability": "chat_completions"}'
                                    }
                                }
                            ]
                        }
                    ).encode("utf-8")

            def fake_urlopen(request, timeout=0):
                captured["url"] = request.full_url
                captured["timeout"] = timeout
                captured["body"] = request.data.decode("utf-8")
                captured["auth"] = request.headers.get("Authorization")
                return FakeResponse()

            with patch.object(app, "LOCAL_SETTINGS_PATH", settings_path), patch("app.urllib.request.urlopen", side_effect=fake_urlopen):
                result = app.test_model_profile("local-test")

        self.assertTrue(result["chat_completions"])
        self.assertTrue(result["json_response"])
        self.assertEqual(captured["url"], "http://localhost:11434/v1/chat/completions")
        self.assertEqual(captured["timeout"], 30)
        self.assertIn("Bearer secret-key", captured["auth"])
        self.assertNotIn("Transcript excerpt", captured["body"])


if __name__ == "__main__":
    unittest.main()
