from __future__ import annotations

import io
import importlib.metadata
import json
import mimetypes
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from collections import Counter
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from yt_dlp import YoutubeDL

from transcriber import (
    LIBRARY_SCHEMA_VERSION,
    TranscriptionError,
    classify_topic,
    humanize_topic,
    list_topic_options,
    list_tracks_for_url,
    tags_for_topic,
    transcribe_url,
)


BASE_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = BASE_DIR / "outputs"
LOCAL_SETTINGS_PATH = BASE_DIR / "local_settings.json"
BATCH_STATE_PATH = BASE_DIR / "batch_jobs.json"
HOST = "127.0.0.1"
PORT = 8765
LOCAL_CORS_HOSTS = {"localhost", "127.0.0.1", "::1"}
TEXT_LIBRARY_EXTENSIONS = {".md", ".txt", ".json", ".srt", ".vtt"}
SESSION_TTL_SECONDS = 40
SHUTDOWN_AFTER_EMPTY_SECONDS = 15
DISABLE_IDLE_SHUTDOWN = os.environ.get("YTT_DISABLE_IDLE_SHUTDOWN", "").strip() == "1"
SESSION_LOCK = threading.Lock()
CLIENT_SESSIONS = {}
ZERO_SESSION_SINCE = time.monotonic()
SERVER_INSTANCE = None
DEFAULT_SETTINGS = {
    "study_guide_provider": "local",
    "study_guide_profile_id": "",
    "model_profiles": [],
    "custom_topics": [],
    "output_dir": "outputs",
    "default_options": {
        "include_timestamps": True,
        "include_metadata": True,
        "paragraph_mode": False,
        "generate_study_notes": False,
    },
    "batch_limit": 10,
    "expand_playlists": False,
}

API_STUDY_GUIDE_SOURCE_BUDGET_CHARS = 24000
API_STUDY_GUIDE_MIN_EXCERPT_CHARS = 500
API_STUDY_GUIDE_MAX_TOKENS = 1000
API_STUDY_GUIDE_MAX_SOURCES = 8
API_STUDY_GUIDE_MAX_INPUT_CHARS = 300000
API_STUDY_GUIDE_MAX_OUTPUT_TOKENS = 8192
BATCH_LOCK = threading.Lock()
BATCH_JOBS = {}
BATCH_JOB_TTL_SECONDS = 6 * 60 * 60


class ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class AppHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def end_headers(self):
        allowed_origin = self.get_allowed_cors_origin()
        if allowed_origin:
            self.send_header("Access-Control-Allow-Origin", allowed_origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
        super().end_headers()

    def do_OPTIONS(self):
        if self.headers.get("Origin") and not self.get_allowed_cors_origin():
            self.send_error(HTTPStatus.FORBIDDEN, "CORS origin is not allowed")
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def get_allowed_cors_origin(self):
        origin = self.headers.get("Origin", "").strip()
        if not origin:
            return None

        parsed = urlparse(origin)
        if parsed.scheme not in {"http", "https"}:
            return None

        hostname = parsed.hostname
        if hostname not in LOCAL_CORS_HOSTS:
            return None

        server_port = self.server.server_address[1]
        origin_port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if origin_port != server_port:
            return None

        return origin

    def do_GET(self):
        parsed_path = urlparse(self.path)

        if parsed_path.path in ("/v2", "/v2/"):
            self.path = "/index.html"
            super().do_GET()
            return

        if parsed_path.path == "/api/health":
            active_clients = count_active_sessions()
            self.send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "host": HOST,
                    "port": PORT,
                    "active_clients": active_clients,
                },
            )
            return

        if parsed_path.path == "/api/diagnostics":
            self.send_json(HTTPStatus.OK, {"ok": True, "diagnostics": get_diagnostics()})
            return

        if parsed_path.path == "/api/library":
            self.send_json(HTTPStatus.OK, {"ok": True, "entries": load_library_entries()})
            return

        if parsed_path.path == "/api/topics":
            self.send_json(HTTPStatus.OK, {"ok": True, "topics": list_topic_options(load_custom_topics())})
            return

        if parsed_path.path == "/api/settings":
            self.send_json(HTTPStatus.OK, {"ok": True, "settings": public_settings(load_settings())})
            return

        if parsed_path.path == "/api/batch":
            query = parse_qs(parsed_path.query)
            job_id = str(query.get("id", [""])[0]).strip()
            job = get_batch_job(job_id)
            if not job:
                self.send_json(
                    HTTPStatus.NOT_FOUND,
                    {
                        "ok": False,
                        "error_code": "batch_not_found",
                        "message": "Batch job not found.",
                    },
                )
                return
            self.send_json(HTTPStatus.OK, {"ok": True, "job": job})
            return

        if parsed_path.path == "/api/batch/zip":
            query = parse_qs(parsed_path.query)
            job_id = str(query.get("id", [""])[0]).strip()
            try:
                zip_bytes = build_batch_zip(job_id)
            except TranscriptionError as exc:
                error_payload = {
                    "ok": False,
                    "error_code": exc.code,
                    "message": exc.user_message,
                }
                if exc.technical_message and exc.technical_message != exc.user_message:
                    error_payload["details"] = exc.technical_message[:500]
                self.send_json(HTTPStatus.BAD_REQUEST, error_payload)
                return

            filename = f"batch-{job_id[:10] or 'results'}.zip"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(zip_bytes)))
            self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(zip_bytes)
            return

        if parsed_path.path.startswith("/outputs/"):
            self.serve_output_file(parsed_path.path.removeprefix("/outputs/"))
            return

        if parsed_path.path == "/api/library/file":
            query = parse_qs(parsed_path.query)
            requested_path = str(query.get("path", [""])[0]).strip()
            if requested_path == "":
                self.send_json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "ok": False,
                        "error_code": "missing_path",
                        "message": "Missing library file path.",
                    },
                )
                return

            try:
                file_path = resolve_output_file(requested_path)
            except ValueError:
                self.send_json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "ok": False,
                        "error_code": "invalid_path",
                        "message": "The library file path is invalid.",
                    },
                )
                return

            if not file_path.exists() or not file_path.is_file():
                self.send_json(
                    HTTPStatus.NOT_FOUND,
                    {
                        "ok": False,
                        "error_code": "file_not_found",
                        "message": "Library file not found.",
                    },
                )
                return

            try:
                text = file_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                self.send_json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "ok": False,
                        "error_code": "unsupported_file",
                        "message": "This library file could not be opened as text.",
                    },
                )
                return

            self.send_json(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "path": to_output_relative_path(file_path),
                    "download_url": output_download_url(to_output_relative_path(file_path)),
                    "text": text,
                },
            )
            return

        super().do_GET()

    def do_POST(self):
        if self.path == "/api/session/open":
            payload = self.read_json_body()
            if payload is None:
                return
            client_id = str(payload.get("client_id", "")).strip()
            if client_id != "":
                register_session(client_id)
            self.send_json(HTTPStatus.OK, {"ok": True})
            return

        if self.path == "/api/session/heartbeat":
            payload = self.read_json_body()
            if payload is None:
                return
            client_id = str(payload.get("client_id", "")).strip()
            if client_id != "":
                register_session(client_id)
            self.send_json(HTTPStatus.OK, {"ok": True})
            return

        if self.path == "/api/session/close":
            payload = self.read_json_body()
            if payload is None:
                return
            client_id = str(payload.get("client_id", "")).strip()
            if client_id != "":
                close_session(client_id)
            self.send_json(HTTPStatus.OK, {"ok": True})
            return

        if self.path == "/api/tracks":
            payload = self.read_json_body()
            if payload is None:
                return

            url = str(payload.get("url", "")).strip()
            if url == "":
                self.send_json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "ok": False,
                        "error_code": "missing_url",
                        "message": "Paste a YouTube URL before checking captions.",
                    },
                )
                return

            try:
                result = list_tracks_for_url(url)
            except TranscriptionError as exc:
                status = HTTPStatus.TOO_MANY_REQUESTS if exc.code == "rate_limited" else HTTPStatus.BAD_REQUEST
                self.send_json(
                    status,
                    {
                        "ok": False,
                        "error_code": exc.code,
                        "message": exc.user_message,
                    },
                )
                return
            except Exception as exc:
                self.send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "ok": False,
                        "error_code": "server_error",
                        "message": "An unexpected server error occurred while checking captions.",
                        "details": str(exc),
                    },
                )
                return

            self.send_json(HTTPStatus.OK, {"ok": True, **result})
            return

        if self.path == "/api/settings":
            payload = self.read_json_body()
            if payload is None:
                return

            try:
                saved_settings = save_settings_from_payload(payload)
            except TranscriptionError as exc:
                error_payload = {
                    "ok": False,
                    "error_code": exc.code,
                    "message": exc.user_message,
                }
                if exc.technical_message and exc.technical_message != exc.user_message:
                    error_payload["details"] = exc.technical_message[:500]
                self.send_json(HTTPStatus.BAD_REQUEST, error_payload)
                return

            self.send_json(HTTPStatus.OK, {"ok": True, "settings": public_settings(saved_settings)})
            return

        if self.path == "/api/settings/test-model":
            payload = self.read_json_body()
            if payload is None:
                return

            try:
                result = test_model_profile(str(payload.get("profile_id", "")).strip())
            except TranscriptionError as exc:
                self.send_json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "ok": False,
                        "error_code": exc.code,
                        "message": exc.user_message,
                    },
                )
                return
            except Exception as exc:
                self.send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "ok": False,
                        "error_code": "server_error",
                        "message": "An unexpected server error occurred while testing this model profile.",
                        "details": str(exc),
                    },
                )
                return

            self.send_json(HTTPStatus.OK, {"ok": True, "result": result})
            return

        if self.path == "/api/library/rebuild":
            payload = self.read_json_body()
            if payload is None:
                return

            try:
                result = rebuild_library_index()
            except TranscriptionError as exc:
                self.send_json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "ok": False,
                        "error_code": exc.code,
                        "message": exc.user_message,
                    },
                )
                return
            except Exception as exc:
                self.send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "ok": False,
                        "error_code": "server_error",
                        "message": "An unexpected server error occurred while rebuilding the library index.",
                        "details": str(exc),
                    },
                )
                return

            self.send_json(HTTPStatus.OK, {"ok": True, "result": result})
            return

        if self.path == "/api/library/study-guide":
            payload = self.read_json_body()
            if payload is None:
                return

            try:
                result = build_library_study_guide(
                    topic=str(payload.get("topic", "")).strip(),
                    topics=parse_topic_list(payload.get("topics")),
                    max_sources=parse_positive_int(payload.get("max_sources"), 8),
                    provider=str(payload.get("provider", "")).strip() or None,
                    profile_id=str(payload.get("profile_id", "")).strip() or None,
                )
            except TranscriptionError as exc:
                error_payload = {
                    "ok": False,
                    "error_code": exc.code,
                    "message": exc.user_message,
                }
                if exc.technical_message and exc.technical_message != exc.user_message:
                    error_payload["details"] = exc.technical_message[:500]
                self.send_json(HTTPStatus.BAD_REQUEST, error_payload)
                return
            except Exception as exc:
                self.send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "ok": False,
                        "error_code": "server_error",
                        "message": "An unexpected server error occurred while generating the study guide.",
                        "details": str(exc),
                    },
                )
                return

            self.send_json(HTTPStatus.OK, {"ok": True, "result": result})
            return

        if self.path == "/api/library/classify-topic":
            payload = self.read_json_body()
            if payload is None:
                return

            try:
                result = classify_library_entry_topic(
                    requested_path=str(payload.get("path", "")).strip(),
                    provider=str(payload.get("provider", "")).strip() or None,
                    profile_id=str(payload.get("profile_id", "")).strip() or None,
                )
            except TranscriptionError as exc:
                self.send_json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "ok": False,
                        "error_code": exc.code,
                        "message": exc.user_message,
                    },
                )
                return
            except Exception as exc:
                self.send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {
                        "ok": False,
                        "error_code": "server_error",
                        "message": "An unexpected server error occurred while classifying this topic.",
                        "details": str(exc),
                    },
                )
                return

            self.send_json(HTTPStatus.OK, {"ok": True, "result": result})
            return

        if self.path == "/api/batch":
            payload = self.read_json_body()
            if payload is None:
                return

            try:
                job = create_batch_job(payload)
            except TranscriptionError as exc:
                self.send_json(
                    HTTPStatus.BAD_REQUEST,
                    {
                        "ok": False,
                        "error_code": exc.code,
                        "message": exc.user_message,
                    },
                )
                return

            self.send_json(HTTPStatus.OK, {"ok": True, "job": job})
            return

        if self.path == "/api/batch/cancel":
            payload = self.read_json_body()
            if payload is None:
                return

            job_id = str(payload.get("job_id", "")).strip()
            job = cancel_batch_job(job_id)
            if not job:
                self.send_json(
                    HTTPStatus.NOT_FOUND,
                    {
                        "ok": False,
                        "error_code": "batch_not_found",
                        "message": "Batch job not found.",
                    },
                )
                return

            self.send_json(HTTPStatus.OK, {"ok": True, "job": job})
            return

        if self.path in ("/api/batch/pause", "/api/batch/resume"):
            payload = self.read_json_body()
            if payload is None:
                return

            job_id = str(payload.get("job_id", "")).strip()
            job = set_batch_paused(job_id, self.path.endswith("/pause"))
            if not job:
                self.send_json(
                    HTTPStatus.NOT_FOUND,
                    {
                        "ok": False,
                        "error_code": "batch_not_found",
                        "message": "Batch job not found.",
                    },
                )
                return

            self.send_json(HTTPStatus.OK, {"ok": True, "job": job})
            return

        if self.path != "/api/transcribe":
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        payload = self.read_json_body()
        if payload is None:
            return

        url = str(payload.get("url", "")).strip()
        if url == "":
            self.send_json(
                HTTPStatus.BAD_REQUEST,
                {
                    "ok": False,
                    "error_code": "missing_url",
                    "message": "Paste a YouTube URL before clicking Transcribe.",
                },
            )
            return

        try:
            start_seconds = parse_optional_seconds(payload.get("start_seconds"), "Start seconds")
            end_seconds = parse_optional_seconds(payload.get("end_seconds"), "End seconds")
            result = transcribe_url(
                url,
                get_output_dir(),
                track_key=str(payload.get("track_key", "")).strip() or None,
                topic_override=str(payload.get("topic_override", "")).strip() or None,
                include_timestamps=parse_bool(payload.get("include_timestamps"), True),
                include_metadata=parse_bool(payload.get("include_metadata"), True),
                paragraph_mode=parse_bool(payload.get("paragraph_mode"), False),
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                generate_study_notes=parse_bool(payload.get("generate_study_notes"), False),
                custom_topics=load_custom_topics(),
            )
        except TranscriptionError as exc:
            status = HTTPStatus.TOO_MANY_REQUESTS if exc.code == "rate_limited" else HTTPStatus.BAD_REQUEST
            self.send_json(
                status,
                {
                    "ok": False,
                    "error_code": exc.code,
                    "message": exc.user_message,
                },
            )
            return
        except Exception as exc:
            self.send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {
                    "ok": False,
                    "error_code": "server_error",
                    "message": "An unexpected server error occurred.",
                    "details": str(exc),
                },
            )
            return

        add_download_urls(result)
        self.send_json(HTTPStatus.OK, {"ok": True, "result": result})

    def read_json_body(self) -> dict | None:
        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length)
        if raw_body == b"":
            return {}

        try:
            return json.loads(raw_body.decode("utf-8"))
        except json.JSONDecodeError:
            self.send_json(
                HTTPStatus.BAD_REQUEST,
                {
                    "ok": False,
                    "error_code": "invalid_json",
                    "message": "The request is invalid. Reload the page and try again.",
                },
            )
            return None

    def guess_type(self, path):
        guessed = super().guess_type(path)
        if guessed != "application/octet-stream":
            return guessed
        return mimetypes.guess_type(path)[0] or "application/octet-stream"

    def send_json(self, status: HTTPStatus, payload: dict):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def serve_output_file(self, rel_path: str):
        try:
            file_path = resolve_output_file(unquote(rel_path))
        except ValueError:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        if not file_path.exists() or not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        data = file_path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", self.guess_type(str(file_path)))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'attachment; filename="{file_path.name}"')
        self.end_headers()
        self.wfile.write(data)


