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
                                "api_key": "",
                            }
                        ],
                    }
                )

                self.assertEqual(saved["model_profiles"][0]["api_key"], "stored-placeholder-value")
                self.assertEqual(saved["model_profiles"][0]["base_url"], "https://api.openai.com/v1")

                public = app.public_settings(saved)
                self.assertTrue(public["model_profiles"][0]["api_key_set"])
                self.assertNotIn("api_key", public["model_profiles"][0])

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


if __name__ == "__main__":
    unittest.main()
