import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
import time
import zipfile
import io
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import app


class QuietAppHandler(app.AppHandler):
    def log_message(self, format, *args):
        return


@contextmanager
def run_test_server():
    server = app.ReusableThreadingHTTPServer(("127.0.0.1", 0), QuietAppHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def request_json(base_url: str, path: str, payload=None):
    url = base_url + path
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


def request_headers(base_url: str, path: str, headers=None, method=None):
    request = urllib.request.Request(base_url + path, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers


def request_text(base_url: str, path: str):
    try:
        with urllib.request.urlopen(base_url + path, timeout=10) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def request_raw(base_url: str, path: str, body: bytes, headers=None):
    request = urllib.request.Request(base_url + path, data=body, headers=headers or {}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


class AppHttpTests(unittest.TestCase):
    def test_primary_ui_and_v2_alias(self):
        with run_test_server() as base_url:
            status, text = request_text(base_url, "/")
            self.assertEqual(status, 200)
            self.assertIn("TubeScribe Local", text)
            self.assertIn('/static/app.css', text)
            self.assertIn('/static/app.js', text)
            self.assertIn("Command palette", text)
            self.assertNotIn("Try UI v2", text)
            self.assertNotIn("/v1", text)
            self.assertNotIn("fonts.googleapis.com", text)

            status, text = request_text(base_url, "/v2")
            self.assertEqual(status, 200)
            self.assertIn("TubeScribe Local", text)
            self.assertIn('/static/app.css', text)
            self.assertIn('/static/app.js', text)

            status, text = request_text(base_url, "/v2/")
            self.assertEqual(status, 200)
            self.assertIn('/static/app.css', text)
            self.assertIn('/static/app.js', text)

            status, _ = request_text(base_url, "/v1")
            self.assertEqual(status, 404)

    def test_unknown_static_fallback_does_not_expose_runtime_json(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            (base_dir / "index.html").write_text("TubeScribe Local", encoding="utf-8")
            (base_dir / "local_settings.json").write_text(
                json.dumps({"model_profiles": [{"api_key": "sentinel-secret"}]}),
                encoding="utf-8",
            )
            (base_dir / "batch_jobs.json").write_text(
                json.dumps({"job": "sentinel-batch"}),
                encoding="utf-8",
            )

            with patch.object(app, "BASE_DIR", base_dir):
                with run_test_server() as base_url:
                    status, text = request_text(base_url, "/local_settings.json")
                    self.assertEqual(status, 404)
                    self.assertNotIn("sentinel-secret", text)

                    status, text = request_text(base_url, "/batch_jobs.json")
                    self.assertEqual(status, 404)
                    self.assertNotIn("sentinel-batch", text)

    def test_cors_allows_only_matching_local_origin(self):
        with run_test_server() as base_url:
            parsed = urllib.parse.urlparse(base_url)
            allowed_origin = f"http://localhost:{parsed.port}"

            status, headers = request_headers(
                base_url,
                "/api/health",
                headers={"Origin": allowed_origin},
            )
            self.assertEqual(status, 200)
            self.assertEqual(headers.get("Access-Control-Allow-Origin"), allowed_origin)

            status, headers = request_headers(
                base_url,
                "/api/health",
                headers={"Origin": "https://example.com"},
            )
            self.assertEqual(status, 200)
            self.assertIsNone(headers.get("Access-Control-Allow-Origin"))

            status, headers = request_headers(
                base_url,
                "/api/health",
                headers={"Origin": "https://example.com"},
                method="OPTIONS",
            )
            self.assertEqual(status, 403)
            self.assertIsNone(headers.get("Access-Control-Allow-Origin"))

    def test_cross_origin_simple_post_is_rejected_before_settings_mutation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "outputs"
            output_dir.mkdir()
            settings_path = Path(temp_dir) / "local_settings.json"
            settings_path.write_text(
                json.dumps({"output_dir": str(output_dir), "batch_limit": 10}),
                encoding="utf-8",
            )
            body = json.dumps({"batch_limit": 7}).encode("utf-8")

            with patch.object(app, "LOCAL_SETTINGS_PATH", settings_path):
                with run_test_server() as base_url:
                    status, _ = request_raw(
                        base_url,
                        "/api/settings",
                        body,
                        headers={
                            "Origin": "https://example.com",
                            "Content-Type": "text/plain",
                        },
                    )

            self.assertEqual(status, 403)
            saved = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["batch_limit"], 10)

    def test_json_body_size_limit_returns_413(self):
        with patch.object(app, "MAX_JSON_BODY_BYTES", 16):
            with run_test_server() as base_url:
                status, text = request_raw(
                    base_url,
                    "/api/session/open",
                    b'{"client_id":"' + (b"a" * 64) + b'"}',
                    headers={"Content-Type": "application/json"},
                )

        self.assertEqual(status, 413)
        self.assertIn("request_too_large", text)

    def test_health_topics_and_validation_errors(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "outputs"
            output_dir.mkdir()
            settings_path = Path(temp_dir) / "local_settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "output_dir": str(output_dir),
                        "custom_topics": [
                            {
                                "topic": "research/papers",
                                "label": "Research Papers",
                                "tags": ["research"],
                                "keywords": ["paper reading"],
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            batch_state_path = Path(temp_dir) / "batch_jobs.json"
            with patch.object(app, "OUTPUT_DIR", output_dir), patch.object(app, "LOCAL_SETTINGS_PATH", settings_path), patch.object(app, "BATCH_STATE_PATH", batch_state_path):
                with run_test_server() as base_url:
                    status, payload = request_json(base_url, "/api/health")
                    self.assertEqual(status, 200)
                    self.assertTrue(payload["ok"])

                    status, payload = request_json(base_url, "/api/topics")
                    self.assertEqual(status, 200)
                    self.assertIn(
                        {"value": "research/papers", "label": "Research Papers"},
                        payload["topics"],
                    )

                    status, payload = request_json(base_url, "/api/tracks", {})
                    self.assertEqual(status, 400)
                    self.assertEqual(payload["error_code"], "missing_url")

                    status, payload = request_json(base_url, "/api/transcribe", {})
                    self.assertEqual(status, 400)
                    self.assertEqual(payload["error_code"], "missing_url")

                    status, payload = request_json(
                        base_url,
                        "/api/transcribe",
                        {"url": "https://youtu.be/example", "start_seconds": "not-a-number"},
                    )
                    self.assertEqual(status, 400)
                    self.assertEqual(payload["error_code"], "invalid_time_range")

                    status, payload = request_json(
                        base_url,
                        "/api/library/file?path=" + urllib.parse.quote("../secret.md"),
                    )
                    self.assertEqual(status, 400)
                    self.assertEqual(payload["error_code"], "invalid_path")

    def test_diagnostics_endpoint_reports_yt_dlp_status(self):
        diagnostics = {
            "yt_dlp": {
                "status": "ok",
                "available": True,
                "module_version": "2026.01.01",
                "cli_available": True,
                "cli_path": "/usr/bin/yt-dlp",
                "cli_version": "2026.01.01",
                "cli_error": "",
                "message": "yt-dlp is available for local caption extraction.",
                "hints": ["Keep yt-dlp updated."],
            }
        }
        with patch.object(app, "get_diagnostics", return_value=diagnostics):
            with run_test_server() as base_url:
                status, payload = request_json(base_url, "/api/diagnostics")

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["diagnostics"]["yt_dlp"]["status"], "ok")
        self.assertEqual(payload["diagnostics"]["yt_dlp"]["module_version"], "2026.01.01")

    def test_model_profile_test_endpoint_reports_capabilities(self):
        result = {
            "profile_id": "local-test",
            "profile_name": "Local Test",
            "base_url": "http://localhost:11434/v1",
            "model": "test-model",
            "chat_completions": True,
            "json_response": True,
            "structured_output": "not_checked",
            "message": "Connection test passed.",
        }
        with patch.object(app, "test_model_profile", return_value=result):
            with run_test_server() as base_url:
                status, payload = request_json(base_url, "/api/settings/test-model", {"profile_id": "local-test"})

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["result"]["chat_completions"])
        self.assertTrue(payload["result"]["json_response"])

    def test_library_file_and_local_study_guide_endpoint(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "outputs"
            source_dir = output_dir / "ai" / "agents"
            source_dir.mkdir(parents=True)
            markdown_path = source_dir / "agent-note.md"
            markdown_path.write_text(
                """---
title: "Agent Note"
topic: "ai/agents"
tags:
  - ai
---

# Agent Note

## Summary

Agents can call tools, inspect results and iterate on tasks.

## Transcript

[00:00:01] Agent workflows combine planning, tool use and review.
""",
                encoding="utf-8",
            )
            library_path = output_dir / "library.json"
            library_path.write_text(
                json.dumps(
                    [
                        {
                            "title": "Agent Note",
                            "channel": "Local",
                            "url": "https://example.test/video",
                            "path": "ai/agents/agent-note.md",
                            "topic": "ai/agents",
                            "tags": ["ai", "agents"],
                            "language": "en",
                            "summary": "Agents can call tools.",
                            "created_at": "2026-05-30T00:00:00Z",
                        }
                    ]
                ),
                encoding="utf-8",
            )
            settings_path = Path(temp_dir) / "local_settings.json"
            settings_path.write_text(
                json.dumps({"study_guide_provider": "local", "output_dir": str(output_dir)}),
                encoding="utf-8",
            )

            batch_state_path = Path(temp_dir) / "batch_jobs.json"
            with patch.object(app, "OUTPUT_DIR", output_dir), patch.object(app, "LOCAL_SETTINGS_PATH", settings_path), patch.object(app, "BATCH_STATE_PATH", batch_state_path):
                with run_test_server() as base_url:
                    status, payload = request_json(
                        base_url,
                        "/api/library/file?path=" + urllib.parse.quote("ai/agents/agent-note.md"),
                    )
                    self.assertEqual(status, 200)
                    self.assertTrue(payload["ok"])
                    self.assertIn("Agent workflows", payload["text"])

                    status, payload = request_json(
                        base_url,
                        "/api/library/study-guide",
                        {"topics": ["ai/agents"], "provider": "local", "max_sources": 3},
                    )
                    self.assertEqual(status, 200)
                    self.assertTrue(payload["ok"])
                    result = payload["result"]
                    self.assertEqual(result["provider"], "local")
                    self.assertEqual(result["sources_count"], 1)
                    self.assertIn("Agent Note", result["guide_text"])

    def test_library_rebuild_endpoint_repairs_index(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "outputs"
            source_dir = output_dir / "programming" / "javascript"
            source_dir.mkdir(parents=True)
            markdown_path = source_dir / "javascript-note_TEST_en_transcript.md"
            markdown_path.write_text(
                """---
title: "JavaScript Note"
url: "https://example.test/watch?v=TEST"
video_id: "TEST"
language: "en"
topic: "programming/javascript"
tags:
  - javascript
---

# JavaScript Note

## Transcript

[00:00:01] JavaScript can run in the browser and in Node.js.
""",
                encoding="utf-8",
            )
            (output_dir / "library.json").write_text(
                json.dumps([{"title": "Missing", "path": "missing.md"}]),
                encoding="utf-8",
            )
            settings_path = Path(temp_dir) / "local_settings.json"
            settings_path.write_text(json.dumps({"output_dir": str(output_dir)}), encoding="utf-8")
            batch_state_path = Path(temp_dir) / "batch_jobs.json"

            with patch.object(app, "OUTPUT_DIR", output_dir), patch.object(app, "LOCAL_SETTINGS_PATH", settings_path), patch.object(app, "BATCH_STATE_PATH", batch_state_path):
                with run_test_server() as base_url:
                    status, payload = request_json(base_url, "/api/library/rebuild", {})

                    self.assertEqual(status, 200)
                    self.assertTrue(payload["ok"])
                    result = payload["result"]
                    self.assertEqual(result["entries_count"], 1)
                    self.assertEqual(result["removed_stale_count"], 1)
                    self.assertEqual(result["entries"][0]["topic"], "programming/javascript")

                    status, payload = request_json(base_url, "/api/library")
                    self.assertEqual(status, 200)
                    self.assertEqual(payload["entries"][0]["path"], "programming/javascript/javascript-note_TEST_en_transcript.md")

    def test_backend_batch_job_runs_with_mocked_transcribe(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "outputs"
            output_dir.mkdir()
            settings_path = Path(temp_dir) / "local_settings.json"
            settings_path.write_text(
                json.dumps({"output_dir": str(output_dir), "batch_limit": 3}),
                encoding="utf-8",
            )

            def fake_transcribe(url, output_dir_arg, **kwargs):
                slug = "first" if "one" in url else "second"
                target_dir = Path(output_dir_arg) / "other"
                target_dir.mkdir(parents=True, exist_ok=True)
                for suffix in ("md", "txt", "json", "srt", "vtt"):
                    (target_dir / f"{slug}.{suffix}").write_text(f"{slug} {suffix}", encoding="utf-8")
                return {
                    "title": slug,
                    "output_rel_path": f"other/{slug}.md",
                    "txt_output_rel_path": f"other/{slug}.txt",
                    "json_output_rel_path": f"other/{slug}.json",
                    "srt_output_rel_path": f"other/{slug}.srt",
                    "vtt_output_rel_path": f"other/{slug}.vtt",
                    "transcript_text": "# " + slug,
                }

            batch_state_path = Path(temp_dir) / "batch_jobs.json"
            with patch.object(app, "LOCAL_SETTINGS_PATH", settings_path), patch.object(app, "BATCH_STATE_PATH", batch_state_path), patch.object(app, "transcribe_url", side_effect=fake_transcribe):
                with run_test_server() as base_url:
                    status, payload = request_json(
                        base_url,
                        "/api/batch",
                        {
                            "urls": [
                                "https://www.youtube.com/watch?v=one",
                                "https://youtu.be/two",
                            ],
                            "options": {
                                "include_timestamps": False,
                                "include_metadata": True,
                                "paragraph_mode": True,
                                "generate_study_notes": False,
                            },
                        },
                    )
                    self.assertEqual(status, 200)
                    job_id = payload["job"]["id"]

                    job = payload["job"]
                    deadline = time.time() + 5
                    while time.time() < deadline:
                        status, payload = request_json(base_url, "/api/batch?id=" + job_id)
                        self.assertEqual(status, 200)
                        job = payload["job"]
                        if job["status"] == "finished":
                            break
                        time.sleep(0.05)

                    self.assertEqual(job["status"], "finished")
                    self.assertEqual(job["completed"], 2)
                    self.assertEqual(job["failed"], 0)
                    self.assertEqual(job["items"][0]["result"]["download_url"], "/outputs/other/first.md")
                    self.assertTrue(batch_state_path.exists())

                    with urllib.request.urlopen(base_url + "/api/batch/zip?id=" + job_id, timeout=10) as response:
                        self.assertEqual(response.status, 200)
                        self.assertEqual(response.headers.get_content_type(), "application/zip")
                        zip_bytes = response.read()

                    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
                        names = set(archive.namelist())
                        self.assertIn("batch-job.json", names)
                        self.assertIn("other/first.md", names)
                        self.assertIn("other/second.vtt", names)

                    app.BATCH_JOBS.clear()
                    app.load_batch_state()
                    restored = app.get_batch_job(job_id)
                    self.assertEqual(restored["status"], "finished")
                    self.assertEqual(restored["completed"], 2)

    def test_backend_batch_pause_and_resume_between_items(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "outputs"
            output_dir.mkdir()
            settings_path = Path(temp_dir) / "local_settings.json"
            settings_path.write_text(
                json.dumps({"output_dir": str(output_dir), "batch_limit": 3}),
                encoding="utf-8",
            )

            def slow_fake_transcribe(url, output_dir_arg, **kwargs):
                time.sleep(0.15)
                slug = "one" if "one" in url else "two"
                target_dir = Path(output_dir_arg) / "other"
                target_dir.mkdir(parents=True, exist_ok=True)
                (target_dir / f"{slug}.md").write_text(slug, encoding="utf-8")
                return {
                    "title": slug,
                    "output_rel_path": f"other/{slug}.md",
                    "txt_output_rel_path": "",
                    "json_output_rel_path": "",
                    "srt_output_rel_path": "",
                    "vtt_output_rel_path": "",
                }

            batch_state_path = Path(temp_dir) / "batch_jobs.json"
            with patch.object(app, "LOCAL_SETTINGS_PATH", settings_path), patch.object(app, "BATCH_STATE_PATH", batch_state_path), patch.object(app, "transcribe_url", side_effect=slow_fake_transcribe):
                with run_test_server() as base_url:
                    status, payload = request_json(
                        base_url,
                        "/api/batch",
                        {
                            "urls": [
                                "https://www.youtube.com/watch?v=one",
                                "https://www.youtube.com/watch?v=two",
                            ],
                            "options": {},
                        },
                    )
                    self.assertEqual(status, 200)
                    job_id = payload["job"]["id"]

                    status, payload = request_json(base_url, "/api/batch/pause", {"job_id": job_id})
                    self.assertEqual(status, 200)

                    deadline = time.time() + 5
                    paused_job = None
                    while time.time() < deadline:
                        status, payload = request_json(base_url, "/api/batch?id=" + job_id)
                        self.assertEqual(status, 200)
                        paused_job = payload["job"]
                        if paused_job["status"] == "paused" and paused_job["completed"] == 1:
                            break
                        time.sleep(0.05)

                    self.assertEqual(paused_job["status"], "paused")
                    self.assertEqual(paused_job["completed"], 1)

                    status, payload = request_json(base_url, "/api/batch/resume", {"job_id": job_id})
                    self.assertEqual(status, 200)

                    deadline = time.time() + 5
                    final_job = None
                    while time.time() < deadline:
                        status, payload = request_json(base_url, "/api/batch?id=" + job_id)
                        self.assertEqual(status, 200)
                        final_job = payload["job"]
                        if final_job["status"] == "finished":
                            break
                        time.sleep(0.05)

                    self.assertEqual(final_job["status"], "finished")
                    self.assertEqual(final_job["completed"], 2)

    def test_backend_batch_expands_playlist_when_enabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            output_dir = Path(temp_dir) / "outputs"
            output_dir.mkdir()
            settings_path = Path(temp_dir) / "local_settings.json"
            settings_path.write_text(
                json.dumps({"output_dir": str(output_dir), "batch_limit": 3}),
                encoding="utf-8",
            )
            batch_state_path = Path(temp_dir) / "batch_jobs.json"

            def fake_transcribe(url, output_dir_arg, **kwargs):
                slug = "a" if "aaa" in url else "b"
                target_dir = Path(output_dir_arg) / "playlist"
                target_dir.mkdir(parents=True, exist_ok=True)
                (target_dir / f"{slug}.md").write_text(slug, encoding="utf-8")
                return {
                    "title": slug,
                    "output_rel_path": f"playlist/{slug}.md",
                    "txt_output_rel_path": "",
                    "json_output_rel_path": "",
                    "srt_output_rel_path": "",
                    "vtt_output_rel_path": "",
                }

            with patch.object(app, "LOCAL_SETTINGS_PATH", settings_path), patch.object(app, "BATCH_STATE_PATH", batch_state_path), patch.object(app, "transcribe_url", side_effect=fake_transcribe), patch.object(
                app,
                "expand_playlist_url",
                return_value=[
                    "https://www.youtube.com/watch?v=aaa111",
                    "https://www.youtube.com/watch?v=bbb222",
                ],
            ):
                with run_test_server() as base_url:
                    status, payload = request_json(
                        base_url,
                        "/api/batch",
                        {
                            "urls": ["https://www.youtube.com/playlist?list=PL123"],
                            "expand_playlists": True,
                            "options": {},
                        },
                    )
                    self.assertEqual(status, 200)
                    job_id = payload["job"]["id"]
                    self.assertEqual(payload["job"]["total"], 2)
                    self.assertEqual(payload["job"]["items"][0]["url"], "https://www.youtube.com/watch?v=aaa111")

                    deadline = time.time() + 5
                    final_job = None
                    while time.time() < deadline:
                        status, payload = request_json(base_url, "/api/batch?id=" + job_id)
                        self.assertEqual(status, 200)
                        final_job = payload["job"]
                        if final_job["status"] == "finished":
                            break
                        time.sleep(0.05)

                    self.assertEqual(final_job["completed"], 2)


if __name__ == "__main__":
    unittest.main()