def cleanup_expired_sessions(now: float | None = None):
    global ZERO_SESSION_SINCE

    if now is None:
        now = time.monotonic()

    with SESSION_LOCK:
        expired_ids = [
            client_id
            for client_id, seen_at in CLIENT_SESSIONS.items()
            if now - seen_at > SESSION_TTL_SECONDS
        ]
        for client_id in expired_ids:
            CLIENT_SESSIONS.pop(client_id, None)

        if CLIENT_SESSIONS:
            ZERO_SESSION_SINCE = None
        elif ZERO_SESSION_SINCE is None:
            ZERO_SESSION_SINCE = now

        return len(CLIENT_SESSIONS)


def count_active_sessions() -> int:
    return cleanup_expired_sessions()


def get_diagnostics() -> dict:
    return {
        "yt_dlp": get_yt_dlp_diagnostics(),
    }


def get_yt_dlp_diagnostics() -> dict:
    module_version = ""
    try:
        module_version = importlib.metadata.version("yt-dlp")
    except importlib.metadata.PackageNotFoundError:
        module_version = ""

    cli_path = shutil.which("yt-dlp") or ""
    cli_version = ""
    cli_error = ""

    if cli_path:
        try:
            completed = subprocess.run(
                [cli_path, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            stdout_lines = (completed.stdout or "").strip().splitlines()
            cli_version = stdout_lines[0] if stdout_lines else ""
            if completed.returncode != 0:
                cli_error = (completed.stderr or "yt-dlp --version failed.").strip()
        except (OSError, subprocess.SubprocessError) as exc:
            cli_error = str(exc)

    hints = [
        "Keep yt-dlp updated if YouTube captions suddenly stop working.",
        "PO Token, cookies, or account-based access are not used by default and should stay explicit opt-in.",
        "Private videos, missing captions, rate limits, or upstream YouTube changes can still block transcription.",
    ]

    if not module_version:
        status = "error"
        message = "The yt-dlp Python package is not available. Install requirements before transcribing."
    elif not cli_path:
        status = "warning"
        message = "The yt-dlp Python package is available, but the yt-dlp command is not on PATH."
    elif cli_error:
        status = "warning"
        message = "The yt-dlp command was found, but its version check returned an error."
    else:
        status = "ok"
        message = "yt-dlp is available for local caption extraction."

    return {
        "status": status,
        "available": bool(module_version),
        "module_version": module_version,
        "cli_available": bool(cli_path),
        "cli_path": cli_path,
        "cli_version": cli_version,
        "cli_error": cli_error,
        "message": message,
        "hints": hints,
    }


def parse_bool(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("1", "true", "yes", "on"):
            return True
        if normalized in ("0", "false", "no", "off"):
            return False
    return default


def parse_optional_seconds(value, label: str) -> float | None:
    if value is None:
        return None

    if isinstance(value, str):
        value = value.strip()
        if value == "":
            return None

    try:
        seconds = float(value)
    except (TypeError, ValueError) as exc:
        raise TranscriptionError(
            "invalid_time_range",
            f"{label} must be a number of seconds.",
        ) from exc

    if seconds < 0:
        raise TranscriptionError(
            "invalid_time_range",
            f"{label} must be zero or greater.",
        )

    return seconds


def parse_positive_int(value, default: int) -> int:
    if value is None or str(value).strip() == "":
        return default

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default

    return max(1, min(parsed, 24))


def parse_topic_list(value) -> list[str]:
    if not isinstance(value, list):
        return []

    topics = []
    seen = set()
    for item in value:
        topic = str(item).strip()
        if topic and topic not in seen:
            seen.add(topic)
            topics.append(topic)
    return topics[:24]


def create_batch_job(payload: dict) -> dict:
    cleanup_old_batch_jobs()
    settings = load_settings()
    batch_limit = sanitize_batch_limit(settings.get("batch_limit", 10))
    urls = normalize_batch_urls(payload.get("urls"))
    expand_playlists = parse_bool(payload.get("expand_playlists"), parse_bool(settings.get("expand_playlists"), False))
    urls = expand_batch_urls(urls, expand_playlists, batch_limit)
    if not urls:
        raise TranscriptionError(
            "missing_batch_urls",
            "Add at least one YouTube video URL to the batch queue.",
        )
    if len(urls) > batch_limit:
        raise TranscriptionError(
            "batch_limit_exceeded",
            f"Batch queue limit is {batch_limit} URLs per run.",
        )

    options = normalize_transcribe_options(payload.get("options", {}), settings)
    job_id = uuid.uuid4().hex
    now = utc_timestamp()
    job = {
        "id": job_id,
        "status": "queued",
        "created_at": now,
        "updated_at": now,
        "started_at": "",
        "finished_at": "",
        "cancel_requested": False,
        "pause_requested": False,
        "total": len(urls),
        "completed": 0,
        "failed": 0,
        "canceled": 0,
        "options": options,
        "items": [
            {
                "index": index,
                "url": url,
                "status": "queued",
                "label": "queued",
                "message": "",
                "result": None,
            }
            for index, url in enumerate(urls)
        ],
    }
    with BATCH_LOCK:
        BATCH_JOBS[job_id] = job
        save_batch_state_locked()

    threading.Thread(target=run_batch_job, args=(job_id,), daemon=True).start()
    return public_batch_job(job)


def expand_batch_urls(urls: list[str], expand_playlists: bool, batch_limit: int) -> list[str]:
    if not expand_playlists:
        return urls

    expanded = []
    seen = set()
    for url in urls:
        candidates = expand_playlist_url(url, max_items=batch_limit)
        for candidate in candidates:
            if candidate not in seen:
                seen.add(candidate)
                expanded.append(candidate)
            if len(expanded) > batch_limit:
                raise TranscriptionError(
                    "batch_limit_exceeded",
                    f"Batch queue limit is {batch_limit} URLs per run.",
                )

    return expanded


def expand_playlist_url(url: str, max_items: int) -> list[str]:
    if not is_playlist_url(url):
        return [url]

    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "playlistend": max_items,
    }

    try:
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as exc:
        raise TranscriptionError(
            "playlist_expand_failed",
            "Could not expand this playlist. Try individual video URLs or turn off playlist expansion.",
            str(exc),
        ) from exc

    entries = info.get("entries") if isinstance(info, dict) else None
    if not entries:
        return [url]

    video_urls = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        video_url = entry.get("webpage_url") or entry.get("url") or entry.get("id")
        video_url = normalize_video_url(video_url)
        if video_url:
            video_urls.append(video_url)

    if not video_urls:
        raise TranscriptionError(
            "playlist_expand_failed",
            "The playlist did not return any readable video URLs.",
        )

    return video_urls


def is_playlist_url(url: str) -> bool:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    return bool(query.get("list")) or parsed.path.rstrip("/").endswith("/playlist")


def normalize_video_url(value: str | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.startswith("http://") or raw.startswith("https://"):
        return raw
    if re.fullmatch(r"[A-Za-z0-9_-]{6,}", raw):
        return "https://www.youtube.com/watch?v=" + raw
    return ""


def normalize_batch_urls(value) -> list[str]:
    raw_urls = value if isinstance(value, list) else []
    urls = []
    seen = set()
    invalid = []
    for raw_url in raw_urls:
        url = str(raw_url or "").strip()
        if not url:
            continue
        if not is_youtube_url(url):
            invalid.append(url)
            continue
        if url not in seen:
            seen.add(url)
            urls.append(url)

    if invalid:
        raise TranscriptionError(
            "invalid_batch_url",
            "Remove invalid or non-YouTube URL(s) before starting the batch.",
        )
    return urls


def is_youtube_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    return parsed.scheme in ("http", "https") and (
        hostname == "youtu.be"
        or hostname == "youtube.com"
        or hostname.endswith(".youtube.com")
    )


def normalize_transcribe_options(value, settings: dict | None = None) -> dict:
    if not isinstance(value, dict):
        value = {}
    settings = settings or load_settings()
    defaults = sanitize_default_options(settings.get("default_options", {}))
    start_seconds = parse_optional_seconds(value.get("start_seconds"), "Start seconds")
    end_seconds = parse_optional_seconds(value.get("end_seconds"), "End seconds")
    if start_seconds is not None and end_seconds is not None and end_seconds <= start_seconds:
        raise TranscriptionError(
            "invalid_time_range",
            "End seconds must be greater than start seconds.",
        )
    return {
        "topic_override": str(value.get("topic_override", "")).strip() or None,
        "include_timestamps": parse_bool(value.get("include_timestamps"), defaults["include_timestamps"]),
        "include_metadata": parse_bool(value.get("include_metadata"), defaults["include_metadata"]),
        "paragraph_mode": parse_bool(value.get("paragraph_mode"), defaults["paragraph_mode"]),
        "generate_study_notes": parse_bool(value.get("generate_study_notes"), defaults["generate_study_notes"]),
        "start_seconds": start_seconds,
        "end_seconds": end_seconds,
    }


def run_batch_job(job_id: str):
    update_batch_job(job_id, status="running", started_at=utc_timestamp())
    for item in get_batch_items(job_id):
        if not wait_while_batch_paused(job_id):
            mark_batch_item(job_id, item["index"], "canceled", "canceled", "Canceled before starting.")
            continue

        if is_batch_cancel_requested(job_id):
            mark_batch_item(job_id, item["index"], "canceled", "canceled", "Canceled before starting.")
            continue

        mark_batch_item(job_id, item["index"], "running", "running", "")
        try:
            options = get_batch_options(job_id)
            result = transcribe_url(
                item["url"],
                get_output_dir(),
                track_key=None,
                topic_override=options.get("topic_override"),
                include_timestamps=options["include_timestamps"],
                include_metadata=options["include_metadata"],
                paragraph_mode=options["paragraph_mode"],
                start_seconds=options["start_seconds"],
                end_seconds=options["end_seconds"],
                generate_study_notes=options["generate_study_notes"],
                custom_topics=load_custom_topics(),
            )
            mark_batch_item(job_id, item["index"], "done", "done", "", add_download_urls(result))
        except TranscriptionError as exc:
            mark_batch_item(job_id, item["index"], "error", "error", exc.user_message)
        except Exception:
            mark_batch_item(job_id, item["index"], "error", "error", "An unexpected server error occurred.")

    finish_batch_job(job_id)


def wait_while_batch_paused(job_id: str) -> bool:
    while True:
        with BATCH_LOCK:
            job = BATCH_JOBS.get(job_id)
            if not job:
                return False
            if job.get("cancel_requested"):
                return False
            if not job.get("pause_requested"):
                if job.get("status") == "paused":
                    job["status"] = "running"
                    job["updated_at"] = utc_timestamp()
                    save_batch_state_locked()
                return True
            job["status"] = "paused"
            job["updated_at"] = utc_timestamp()
            save_batch_state_locked()
        time.sleep(0.3)


def get_batch_items(job_id: str) -> list[dict]:
    with BATCH_LOCK:
        job = BATCH_JOBS.get(job_id)
        return [dict(item) for item in job.get("items", [])] if job else []


def get_batch_options(job_id: str) -> dict:
    with BATCH_LOCK:
        job = BATCH_JOBS.get(job_id)
        return dict(job.get("options", {})) if job else {}


def is_batch_cancel_requested(job_id: str) -> bool:
    with BATCH_LOCK:
        job = BATCH_JOBS.get(job_id)
        return bool(job and job.get("cancel_requested"))


def mark_batch_item(
    job_id: str,
    index: int,
    status: str,
    label: str,
    message: str = "",
    result: dict | None = None,
):
    with BATCH_LOCK:
        job = BATCH_JOBS.get(job_id)
        if not job:
            return
        item = job["items"][index]
        item["status"] = status
        item["label"] = label
        item["message"] = message
        if result is not None:
            item["result"] = result
        update_batch_counts_locked(job)
        save_batch_state_locked()


def update_batch_job(job_id: str, **updates):
    with BATCH_LOCK:
        job = BATCH_JOBS.get(job_id)
        if not job:
            return
        job.update(updates)
        job["updated_at"] = utc_timestamp()
        save_batch_state_locked()


def finish_batch_job(job_id: str):
    with BATCH_LOCK:
        job = BATCH_JOBS.get(job_id)
        if not job:
            return
        update_batch_counts_locked(job)
        if job["canceled"] and job["completed"] + job["failed"] + job["canceled"] == job["total"]:
            job["status"] = "canceled"
        elif job["failed"]:
            job["status"] = "finished_with_errors"
        else:
            job["status"] = "finished"
        job["finished_at"] = utc_timestamp()
        job["updated_at"] = job["finished_at"]
        save_batch_state_locked()


def update_batch_counts_locked(job: dict):
    items = job.get("items", [])
    job["completed"] = len([item for item in items if item.get("status") == "done"])
    job["failed"] = len([item for item in items if item.get("status") == "error"])
    job["canceled"] = len([item for item in items if item.get("status") == "canceled"])
    if job.get("status") not in ("finished", "finished_with_errors", "canceled", "interrupted"):
        if job.get("pause_requested"):
            job["status"] = "paused"
        if any(item.get("status") == "running" for item in items):
            job["status"] = "running"
        elif job.get("pause_requested"):
            job["status"] = "paused"
        elif any(item.get("status") == "queued" for item in items):
            job["status"] = "queued"
        job["updated_at"] = utc_timestamp()


def get_batch_job(job_id: str) -> dict | None:
    cleanup_old_batch_jobs()
    with BATCH_LOCK:
        job = BATCH_JOBS.get(job_id)
        return public_batch_job(job) if job else None


def cancel_batch_job(job_id: str) -> dict | None:
    with BATCH_LOCK:
        job = BATCH_JOBS.get(job_id)
        if not job:
            return None
        job["cancel_requested"] = True
        job["pause_requested"] = False
        for item in job.get("items", []):
            if item.get("status") == "queued":
                item["status"] = "canceled"
                item["label"] = "canceled"
                item["message"] = "Canceled before starting."
        update_batch_counts_locked(job)
        job["updated_at"] = utc_timestamp()
        save_batch_state_locked()
        return public_batch_job(job)


def set_batch_paused(job_id: str, paused: bool) -> dict | None:
    with BATCH_LOCK:
        job = BATCH_JOBS.get(job_id)
        if not job:
            return None
        if job.get("status") in ("finished", "finished_with_errors", "canceled"):
            return public_batch_job(job)
        job["pause_requested"] = paused
        update_batch_counts_locked(job)
        if paused and not any(item.get("status") == "running" for item in job.get("items", [])):
            job["status"] = "paused"
        elif not paused and job.get("status") == "paused":
            job["status"] = "running"
        job["updated_at"] = utc_timestamp()
        save_batch_state_locked()
        return public_batch_job(job)


def public_batch_job(job: dict) -> dict:
    public = json.loads(json.dumps(job))
    public.pop("cancel_requested", None)
    public.pop("pause_requested", None)
    return public


def build_batch_zip(job_id: str) -> bytes:
    job = get_batch_job(job_id)
    if not job:
        raise TranscriptionError(
            "batch_not_found",
            "Batch job not found.",
        )

    buffer = io.BytesIO()
    included = set()
    with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("batch-job.json", json.dumps(job, ensure_ascii=False, indent=2) + "\n")
        for item in job.get("items", []):
            result = item.get("result")
            if item.get("status") != "done" or not isinstance(result, dict):
                continue
            for field in ("output_rel_path", "txt_output_rel_path", "json_output_rel_path", "srt_output_rel_path", "vtt_output_rel_path"):
                rel_path = result.get(field)
                if not isinstance(rel_path, str) or not rel_path or rel_path in included:
                    continue
                try:
                    file_path = resolve_output_file(rel_path)
                except ValueError:
                    continue
                if not file_path.exists() or not file_path.is_file():
                    continue
                archive.write(file_path, rel_path)
                included.add(rel_path)

    if not included:
        raise TranscriptionError(
            "no_batch_outputs",
            "This batch does not have downloadable output files yet.",
        )

    return buffer.getvalue()


def cleanup_old_batch_jobs():
    now = time.time()
    with BATCH_LOCK:
        expired = []
        for job_id, job in BATCH_JOBS.items():
            if not job.get("finished_at"):
                continue
            try:
                updated_struct = time.strptime(job.get("updated_at", ""), "%Y-%m-%dT%H:%M:%SZ")
                updated_epoch = time.mktime(updated_struct)
            except (TypeError, ValueError):
                updated_epoch = now
            if now - updated_epoch > BATCH_JOB_TTL_SECONDS:
                expired.append(job_id)
        for job_id in expired:
            BATCH_JOBS.pop(job_id, None)
        if expired:
            save_batch_state_locked()


def load_batch_state():
    if not BATCH_STATE_PATH.exists():
        return

    try:
        payload = json.loads(BATCH_STATE_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return

    jobs = payload.get("jobs") if isinstance(payload, dict) else None
    if not isinstance(jobs, dict):
        return

    with BATCH_LOCK:
        BATCH_JOBS.clear()
        for job_id, job in jobs.items():
            if not isinstance(job, dict) or not isinstance(job.get("items"), list):
                continue
            status = str(job.get("status", "queued"))
            if status not in ("finished", "finished_with_errors", "canceled", "interrupted"):
                job["status"] = "interrupted"
                job["finished_at"] = job.get("finished_at") or utc_timestamp()
                job["updated_at"] = job["finished_at"]
                for item in job.get("items", []):
                    if item.get("status") in ("queued", "running"):
                        item["status"] = "canceled"
                        item["label"] = "canceled"
                        item["message"] = "Server restarted before this item finished."
            BATCH_JOBS[str(job_id)] = job
        save_batch_state_locked()


def save_batch_state_locked():
    payload = {
        "schema_version": 1,
        "saved_at": utc_timestamp(),
        "jobs": BATCH_JOBS,
    }
    temp_path = BATCH_STATE_PATH.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(BATCH_STATE_PATH)


def load_settings() -> dict:
    settings = json.loads(json.dumps(DEFAULT_SETTINGS))
    if not LOCAL_SETTINGS_PATH.exists():
        return settings

    try:
        stored = json.loads(LOCAL_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return settings

    if not isinstance(stored, dict):
        return settings

    settings["study_guide_provider"] = sanitize_provider(stored.get("study_guide_provider", "local"))
    settings["study_guide_profile_id"] = sanitize_profile_id(stored.get("study_guide_profile_id", ""))

    model_profiles = stored.get("model_profiles")
    if isinstance(model_profiles, list):
        settings["model_profiles"] = sanitize_model_profiles(model_profiles, [])
    else:
        migrated_profile = legacy_api_provider_to_profile(stored.get("api_provider"))
        if migrated_profile:
            settings["model_profiles"] = [migrated_profile]

    settings["custom_topics"] = sanitize_custom_topics(stored.get("custom_topics", []))
    settings["output_dir"] = sanitize_output_dir(stored.get("output_dir", settings["output_dir"]))
    settings["default_options"] = sanitize_default_options(stored.get("default_options", settings["default_options"]))
    settings["batch_limit"] = sanitize_batch_limit(stored.get("batch_limit", settings["batch_limit"]))
    settings["expand_playlists"] = parse_bool(stored.get("expand_playlists"), settings["expand_playlists"])

    if not any(profile["id"] == settings["study_guide_profile_id"] for profile in settings["model_profiles"]):
        settings["study_guide_profile_id"] = settings["model_profiles"][0]["id"] if settings["model_profiles"] else ""

    return settings


def save_settings_from_payload(payload: dict) -> dict:
    current = load_settings()
    incoming_profiles = payload.get("model_profiles")
    if isinstance(incoming_profiles, list):
        profiles = sanitize_model_profiles(incoming_profiles, current.get("model_profiles", []))
    else:
        profiles = sanitize_model_profiles(
            [legacy_payload_api_provider_to_profile(payload, current)],
            current.get("model_profiles", []),
        )

    for profile in profiles:
        base_url = sanitize_base_url(profile.get("base_url", ""))
        if base_url and not base_url.startswith(("http://", "https://")):
            raise TranscriptionError(
                "invalid_settings",
                "API base URL must start with http:// or https://.",
            )

    current["study_guide_provider"] = sanitize_provider(payload.get("study_guide_provider", current["study_guide_provider"]))
    current["model_profiles"] = profiles
    if isinstance(payload.get("custom_topics"), list):
        current["custom_topics"] = sanitize_custom_topics(payload.get("custom_topics"))
    if "output_dir" in payload:
        current["output_dir"] = sanitize_output_dir(payload.get("output_dir"))
    if "default_options" in payload:
        current["default_options"] = sanitize_default_options(payload.get("default_options"))
    if "batch_limit" in payload:
        current["batch_limit"] = sanitize_batch_limit(payload.get("batch_limit"))
    if "expand_playlists" in payload:
        current["expand_playlists"] = parse_bool(payload.get("expand_playlists"), current.get("expand_playlists", False))
    current["study_guide_profile_id"] = sanitize_profile_id(
        payload.get("study_guide_profile_id", current.get("study_guide_profile_id", ""))
    )

    if not any(profile["id"] == current["study_guide_profile_id"] for profile in profiles):
        current["study_guide_profile_id"] = profiles[0]["id"] if profiles else ""

    temp_path = LOCAL_SETTINGS_PATH.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(LOCAL_SETTINGS_PATH)
    return current


def public_settings(settings: dict) -> dict:
    profiles = [
        {
            "id": profile.get("id", ""),
            "name": profile.get("name", ""),
            "kind": "openai_compatible",
            "base_url": profile.get("base_url", ""),
            "model": profile.get("model", ""),
            "study_guide_max_sources": sanitize_study_guide_max_sources(profile.get("study_guide_max_sources")),
            "study_guide_input_chars": sanitize_study_guide_input_chars(profile.get("study_guide_input_chars")),
            "study_guide_output_tokens": sanitize_study_guide_output_tokens(profile.get("study_guide_output_tokens")),
            "api_key_set": bool(profile.get("api_key", "")),
        }
        for profile in settings.get("model_profiles", [])
        if isinstance(profile, dict)
    ]
    legacy_provider = profiles[0] if profiles else {
        "kind": "openai_compatible",
        "base_url": "https://api.openai.com/v1",
        "model": "",
        "api_key_set": False,
    }
    return {
        "study_guide_provider": sanitize_provider(settings.get("study_guide_provider", "local")),
        "study_guide_profile_id": sanitize_profile_id(settings.get("study_guide_profile_id", "")),
        "model_profiles": profiles,
        "custom_topics": sanitize_custom_topics(settings.get("custom_topics", [])),
        "output_dir": sanitize_output_dir(settings.get("output_dir", "outputs")),
        "resolved_output_dir": str(resolve_output_root(settings.get("output_dir", "outputs"))),
        "default_options": sanitize_default_options(settings.get("default_options", {})),
        "batch_limit": sanitize_batch_limit(settings.get("batch_limit", 10)),
        "expand_playlists": parse_bool(settings.get("expand_playlists"), False),
        "api_provider": legacy_provider,
    }


def sanitize_provider(provider: str | None) -> str:
    clean_provider = str(provider or "local").strip().lower()
    if clean_provider not in ("local", "api"):
        return "local"
    return clean_provider


def sanitize_base_url(base_url: str | None) -> str:
    return str(base_url or "").strip().rstrip("/")


def sanitize_profile_id(profile_id: str | None) -> str:
    value = str(profile_id or "").strip()
    value = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")
    return value[:80]


def sanitize_int_setting(value, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(parsed, maximum))


def sanitize_study_guide_max_sources(value) -> int:
    return sanitize_int_setting(value, API_STUDY_GUIDE_MAX_SOURCES, 1, 24)


def sanitize_study_guide_input_chars(value) -> int:
    return sanitize_int_setting(value, API_STUDY_GUIDE_SOURCE_BUDGET_CHARS, 1000, API_STUDY_GUIDE_MAX_INPUT_CHARS)


def sanitize_study_guide_output_tokens(value) -> int:
    return sanitize_int_setting(value, API_STUDY_GUIDE_MAX_TOKENS, 128, API_STUDY_GUIDE_MAX_OUTPUT_TOKENS)


def sanitize_model_profiles(incoming_profiles: list, current_profiles: list[dict]) -> list[dict]:
    current_by_id = {
        profile.get("id", ""): profile
        for profile in current_profiles
        if isinstance(profile, dict) and profile.get("id")
    }
    profiles = []
    seen_ids = set()

    for index, raw_profile in enumerate(incoming_profiles, start=1):
        if not isinstance(raw_profile, dict):
            continue

        profile_id = sanitize_profile_id(raw_profile.get("id", ""))
        if not profile_id:
            profile_id = f"model-{index}"
        original_profile_id = profile_id
        suffix = 2
        while profile_id in seen_ids:
            profile_id = f"{original_profile_id}-{suffix}"
            suffix += 1
        seen_ids.add(profile_id)

        existing = current_by_id.get(profile_id, {})
        api_key = str(raw_profile.get("api_key", "")).strip()
        name = str(raw_profile.get("name", "")).strip() or f"Model profile {index}"
        profiles.append(
            {
                "id": profile_id,
                "name": name[:120],
                "kind": "openai_compatible",
                "base_url": sanitize_base_url(raw_profile.get("base_url", "")),
                "model": str(raw_profile.get("model", "")).strip(),
                "study_guide_max_sources": sanitize_study_guide_max_sources(raw_profile.get("study_guide_max_sources")),
                "study_guide_input_chars": sanitize_study_guide_input_chars(raw_profile.get("study_guide_input_chars")),
                "study_guide_output_tokens": sanitize_study_guide_output_tokens(raw_profile.get("study_guide_output_tokens")),
                "api_key": api_key if api_key else str(existing.get("api_key", "")).strip(),
            }
        )

    return profiles


def sanitize_output_dir(value: str | None) -> str:
    raw = str(value or "outputs").strip().strip('"')
    if raw == "":
        raw = "outputs"
    return raw


def resolve_output_root(value: str | None = None) -> Path:
    clean_value = sanitize_output_dir(value)
    path = Path(clean_value).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


def get_output_dir() -> Path:
    return resolve_output_root(load_settings().get("output_dir", "outputs"))


def sanitize_default_options(value) -> dict:
    if not isinstance(value, dict):
        value = {}
    defaults = DEFAULT_SETTINGS["default_options"]
    return {
        "include_timestamps": parse_bool(value.get("include_timestamps"), defaults["include_timestamps"]),
        "include_metadata": parse_bool(value.get("include_metadata"), defaults["include_metadata"]),
        "paragraph_mode": parse_bool(value.get("paragraph_mode"), defaults["paragraph_mode"]),
        "generate_study_notes": parse_bool(value.get("generate_study_notes"), defaults["generate_study_notes"]),
    }


def sanitize_batch_limit(value) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = DEFAULT_SETTINGS["batch_limit"]
    return max(1, min(parsed, 25))


def load_custom_topics() -> list[dict]:
    return sanitize_custom_topics(load_settings().get("custom_topics", []))


def sanitize_custom_topics(custom_topics: list | None) -> list[dict]:
    if not isinstance(custom_topics, list):
        return []

    topics = []
    seen = set()
    for raw_topic in custom_topics:
        if not isinstance(raw_topic, dict):
            continue

        topic = sanitize_topic_slug(raw_topic.get("topic", ""))
        if not topic or topic == "other" or topic in seen:
            continue
        seen.add(topic)

        tags = sanitize_tags(raw_topic.get("tags", []))
        if not tags:
            tags = sanitize_tags(topic.split("/"))

        keywords = sanitize_keywords(raw_topic.get("keywords", []))
        if not keywords:
            keywords = sanitize_keywords(tags + topic.split("/"))

        label = str(raw_topic.get("label", "")).strip() or humanize_topic(topic)
        source = str(raw_topic.get("source", "")).strip()[:80] or "user"
        created_at = str(raw_topic.get("created_at", "")).strip() or utc_timestamp()
        topics.append(
            {
                "topic": topic,
                "label": label[:120],
                "tags": tags[:12],
                "keywords": keywords[:24],
                "source": source,
                "created_at": created_at,
            }
        )

    return topics


def sanitize_topic_slug(value: str | None) -> str:
    raw = str(value or "").strip().lower().replace("\\", "/")
    raw = raw.replace("c++", "cpp").replace("cplusplus", "cpp")
    raw = re.sub(r"\s*/\s*", "/", raw)
    parts = [
        re.sub(r"[^a-z0-9_-]+", "-", part).strip("-")
        for part in raw.split("/")
    ]
    parts = [part for part in parts if part]
    return "/".join(parts)[:80]


def sanitize_tags(tags) -> list[str]:
    if not isinstance(tags, list):
        tags = [tags]

    clean_tags = []
    seen = set()
    for tag in tags:
        normalized = re.sub(r"[^a-z0-9_-]+", "-", str(tag).lower()).strip("-")
        if normalized and normalized not in seen:
            seen.add(normalized)
            clean_tags.append(normalized)
    return clean_tags


def sanitize_keywords(keywords) -> list[str]:
    if not isinstance(keywords, list):
        keywords = [keywords]

    clean_keywords = []
    seen = set()
    for keyword in keywords:
        normalized = re.sub(r"\s+", " ", str(keyword).lower()).strip()
        normalized = re.sub(r"[^a-z0-9_+./ -]+", "", normalized).strip(" -")
        if normalized and normalized not in seen:
            seen.add(normalized)
            clean_keywords.append(normalized[:80])
    return clean_keywords


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def legacy_api_provider_to_profile(api_provider: dict | None) -> dict | None:
    if not isinstance(api_provider, dict):
        return None

    base_url = sanitize_base_url(api_provider.get("base_url", ""))
    model = str(api_provider.get("model", "")).strip()
    api_key = str(api_provider.get("api_key", "")).strip()
    if not base_url and not model and not api_key:
        return None

    return {
        "id": "default-api",
        "name": "Configured API model",
        "kind": "openai_compatible",
        "base_url": base_url or "https://api.openai.com/v1",
        "model": model,
        "api_key": api_key,
    }


def legacy_payload_api_provider_to_profile(payload: dict, current: dict) -> dict:
    incoming_api = payload.get("api_provider") if isinstance(payload.get("api_provider"), dict) else {}
    current_profile = current.get("model_profiles", [{}])[0] if current.get("model_profiles") else {}
    return {
        "id": current_profile.get("id", "default-api"),
        "name": current_profile.get("name", "Configured API model"),
        "kind": "openai_compatible",
        "base_url": incoming_api.get("base_url", current_profile.get("base_url", "https://api.openai.com/v1")),
        "model": incoming_api.get("model", current_profile.get("model", "")),
        "api_key": incoming_api.get("api_key", ""),
    }


def select_model_profile(settings: dict, profile_id: str | None = None) -> dict:
    profiles = [profile for profile in settings.get("model_profiles", []) if isinstance(profile, dict)]
    selected_id = sanitize_profile_id(profile_id or settings.get("study_guide_profile_id", ""))
    if selected_id:
        for profile in profiles:
            if profile.get("id") == selected_id:
                return profile

    if profiles:
        return profiles[0]

    raise TranscriptionError(
        "api_provider_not_configured",
        "Add an API model profile in Settings before using an API study guide engine.",
    )


def api_study_guide_source_limit(profile: dict, requested_max_sources: int) -> int:
    profile_max_sources = sanitize_study_guide_max_sources(profile.get("study_guide_max_sources"))
    return min(requested_max_sources, profile_max_sources)


def load_library_entries() -> list[dict]:
    library_path = get_output_dir() / "library.json"
    data = read_library_index(library_path)
    return [decorate_library_entry(entry) for entry in data]


def read_library_index(library_path: Path | None = None) -> list[dict]:
    library_path = library_path or (get_output_dir() / "library.json")
    if not library_path.exists():
        return []

    try:
        data = json.loads(library_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []

    if not isinstance(data, list):
        return []

    return [entry for entry in data if isinstance(entry, dict)]


def rebuild_library_index() -> dict:
    output_root = get_output_dir().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    library_path = output_root / "library.json"
    previous_entries = read_library_index(library_path)
    previous_by_path = {
        str(entry.get("path", "")): entry
        for entry in previous_entries
        if isinstance(entry.get("path"), str) and entry.get("path")
    }

    rebuilt_entries = []
    skipped = []
    for markdown_path in sorted(output_root.rglob("*.md")):
        try:
            rel_path = markdown_path.resolve().relative_to(output_root).as_posix()
        except ValueError:
            continue

        try:
            markdown = markdown_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            skipped.append({"path": rel_path, "reason": "not_utf8"})
            continue

        frontmatter = parse_markdown_frontmatter(markdown)
        previous = previous_by_path.get(rel_path, {})
        if not should_index_markdown(markdown_path, markdown, frontmatter, previous):
            skipped.append({"path": rel_path, "reason": "not_transcript"})
            continue

        rebuilt_entries.append(
            build_rebuilt_library_entry(output_root, markdown_path, markdown, frontmatter, previous)
        )

    rebuilt_entries = dedupe_library_entries(rebuilt_entries)
    rebuilt_entries.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
    write_library_index(library_path, rebuilt_entries)

    rebuilt_paths = {entry.get("path") for entry in rebuilt_entries}
    previous_paths = {entry.get("path") for entry in previous_entries if entry.get("path")}
    return {
        "library_path": str(library_path),
        "entries_count": len(rebuilt_entries),
        "added_count": len(rebuilt_paths - previous_paths),
        "removed_stale_count": len(previous_paths - rebuilt_paths),
        "skipped_count": len(skipped),
        "skipped": skipped[:25],
        "entries": [decorate_library_entry(entry) for entry in rebuilt_entries],
    }


def should_index_markdown(markdown_path: Path, markdown: str, frontmatter: dict, previous_entry: dict) -> bool:
    if previous_entry:
        return True
    if any(frontmatter.get(field) for field in ("url", "video_id", "topic", "language")):
        return True
    if markdown_path.stem.endswith("_transcript"):
        return True
    return bool(re.search(r"(?im)^##\s+Transcript\s*$", markdown))


def build_rebuilt_library_entry(
    output_root: Path,
    markdown_path: Path,
    markdown: str,
    frontmatter: dict,
    previous: dict,
) -> dict:
    rel_path = markdown_path.resolve().relative_to(output_root).as_posix()
    json_metadata = load_json_sidecar_metadata(markdown_path)

    def pick(field: str, default=""):
        for source in (frontmatter, json_metadata, previous):
            value = source.get(field) if isinstance(source, dict) else None
            if value not in (None, ""):
                return value
        return default

    inferred_topic = infer_topic_from_path(output_root, markdown_path)
    topic = sanitize_topic_slug(str(pick("topic", inferred_topic))) or inferred_topic or "other"
    tags = sanitize_tags(pick("tags", []))
    if not tags:
        tags = tags_for_topic(topic, load_custom_topics())

    summary = str(pick("summary", "")).strip()
    if is_placeholder_summary(summary):
        summary = ""
    if not summary:
        summary = extract_markdown_section(markdown, "Summary")
    if is_placeholder_summary(summary):
        summary = ""

    title = str(pick("title", "")).strip() or extract_markdown_title(markdown) or markdown_path.stem
    created_at = str(pick("created_at", "")).strip() or file_mtime_utc(markdown_path)
    language = str(pick("language", "")).strip()

    entry = {
        "schema_version": normalize_schema_version(pick("schema_version", LIBRARY_SCHEMA_VERSION)),
        "title": title,
        "channel": str(pick("channel", "")).strip(),
        "url": str(pick("url", "")).strip(),
        "video_id": str(pick("video_id", "")).strip(),
        "upload_date": str(pick("upload_date", "")).strip(),
        "duration_seconds": parse_number_or_default(pick("duration_seconds", 0), 0),
        "language": language,
        "source": str(pick("source", "")).strip(),
        "track_key": str(pick("track_key", "")).strip(),
        "track_name": str(pick("track_name", "")).strip(),
        "topic": topic,
        "topic_source": str(pick("topic_source", "rebuild")).strip() or "rebuild",
        "tags": tags,
        "segments_count": int(parse_number_or_default(pick("segments_count", 0), 0)),
        "include_timestamps": parse_bool(pick("include_timestamps", True), True),
        "include_metadata": parse_bool(pick("include_metadata", True), True),
        "paragraph_mode": parse_bool(pick("paragraph_mode", False), False),
        "time_range_start_seconds": normalize_optional_number(pick("time_range_start_seconds", None)),
        "time_range_end_seconds": normalize_optional_number(pick("time_range_end_seconds", None)),
        "study_notes_generated": parse_bool(pick("study_notes_generated", False), False),
        "study_notes_provider": str(pick("study_notes_provider", "")).strip(),
        "summary": summary,
        "key_points": normalize_string_list(pick("key_points", [])),
        "highlights": normalize_highlights(pick("highlights", [])),
        "review_questions": normalize_string_list(pick("review_questions", [])),
        "created_at": created_at,
        "path": rel_path,
    }

    for field in ("topic_confidence", "topic_rationale", "topic_provider", "topic_classified_at"):
        value = pick(field, "")
        if value not in (None, ""):
            entry[field] = value

    for key, suffix in (("txt_path", ".txt"), ("json_path", ".json"), ("srt_path", ".srt"), ("vtt_path", ".vtt")):
        sidecar_rel_path = find_sidecar_rel_path(output_root, markdown_path, suffix, previous.get(key, ""))
        if sidecar_rel_path:
            entry[key] = sidecar_rel_path

    return entry


def parse_markdown_frontmatter(markdown: str) -> dict:
    match = re.match(r"\A---\s*\r?\n(.*?)\r?\n---(?:\r?\n|\Z)", markdown, flags=re.DOTALL)
    if not match:
        return {}

    lines = match.group(1).splitlines()
    metadata = {}
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            index += 1
            continue

        key, raw_value = line.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if not key:
            index += 1
            continue

        if raw_value:
            metadata[key] = parse_frontmatter_scalar(raw_value)
            index += 1
            continue

        values = []
        index += 1
        while index < len(lines):
            child_line = lines[index]
            child_match = re.match(r"^\s*-\s*(.*)$", child_line)
            if not child_match:
                break
            values.append(parse_frontmatter_scalar(child_match.group(1).strip()))
            index += 1
        metadata[key] = values

    return metadata


def parse_frontmatter_scalar(value: str):
    lowered = value.lower()
    if lowered == "null":
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False

    try:
        return json.loads(value)
    except json.JSONDecodeError:
        pass

    try:
        if re.match(r"^-?\d+$", value):
            return int(value)
        if re.match(r"^-?\d+\.\d+$", value):
            return float(value)
    except ValueError:
        pass

    return value.strip().strip('"').strip("'")


def load_json_sidecar_metadata(markdown_path: Path) -> dict:
    sidecar_path = markdown_path.with_suffix(".json")
    if not sidecar_path.exists() or not sidecar_path.is_file():
        return {}

    try:
        payload = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}

    if not isinstance(payload, dict):
        return {}

    metadata = payload.get("metadata")
    return metadata if isinstance(metadata, dict) else {}


def infer_topic_from_path(output_root: Path, markdown_path: Path) -> str:
    parent_rel = markdown_path.parent.resolve().relative_to(output_root).as_posix()
    if parent_rel in (".", ""):
        return "other"
    return sanitize_topic_slug(parent_rel) or "other"


def extract_markdown_title(markdown: str) -> str:
    match = re.search(r"(?m)^#\s+(.+?)\s*$", markdown)
    return match.group(1).strip() if match else ""


def extract_markdown_section(markdown: str, heading: str) -> str:
    pattern = rf"(?ims)^##\s+{re.escape(heading)}\s*\n(.*?)(?=^##\s+|\Z)"
    match = re.search(pattern, markdown)
    if not match:
        return ""
    return re.sub(r"\s+", " ", match.group(1)).strip()[:600]


def file_mtime_utc(path: Path) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(path.stat().st_mtime))


def normalize_schema_version(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return LIBRARY_SCHEMA_VERSION


def parse_number_or_default(value, default):
    if value in (None, ""):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if parsed.is_integer():
        return int(parsed)
    return parsed


def normalize_optional_number(value):
    if value in (None, "", "null"):
        return None
    return parse_number_or_default(value, None)


def normalize_string_list(value) -> list[str]:
    if not isinstance(value, list):
        value = [value] if value not in (None, "") else []
    return [str(item).strip() for item in value if str(item).strip()]


def normalize_highlights(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    highlights = []
    for item in value:
        if isinstance(item, dict):
            text = str(item.get("text", "")).strip()
            start = str(item.get("start", "")).strip()
            if text:
                highlights.append({"start": start, "text": text})
        else:
            text = str(item).strip()
            if text:
                highlights.append({"start": "", "text": text})
    return highlights


def find_sidecar_rel_path(output_root: Path, markdown_path: Path, suffix: str, previous_value: str) -> str:
    sidecar_path = markdown_path.with_suffix(suffix)
    if sidecar_path.exists() and sidecar_path.is_file():
        return sidecar_path.resolve().relative_to(output_root).as_posix()
    if previous_value:
        candidate = (output_root / str(previous_value).replace("\\", "/").lstrip("/")).resolve()
        try:
            candidate.relative_to(output_root)
        except ValueError:
            return ""
        if candidate.exists() and candidate.is_file():
            return candidate.relative_to(output_root).as_posix()
    return ""


def dedupe_library_entries(entries: list[dict]) -> list[dict]:
    deduped = {}
    for entry in entries:
        key = str(entry.get("path", ""))
        if not key:
            continue
        deduped[key] = entry
    return list(deduped.values())


def write_library_index(library_path: Path, entries: list[dict]):
    temp_path = library_path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(library_path)


def build_library_study_guide(
    topic: str = "",
    topics: list[str] | None = None,
    max_sources: int = 8,
    provider: str | None = None,
    profile_id: str | None = None,
) -> dict:
    entries = load_library_entries()
    selected_topics = [item for item in (topics or []) if item]
    if selected_topics:
        selected_topic_set = set(selected_topics)
        entries = [entry for entry in entries if entry.get("topic") in selected_topic_set]
    elif topic:
        selected_topics = [topic]
        entries = [entry for entry in entries if entry.get("topic") == topic]

    entries = [entry for entry in entries if isinstance(entry.get("path"), str) and entry.get("path")]
    if not entries:
        raise TranscriptionError(
            "no_library_sources",
            "No library entries match this topic yet. Generate or refresh transcripts first.",
        )

    settings = load_settings()
    selected_provider = sanitize_provider(provider or settings.get("study_guide_provider", "local"))
    profile = None
    source_limit = max_sources
    if selected_provider == "api":
        profile = select_model_profile(settings, profile_id)
        source_limit = api_study_guide_source_limit(profile, max_sources)

    selected_entries = entries[:source_limit]
    source_docs = []
    for entry in selected_entries:
        try:
            file_path = resolve_output_file(entry["path"])
        except ValueError:
            continue
        if not file_path.exists() or not file_path.is_file():
            continue

        try:
            markdown = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        source_docs.append(
            {
                "entry": entry,
                "text": strip_markdown_for_learning(markdown),
            }
        )

    if not source_docs:
        raise TranscriptionError(
            "no_readable_sources",
            "Matching library entries were found, but their Markdown files could not be opened.",
        )

    if selected_provider == "api":
        if profile is None:
            profile = select_model_profile(settings, profile_id)
        guide_text = render_api_library_study_guide(source_docs, topic, profile, selected_topics)
        provider_label = profile.get("name") or profile.get("model") or "configured-api-model"
        provider_profile_id = profile.get("id", "")
        provider_profile_name = profile.get("name", "")
    else:
        guide_text = render_library_study_guide(source_docs, topic, selected_topics)
        provider_label = "local-heuristic-v1"
        provider_profile_id = ""
        provider_profile_name = ""

    label_topic = selected_topics[0] if len(selected_topics) == 1 else topic
    topic_label_text = topic_label(label_topic, load_custom_topics()) if len(selected_topics) <= 1 else f"{len(selected_topics)} selected topics"
    return {
        "topic": topic,
        "topics": selected_topics,
        "topic_label": topic_label_text,
        "provider": selected_provider,
        "provider_label": provider_label,
        "provider_profile_id": provider_profile_id,
        "provider_profile_name": provider_profile_name,
        "sources_count": len(source_docs),
        "guide_text": guide_text,
        "sources": [
            {
                "title": doc["entry"].get("title", "Untitled"),
                "channel": doc["entry"].get("channel", ""),
                "topic": doc["entry"].get("topic", ""),
                "path": doc["entry"].get("path", ""),
                "url": doc["entry"].get("url", ""),
            }
            for doc in source_docs
        ],
    }


def render_library_study_guide(source_docs: list[dict], topic: str, topics: list[str] | None = None) -> str:
    selected_topics = [item for item in (topics or []) if item]
    if len(selected_topics) > 1:
        label = f"{len(selected_topics)} selected topics"
    else:
        label_topic = selected_topics[0] if selected_topics else topic
        label = topic_label(label_topic, load_custom_topics()) if label_topic else "Current library selection"
    summaries = collect_summaries(source_docs)
    key_points = collect_key_points(source_docs)
    questions = collect_review_questions(source_docs)
    terms = collect_terms(source_docs)

    lines = [
        f"# Study Guide: {label}",
        "",
        "Generated locally from the current transcript library. No transcript text was sent to a cloud service.",
        "",
        "## Sources",
        "",
    ]

    for index, doc in enumerate(source_docs, start=1):
        entry = doc["entry"]
        title = entry.get("title") or "Untitled"
        channel = entry.get("channel") or "Unknown channel"
        topic_name = entry.get("topic") or "no topic"
        lines.append(f"{index}. {title} - {channel} ({topic_name})")

    lines.extend(["", "## Short Summary", ""])
    if summaries:
        for summary in summaries[:4]:
            lines.append(f"- {summary}")
    else:
        lines.append("- No existing summaries were found, so this guide uses transcript excerpts and metadata.")

    lines.extend(["", "## Source Notes", ""])
    for index, doc in enumerate(source_docs, start=1):
        entry = doc["entry"]
        title = entry.get("title") or "Untitled"
        source_summary = str(entry.get("summary", "")).strip()
        if not source_summary or is_placeholder_summary(source_summary):
            source_summary = first_sentence(doc["text"]) or "No concise source summary is available yet."
        lines.append(f"{index}. **{title}** - {source_summary}")

    lines.extend(["", "## Key Points", ""])
    for point in key_points[:10]:
        lines.append(f"- {point}")

    lines.extend(["", "## Terms To Review", ""])
    for term in terms[:14]:
        lines.append(f"- {term}")

    lines.extend(["", "## Review Questions", ""])
    for question in questions[:8]:
        lines.append(f"- {question}")

    lines.extend(["", "## Suggested Learning Path", ""])
    for index, doc in enumerate(source_docs, start=1):
        title = doc["entry"].get("title") or "Untitled"
        lines.append(f"{index}. Read or preview `{title}` and capture the concepts you want to reuse.")

    lines.append("")
    return "\n".join(lines)


def render_api_library_study_guide(
    source_docs: list[dict],
    topic: str,
    profile: dict,
    topics: list[str] | None = None,
) -> str:
    base_url = sanitize_base_url(profile.get("base_url", ""))
    model = str(profile.get("model", "")).strip()
    api_key = str(profile.get("api_key", "")).strip()

    if not base_url or not model or not api_key:
        raise TranscriptionError(
            "api_provider_not_configured",
            "Configure API base URL, model, and API key in the selected Settings profile before using the API model.",
        )

    endpoint = chat_completions_endpoint(base_url)
    source_payload = build_api_source_payload(
        source_docs,
        total_excerpt_budget=sanitize_study_guide_input_chars(profile.get("study_guide_input_chars")),
    )
    system_message = (
        "You generate concise Markdown study guides from transcript excerpts. "
        "Return only the final Markdown answer. Do not include reasoning, analysis, or scratchpad text. "
        "Use only the provided sources and do not invent demos, links, claims, or action items. "
        "If the sources cover different topics, keep them separated instead of forcing one unified story. "
        "Include sections: Sources, Short Summary, Source Notes, Cross-Source Themes, "
        "Key Points, Terms To Review, Review Questions, Suggested Next Steps. "
        "Keep the guide compact, practical, and easy for another agent to use as context. "
        "Mention that the guide was generated by the configured API model."
    )
    selected_topics = [item for item in (topics or []) if item]
    if len(selected_topics) > 1:
        topic_context = ", ".join(selected_topics)
    else:
        label_topic = selected_topics[0] if selected_topics else topic
        topic_context = topic_label(label_topic, load_custom_topics()) if label_topic else "Current library selection"
    user_message = (
        f"Topic context: {topic_context}\n\n"
        f"Sources:\n{source_payload}\n\n"
        "Return only Markdown."
    )

    request_body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.2,
        "max_tokens": sanitize_study_guide_output_tokens(profile.get("study_guide_output_tokens")),
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(request_body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")[:500]
        code, user_message = classify_api_provider_http_error(message)
        raise TranscriptionError(
            code,
            user_message,
            message,
        ) from exc
    except Exception as exc:
        raise TranscriptionError(
            "api_provider_error",
            "Could not reach the configured API model. Check Settings and network access.",
            str(exc),
        ) from exc

    content = extract_chat_completion_text(payload).strip()
    if not content:
        raise TranscriptionError(
            "api_provider_empty_response",
            "The configured API model returned an empty response.",
        )

    return content + "\n"


def chat_completions_endpoint(base_url: str) -> str:
    clean_url = sanitize_base_url(base_url)
    if clean_url.endswith("/chat/completions"):
        return clean_url
    return clean_url + "/chat/completions"


def test_model_profile(profile_id: str) -> dict:
    clean_profile_id = sanitize_profile_id(profile_id)
    if not clean_profile_id:
        raise TranscriptionError(
            "missing_profile",
            "Choose a saved model profile before testing the connection.",
        )

    settings = load_settings()
    profile = select_model_profile(settings, clean_profile_id)
    base_url = sanitize_base_url(profile.get("base_url", ""))
    model = str(profile.get("model", "")).strip()
    api_key = str(profile.get("api_key", "")).strip()
    if not base_url or not model or not api_key:
        raise TranscriptionError(
            "api_provider_not_configured",
            "Save API base URL, model, and API key in this Settings profile before testing it.",
        )

    payload = call_chat_completion(
        profile,
        [
            {
                "role": "system",
                "content": "You are a connection test. Return strict JSON only.",
            },
            {
                "role": "user",
                "content": 'Return exactly: {"ok": true, "capability": "chat_completions"}',
            },
        ],
        temperature=0,
        timeout_seconds=30,
    )
    content = extract_chat_completion_text(payload).strip()
    if not content:
        raise TranscriptionError(
            "api_provider_empty_response",
            "The model profile responded, but returned an empty message.",
        )

    json_ok = False
    try:
        parsed = parse_json_object_from_text(content)
        json_ok = parsed.get("ok") is True
    except TranscriptionError:
        json_ok = False

    return {
        "profile_id": profile.get("id", ""),
        "profile_name": profile.get("name", "Model profile"),
        "base_url": base_url,
        "model": model,
        "chat_completions": True,
        "json_response": json_ok,
        "structured_output": "not_checked",
        "message": "Connection test passed. Chat completions responded without sending transcript data.",
    }


def call_chat_completion(
    profile: dict,
    messages: list[dict],
    temperature: float = 0.2,
    timeout_seconds: int = 90,
) -> dict:
    base_url = sanitize_base_url(profile.get("base_url", ""))
    model = str(profile.get("model", "")).strip()
    api_key = str(profile.get("api_key", "")).strip()
    if not base_url or not model or not api_key:
        raise TranscriptionError(
            "api_provider_not_configured",
            "Configure API base URL, model, and API key in the selected Settings profile.",
        )

    request_body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    request = urllib.request.Request(
        chat_completions_endpoint(base_url),
        data=json.dumps(request_body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")[:500]
        raise TranscriptionError(
            "api_provider_error",
            "The configured API model returned an error. Check Settings and try again.",
            message,
        ) from exc
    except Exception as exc:
        raise TranscriptionError(
            "api_provider_error",
            "Could not reach the configured API model. Check Settings and network access.",
            str(exc),
        ) from exc


def build_api_source_payload(source_docs: list[dict], total_excerpt_budget: int = API_STUDY_GUIDE_SOURCE_BUDGET_CHARS) -> str:
    blocks = []
    source_count = max(1, len(source_docs))
    total_budget = sanitize_study_guide_input_chars(total_excerpt_budget)
    per_source_chars = max(API_STUDY_GUIDE_MIN_EXCERPT_CHARS, total_budget // source_count)
    for index, doc in enumerate(source_docs, start=1):
        entry = doc["entry"]
        title = entry.get("title") or "Untitled"
        channel = entry.get("channel") or "Unknown channel"
        source_topic = entry.get("topic") or "no topic"
        summary = str(entry.get("summary", "")).strip()
        tags = format_prompt_tags(entry.get("tags", []))
        excerpt = prompt_excerpt(doc["text"], per_source_chars)
        lines = [
            f"Source {index}: {title}",
            f"Channel: {channel}",
            f"Topic: {source_topic}",
            f"URL: {entry.get('url', '')}",
        ]
        if tags:
            lines.append(f"Tags: {tags}")
        if summary and not is_placeholder_summary(summary):
            lines.extend(["Existing summary:", prompt_excerpt(summary, 500)])
        lines.extend(
            [
                "Transcript excerpt:",
                excerpt,
            ]
        )
        blocks.append(
            "\n".join(lines)
        )
    return "\n\n---\n\n".join(blocks)


def format_prompt_tags(tags) -> str:
    if not isinstance(tags, list):
        return ""
    clean_tags = [str(tag).strip() for tag in tags if str(tag).strip()]
    return ", ".join(clean_tags[:12])


def prompt_excerpt(text: str, max_chars: int) -> str:
    clean_text = re.sub(r"\s+", " ", str(text or "")).strip()
    if max_chars <= 0 or len(clean_text) <= max_chars:
        return clean_text

    cutoff = max_chars
    sentence_cutoff = max(
        clean_text.rfind(". ", 0, max_chars),
        clean_text.rfind("? ", 0, max_chars),
        clean_text.rfind("! ", 0, max_chars),
    )
    if sentence_cutoff >= int(max_chars * 0.55):
        cutoff = sentence_cutoff + 1

    return clean_text[:cutoff].rstrip() + "..."


def classify_api_provider_http_error(message: str) -> tuple[str, str]:
    clean_message = str(message or "")
    lower_message = clean_message.lower()
    context_markers = (
        "context length",
        "context size",
        "n_ctx",
        "n_keep",
        "too many tokens",
        "maximum context",
        "token limit",
    )
    if any(marker in lower_message for marker in context_markers):
        return (
            "api_provider_context_limit",
            "The selected API model context window is too small for the selected sources. Try fewer topics, fewer sources, or load the model with a larger context window.",
        )

    return (
        "api_provider_error",
        "The configured API model returned an error. Check Settings and try again.",
    )


def extract_chat_completion_text(payload: dict) -> str:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        first_choice = choices[0] if isinstance(choices[0], dict) else {}
        message = first_choice.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        parts.append(str(item.get("text", "")))
                return "\n".join(parts)

    output = payload.get("output")
    if isinstance(output, list):
        parts = []
        for item in output:
            if not isinstance(item, dict):
                continue
            for content_item in item.get("content", []):
                if isinstance(content_item, dict) and content_item.get("type") in ("output_text", "text"):
                    parts.append(str(content_item.get("text", "")))
        return "\n".join(parts)

    return ""


def strip_markdown_for_learning(markdown: str) -> str:
    transcript_match = re.search(r"(?im)^##\s+Transcript\s*$", markdown)
    if transcript_match:
        markdown = markdown[transcript_match.end() :]

    text = re.sub(r"(?s)^---\s.*?\s---", " ", markdown.strip())
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"Summary has not been generated yet\.", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"Santrauka dar nesugeneruota\.", " ", text, flags=re.IGNORECASE)
    text = re.sub(
        r"(?im)^\s*#+\s*(summary|key topics|transcript|study notes|key points|highlights|review questions)\s*$",
        " ",
        text,
    )
    text = re.sub(r"^#+\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^\s*[-*]\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\[[0-9:.]+\]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def collect_summaries(source_docs: list[dict]) -> list[str]:
    summaries = []
    for doc in source_docs:
        summary = str(doc["entry"].get("summary", "")).strip()
        if summary and not is_placeholder_summary(summary):
            summaries.append(summary)
            continue

        excerpt = first_sentence(doc["text"])
        if excerpt:
            summaries.append(excerpt)

    return unique_items(summaries)


def is_placeholder_summary(summary: str) -> bool:
    normalized = summary.lower()
    return (
        "summary has not been generated yet" in normalized
        or "santrauka dar nesugeneruota" in normalized
    )


def collect_key_points(source_docs: list[dict]) -> list[str]:
    points = []
    signal_words = (
        "important",
        "because",
        "therefore",
        "workflow",
        "problem",
        "solution",
        "strategy",
        "risk",
        "example",
        "step",
        "build",
        "learn",
        "understand",
    )

    for doc in source_docs:
        existing_points = doc["entry"].get("key_points")
        if isinstance(existing_points, list):
            points.extend(str(point).strip() for point in existing_points if str(point).strip())

        for sentence in split_sentences(doc["text"]):
            lowered = sentence.lower()
            if len(sentence) >= 60 and any(word in lowered for word in signal_words):
                points.append(sentence)

    if not points:
        for doc in source_docs:
            points.extend(split_sentences(doc["text"])[:2])

    return unique_items(points)


def collect_review_questions(source_docs: list[dict]) -> list[str]:
    questions = []
    for doc in source_docs:
        existing_questions = doc["entry"].get("review_questions")
        if isinstance(existing_questions, list):
            questions.extend(str(question).strip() for question in existing_questions if str(question).strip())

    questions.extend(
        [
            "What are the most reusable ideas across these sources?",
            "Which source should be reviewed first before applying this topic?",
            "What decisions, risks, or examples appear repeatedly?",
        ]
    )
    return unique_items(questions)


def collect_terms(source_docs: list[dict]) -> list[str]:
    stop_words = {
        "about",
        "after",
        "also",
        "because",
        "before",
        "being",
        "between",
        "could",
        "every",
        "first",
        "from",
        "have",
        "into",
        "just",
        "like",
        "more",
        "most",
        "only",
        "other",
        "should",
        "that",
        "their",
        "there",
        "these",
        "thing",
        "this",
        "those",
        "through",
        "what",
        "when",
        "where",
        "which",
        "with",
        "would",
        "your",
    }
    counter = Counter()
    for doc in source_docs:
        tags = doc["entry"].get("tags")
        if isinstance(tags, list):
            counter.update(str(tag).lower() for tag in tags if str(tag).strip())
        words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{3,}", doc["text"].lower())
        counter.update(word for word in words if word not in stop_words)

    return [term for term, _ in counter.most_common(20)]


def split_sentences(text: str) -> list[str]:
    sentences = re.split(r"(?<=[.!?])\s+", text)
    return [sentence.strip()[:260] for sentence in sentences if sentence.strip()]


def first_sentence(text: str) -> str:
    sentences = split_sentences(text)
    return sentences[0] if sentences else ""


def unique_items(items: list[str]) -> list[str]:
    seen = set()
    unique = []
    for item in items:
        normalized = re.sub(r"\s+", " ", item).strip()
        key = normalized.lower()
        if normalized and key not in seen:
            seen.add(key)
            unique.append(normalized)
    return unique


def classify_library_entry_topic(
    requested_path: str,
    provider: str | None = None,
    profile_id: str | None = None,
) -> dict:
    if requested_path == "":
        raise TranscriptionError(
            "missing_path",
            "Choose a library entry before classifying its topic.",
        )

    entry = find_library_entry(requested_path)
    if entry is None:
        raise TranscriptionError(
            "library_entry_not_found",
            "This library entry could not be found. Refresh the library and try again.",
        )

    try:
        file_path = resolve_output_file(str(entry.get("path", requested_path)))
    except ValueError as exc:
        raise TranscriptionError(
            "invalid_path",
            "The library file path is invalid.",
        ) from exc

    if not file_path.exists() or not file_path.is_file():
        raise TranscriptionError(
            "file_not_found",
            "The Markdown file for this library entry is missing.",
        )

    markdown = file_path.read_text(encoding="utf-8")
    source_doc = {
        "entry": entry,
        "text": strip_markdown_for_learning(markdown),
    }
    settings = load_settings()
    selected_provider = sanitize_provider(provider or "api")

    if selected_provider == "api":
        profile = select_model_profile(settings, profile_id)
        raw_classification = render_api_topic_classification(
            source_doc,
            profile,
            list_topic_options(settings.get("custom_topics", [])),
        )
        provider_label = profile.get("name") or profile.get("model") or "configured-api-model"
    else:
        raw_classification = render_local_topic_classification(source_doc, settings.get("custom_topics", []))
        provider_label = "local-heuristic-v1"

    classification = normalize_topic_classification(raw_classification, settings.get("custom_topics", []))
    updated_settings = ensure_custom_topic(settings, classification)
    updated_entry = update_library_entry_metadata(entry.get("path", requested_path), classification, provider_label)
    update_markdown_learning_metadata(file_path, classification)
    update_json_sidecar_metadata(updated_entry, classification)

    return {
        "entry": decorate_library_entry(updated_entry),
        "classification": classification,
        "provider": selected_provider,
        "provider_label": provider_label,
        "topics": list_topic_options(updated_settings.get("custom_topics", [])),
    }


def find_library_entry(requested_path: str) -> dict | None:
    for entry in load_library_entries():
        if entry.get("path") == requested_path:
            return entry
    return None


def render_local_topic_classification(source_doc: dict, custom_topics: list[dict]) -> dict:
    entry = source_doc["entry"]
    result = classify_topic(
        {"title": entry.get("title", ""), "channel": entry.get("channel", "")},
        [(0.0, source_doc.get("text", ""))],
        custom_topics,
    )
    topic = result.get("topic", "other")
    return {
        "topic": topic,
        "label": topic_label(topic, custom_topics),
        "tags": result.get("tags", tags_for_topic(topic, custom_topics)),
        "confidence": 0.45 if topic == "other" else 0.7,
        "summary": first_sentence(source_doc.get("text", "")),
        "rationale": "Matched local topic keywords." if topic != "other" else "No strong local keyword match was found.",
        "keywords": result.get("matched_keywords", []),
    }


def render_api_topic_classification(source_doc: dict, profile: dict, topic_options: list[dict]) -> dict:
    base_url = sanitize_base_url(profile.get("base_url", ""))
    model = str(profile.get("model", "")).strip()
    api_key = str(profile.get("api_key", "")).strip()

    if not base_url or not model or not api_key:
        raise TranscriptionError(
            "api_provider_not_configured",
            "Configure API base URL, model, and API key in the selected Settings profile before classifying topics.",
        )

    entry = source_doc["entry"]
    allowed_topics = build_topic_catalog_for_prompt(topic_options, load_settings().get("custom_topics", []))
    system_message = (
        "You classify one transcript into a knowledge-library topic. "
        "Prefer an allowed topic when it fits, even if the wording is not exact. "
        "Return the exact allowed topic value for near matches, for example use ai/agents instead of agent, agents, or agentic-ai. "
        "Create a new stable topic slug only when none of the allowed topics is semantically appropriate. "
        "Do not create singular/plural, spelling, capitalization, or naming-style variants of existing topics. "
        "Return strict JSON only, with keys: topic, label, tags, confidence, summary, rationale, keywords. "
        "Use lowercase topic slugs like programming/javascript or creative-coding. "
        "Keep summary under 45 words and rationale under 35 words."
    )
    user_message = "\n".join(
        [
            "Allowed topics:",
            allowed_topics,
            "",
            "Topic decision rule: first try to reuse one exact allowed topic value. Only propose a new topic if a human would clearly want a new library folder.",
            "",
            f"Title: {entry.get('title', 'Untitled')}",
            f"Channel: {entry.get('channel', 'Unknown channel')}",
            f"Current topic: {entry.get('topic', 'other')}",
            f"URL: {entry.get('url', '')}",
            "",
            "Transcript excerpt:",
            source_doc.get("text", "")[:6000],
        ]
    )

    request_body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_message},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.1,
    }
    request = urllib.request.Request(
        chat_completions_endpoint(base_url),
        data=json.dumps(request_body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        message = exc.read().decode("utf-8", errors="replace")[:500]
        raise TranscriptionError(
            "api_provider_error",
            "The configured API model returned an error while classifying the topic.",
            message,
        ) from exc
    except Exception as exc:
        raise TranscriptionError(
            "api_provider_error",
            "Could not reach the configured API model while classifying the topic.",
            str(exc),
        ) from exc

    content = extract_chat_completion_text(payload).strip()
    if not content:
        raise TranscriptionError(
            "api_provider_empty_response",
            "The configured API model returned an empty topic classification.",
        )

    return parse_topic_classification_content(content)


def parse_topic_classification_content(content: str) -> dict:
    parsed = parse_json_object_from_text(content)
    validate_topic_classification_schema(parsed)
    return parsed


def parse_json_object_from_text(content: str) -> dict:
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not match:
            raise TranscriptionError(
                "api_provider_invalid_response",
                "The configured API model did not return valid topic JSON.",
            )
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            raise TranscriptionError(
                "api_provider_invalid_response",
                "The configured API model did not return valid topic JSON.",
            ) from exc

    if not isinstance(parsed, dict):
        raise TranscriptionError(
            "api_provider_invalid_response",
            "The configured API model did not return a topic object.",
        )

    return parsed


def validate_topic_classification_schema(parsed: dict):
    required_fields = ("topic", "label", "tags", "confidence", "summary", "rationale", "keywords")
    missing_fields = [field for field in required_fields if field not in parsed]
    if missing_fields:
        raise TranscriptionError(
            "api_provider_invalid_response",
            "The configured API model returned topic JSON with missing fields: " + ", ".join(missing_fields) + ".",
        )

    if not isinstance(parsed.get("topic"), str) or not parsed["topic"].strip():
        raise TranscriptionError(
            "api_provider_invalid_response",
            "The configured API model returned topic JSON with an invalid topic.",
        )

    if not isinstance(parsed.get("label"), str):
        raise TranscriptionError(
            "api_provider_invalid_response",
            "The configured API model returned topic JSON with an invalid label.",
        )

    if not isinstance(parsed.get("tags"), list) or not all(isinstance(item, str) for item in parsed["tags"]):
        raise TranscriptionError(
            "api_provider_invalid_response",
            "The configured API model returned topic JSON with invalid tags.",
        )

    if not isinstance(parsed.get("keywords"), list) or not all(isinstance(item, str) for item in parsed["keywords"]):
        raise TranscriptionError(
            "api_provider_invalid_response",
            "The configured API model returned topic JSON with invalid keywords.",
        )

    try:
        float(parsed.get("confidence"))
    except (TypeError, ValueError) as exc:
        raise TranscriptionError(
            "api_provider_invalid_response",
            "The configured API model returned topic JSON with invalid confidence.",
        ) from exc

    for field in ("summary", "rationale"):
        if not isinstance(parsed.get(field), str):
            raise TranscriptionError(
                "api_provider_invalid_response",
                f"The configured API model returned topic JSON with invalid {field}.",
            )


def normalize_topic_classification(classification: dict, custom_topics: list[dict]) -> dict:
    proposed_topic = sanitize_topic_slug(classification.get("topic", ""))
    proposed_label = str(classification.get("label", "")).strip()
    proposed_tags = sanitize_tags(classification.get("tags", []))
    topic = canonical_topic_slug(proposed_topic, proposed_label, proposed_tags, custom_topics)
    if not topic:
        topic = "other"

    tags = proposed_tags
    if not tags:
        tags = tags_for_topic(topic, custom_topics)

    try:
        confidence = float(classification.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(confidence, 1.0))

    label = str(classification.get("label", "")).strip() or topic_label(topic, custom_topics)
    summary = re.sub(r"\s+", " ", str(classification.get("summary", "")).strip())[:600]
    rationale = re.sub(r"\s+", " ", str(classification.get("rationale", "")).strip())[:400]
    keywords = sanitize_keywords(classification.get("keywords", []))

    return {
        "topic": topic,
        "label": label[:120],
        "tags": tags[:12],
        "confidence": round(confidence, 3),
        "summary": summary,
        "rationale": rationale,
        "keywords": keywords[:24],
        "classified_at": utc_timestamp(),
    }


def build_topic_catalog_for_prompt(topic_options: list[dict], custom_topics: list[dict]) -> str:
    custom_by_topic = {
        topic.get("topic", ""): topic
        for topic in sanitize_custom_topics(custom_topics)
        if topic.get("topic")
    }
    lines = []
    for option in topic_options:
        value = str(option.get("value", "")).strip()
        if not value:
            continue
        label = str(option.get("label", "")).strip() or value
        custom = custom_by_topic.get(value, {})
        extras = []
        if custom.get("tags"):
            extras.append("tags: " + ", ".join(custom["tags"][:8]))
        if custom.get("keywords"):
            extras.append("known keywords: " + ", ".join(custom["keywords"][:10]))
        suffix = " (" + "; ".join(extras) + ")" if extras else ""
        lines.append(f"- {value}: {label}{suffix}")
    return "\n".join(lines)


def canonical_topic_slug(
    proposed_topic: str,
    proposed_label: str,
    proposed_tags: list[str],
    custom_topics: list[dict],
) -> str:
    if not proposed_topic:
        return ""

    topic_options = list_topic_options(custom_topics)
    allowed_values = {option["value"] for option in topic_options}
    if proposed_topic in allowed_values:
        return proposed_topic

    proposed_aliases = topic_aliases(
        proposed_topic,
        proposed_label,
        [],
        [],
    )
    for option in topic_options:
        value = option["value"]
        aliases = topic_aliases(value, option.get("label", ""), [], [])
        if proposed_aliases & aliases:
            return value

    proposed_parts = set(topic_parts_from_slug(proposed_topic))
    for option in topic_options:
        value = option["value"]
        option_parts = set(topic_parts_from_slug(value))
        if proposed_parts and proposed_parts <= option_parts:
            return value

    return proposed_topic


def topic_aliases(topic: str, label: str, tags: list[str], keywords: list[str]) -> set[str]:
    raw_values = [topic, label, *tags, *keywords]
    raw_values.extend(topic_parts_from_slug(topic))
    aliases = set()
    for value in raw_values:
        normalized = normalize_topic_alias(value)
        if normalized:
            aliases.add(normalized)
            if normalized.endswith("s"):
                aliases.add(normalized[:-1])
    return aliases


def normalize_topic_alias(value: str) -> str:
    normalized = str(value or "").lower()
    normalized = normalized.replace("c++", "cpp").replace("cplusplus", "cpp")
    normalized = re.sub(r"[^a-z0-9]+", "", normalized)
    return normalized


def topic_parts_from_slug(topic: str) -> list[str]:
    return [part for part in re.split(r"[/_-]+", topic) if part]


def ensure_custom_topic(settings: dict, classification: dict) -> dict:
    topic = classification["topic"]
    if topic == "other":
        return settings

    custom_topics = sanitize_custom_topics(settings.get("custom_topics", []))
    merged = False
    for custom_topic in custom_topics:
        if custom_topic.get("topic") != topic:
            continue
        custom_topic["tags"] = sanitize_tags(custom_topic.get("tags", []) + classification.get("tags", []))
        custom_topic["keywords"] = sanitize_keywords(
            custom_topic.get("keywords", [])
            + classification.get("keywords", [])
            + classification.get("tags", [])
        )
        if classification.get("label"):
            custom_topic["label"] = classification["label"]
        custom_topic["source"] = "api-classification"
        merged = True
        break

    if not merged:
        custom_topics.append(
            {
                "topic": topic,
                "label": classification.get("label") or humanize_topic(topic),
                "tags": classification.get("tags") or topic.split("/"),
                "keywords": classification.get("keywords") or classification.get("tags") or topic.split("/"),
                "source": "api-classification",
                "created_at": classification.get("classified_at") or utc_timestamp(),
            }
        )

    settings["custom_topics"] = sanitize_custom_topics(custom_topics)
    temp_path = LOCAL_SETTINGS_PATH.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(LOCAL_SETTINGS_PATH)
    return settings


def update_library_entry_metadata(requested_path: str, classification: dict, provider_label: str) -> dict:
    library_path = get_output_dir() / "library.json"
    if not library_path.exists():
        raise TranscriptionError(
            "library_missing",
            "The library index is missing. Refresh or generate a transcript first.",
        )

    try:
        data = json.loads(library_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TranscriptionError(
            "library_invalid",
            "The library index could not be read.",
        ) from exc

    if not isinstance(data, list):
        raise TranscriptionError(
            "library_invalid",
            "The library index is invalid.",
        )

    updated_entry = None
    for entry in data:
        if not isinstance(entry, dict) or entry.get("path") != requested_path:
            continue

        entry["topic"] = classification["topic"]
        entry["topic_source"] = "ai"
        entry["tags"] = classification["tags"]
        entry["topic_confidence"] = classification["confidence"]
        entry["topic_rationale"] = classification["rationale"]
        entry["topic_provider"] = provider_label
        entry["topic_classified_at"] = classification["classified_at"]
        if classification.get("summary"):
            entry["summary"] = classification["summary"]
        updated_entry = entry
        break

    if updated_entry is None:
        raise TranscriptionError(
            "library_entry_not_found",
            "This library entry could not be found. Refresh the library and try again.",
        )

    temp_path = library_path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(library_path)
    return updated_entry


def update_markdown_learning_metadata(file_path: Path, classification: dict):
    try:
        markdown = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return

    markdown = replace_frontmatter_scalar(markdown, "topic", classification["topic"])
    markdown = replace_frontmatter_scalar(markdown, "topic_source", "ai")
    markdown = replace_frontmatter_list(markdown, "tags", classification["tags"])
    if classification.get("summary"):
        markdown = replace_markdown_section(markdown, "Summary", classification["summary"])
    markdown = replace_markdown_section(
        markdown,
        "Key Topics",
        "\n".join(f"- {tag}" for tag in classification["tags"]),
    )
    file_path.write_text(markdown, encoding="utf-8")


def replace_frontmatter_scalar(markdown: str, key: str, value: str) -> str:
    pattern = rf"(?m)^({re.escape(key)}:\s*).*$"
    replacement = rf"\1{json.dumps(value, ensure_ascii=False)}"
    if re.search(pattern, markdown):
        return re.sub(pattern, replacement, markdown, count=1)
    return insert_frontmatter_field(markdown, f"{key}: {json.dumps(value, ensure_ascii=False)}")


def replace_frontmatter_list(markdown: str, key: str, values: list[str]) -> str:
    block = key + ":\n" + "\n".join(f"  - {json.dumps(value, ensure_ascii=False)}" for value in values)
    pattern = rf"(?m)^{re.escape(key)}:\s*\r?\n(?:[ \t]+- .*(?:\r?\n|$))+"
    if re.search(pattern, markdown):
        return re.sub(pattern, block + "\n", markdown, count=1)
    return insert_frontmatter_field(markdown, block)


def insert_frontmatter_field(markdown: str, field: str) -> str:
    if not markdown.startswith("---"):
        return markdown
    end_match = re.search(r"(?m)^---\s*$", markdown[3:])
    if not end_match:
        return markdown
    end_index = end_match.start() + 3
    return markdown[:end_index].rstrip() + "\n" + field + "\n" + markdown[end_index:]


def replace_markdown_section(markdown: str, heading: str, body: str) -> str:
    pattern = rf"(?ims)(^##\s+{re.escape(heading)}\s*\n)(.*?)(?=^##\s+|\Z)"
    replacement = "\\1\n" + body.strip() + "\n\n"
    if re.search(pattern, markdown):
        return re.sub(pattern, replacement, markdown, count=1)
    return markdown


def update_json_sidecar_metadata(entry: dict, classification: dict):
    json_path = entry.get("json_path")
    if not json_path:
        return

    try:
        file_path = resolve_output_file(str(json_path))
    except ValueError:
        return
    if not file_path.exists() or not file_path.is_file():
        return

    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return

    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        return

    metadata["topic"] = classification["topic"]
    metadata["topic_source"] = "ai"
    metadata["tags"] = classification["tags"]
    metadata["topic_confidence"] = classification["confidence"]
    metadata["topic_rationale"] = classification["rationale"]
    metadata["topic_classified_at"] = classification["classified_at"]
    if classification.get("summary"):
        metadata["summary"] = classification["summary"]

    temp_path = file_path.with_suffix(".json.tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(file_path)


def topic_label(topic: str, custom_topics: list[dict] | None = None) -> str:
    if not topic:
        return "All topics"

    for option in list_topic_options(custom_topics):
        if option["value"] == topic:
            return option["label"]

    return topic


def decorate_library_entry(entry: dict) -> dict:
    decorated = dict(entry)
    downloads = {}

    path_fields = {
        "md": "path",
        "txt": "txt_path",
        "json": "json_path",
        "srt": "srt_path",
        "vtt": "vtt_path",
    }
    for key, field in path_fields.items():
        rel_path = decorated.get(field)
        if isinstance(rel_path, str) and rel_path and output_file_exists(rel_path):
            downloads[key] = output_download_url(rel_path)

    decorated["downloads"] = downloads
    return decorated


def output_download_url(rel_path: str) -> str:
    return "/outputs/" + quote(rel_path.replace("\\", "/"), safe="/")


def add_download_urls(result: dict) -> dict:
    path_fields = [
        ("output_rel_path", "download_url"),
        ("txt_output_rel_path", "txt_download_url"),
        ("json_output_rel_path", "json_download_url"),
        ("srt_output_rel_path", "srt_download_url"),
        ("vtt_output_rel_path", "vtt_download_url"),
    ]
    for source_field, target_field in path_fields:
        rel_path = result.get(source_field)
        if isinstance(rel_path, str) and rel_path:
            result[target_field] = output_download_url(rel_path)
    return result


def resolve_output_file(rel_path: str) -> Path:
    normalized = rel_path.replace("\\", "/").lstrip("/")
    output_root = get_output_dir().resolve()
    candidate = (output_root / normalized).resolve()
    try:
        candidate.relative_to(output_root)
    except ValueError as exc:
        raise ValueError("Path escapes output directory") from exc
    if candidate.suffix.lower() not in TEXT_LIBRARY_EXTENSIONS:
        raise ValueError("Unsupported library file extension")
    return candidate


def to_output_relative_path(path: Path) -> str:
    return path.resolve().relative_to(get_output_dir().resolve()).as_posix()


def output_file_exists(rel_path: str) -> bool:
    try:
        path = resolve_output_file(rel_path)
    except ValueError:
        return False
    return path.exists() and path.is_file()


def register_session(client_id: str):
    global ZERO_SESSION_SINCE

    now = time.monotonic()
    with SESSION_LOCK:
        CLIENT_SESSIONS[client_id] = now
        ZERO_SESSION_SINCE = None


def close_session(client_id: str):
    global ZERO_SESSION_SINCE

    now = time.monotonic()
    with SESSION_LOCK:
        CLIENT_SESSIONS.pop(client_id, None)
        if not CLIENT_SESSIONS:
            ZERO_SESSION_SINCE = now


def shutdown_when_idle():
    while True:
        time.sleep(2)
        cleanup_expired_sessions()
        with SESSION_LOCK:
            if ZERO_SESSION_SINCE is None:
                continue
            empty_for = time.monotonic() - ZERO_SESSION_SINCE

        if empty_for >= SHUTDOWN_AFTER_EMPTY_SECONDS:
            server = SERVER_INSTANCE
            if server is not None:
                threading.Thread(target=server.shutdown, daemon=True).start()
            break


def main():
    global SERVER_INSTANCE

    load_batch_state()
    get_output_dir().mkdir(parents=True, exist_ok=True)
    server = ReusableThreadingHTTPServer((HOST, PORT), AppHandler)
    SERVER_INSTANCE = server
    if not DISABLE_IDLE_SHUTDOWN:
        threading.Thread(target=shutdown_when_idle, daemon=True).start()
    print(f"Open in browser: http://{HOST}:{PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
