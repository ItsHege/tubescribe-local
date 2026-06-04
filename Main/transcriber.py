from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

from yt_dlp import YoutubeDL


DEFAULT_LANG_PRIORITY = [
    "en-orig",
    "en",
    "lt",
    "lt-orig",
]

LIBRARY_SCHEMA_VERSION = 2
MAX_CAPTION_JSON_BYTES = 20 * 1024 * 1024
MAX_CAPTION_SEGMENTS = 100000
MAX_TRANSCRIPT_CHARS = 5 * 1024 * 1024
MAX_SIDECAR_CHARS = 25 * 1024 * 1024

TOPIC_RULES = [
    {
        "topic": "programming/cpp",
        "tags": ["programming", "cpp"],
        "keywords": [
            "c++",
            "cpp",
            "cplusplus",
            "clang",
            "gcc",
            "templates",
            "pointers",
            "memory management",
            "std::",
        ],
    },
    {
        "topic": "programming/javascript",
        "tags": ["programming", "javascript"],
        "keywords": [
            "javascript",
            "typescript",
            "node.js",
            "nodejs",
            "react",
            "vue",
            "svelte",
            "npm",
            "frontend",
        ],
    },
    {
        "topic": "ai/agents",
        "tags": ["ai", "agents"],
        "keywords": [
            "agent",
            "agents",
            "agentic",
            "ai agent",
            "ai agents",
            "tool use",
            "workflow",
            "workflows",
            "multi-agent",
            "coding agent",
            "agentic ai",
            "autonomous agent",
            "autonomous agents",
            "pomdp",
            "reflection",
            "action policy",
            "multi-agent systems",
            "agenttools",
            "agent tools",
            "engineering productivity",
            "devtools",
        ],
    },
    {
        "topic": "ai/rag",
        "tags": ["ai", "rag"],
        "keywords": [
            "rag",
            "retrieval",
            "vector database",
            "embeddings",
            "semantic search",
            "knowledge base",
            "chunking",
        ],
    },
    {
        "topic": "finance/investing",
        "tags": ["finance", "investing"],
        "keywords": [
            "investing",
            "investment",
            "stocks",
            "stock market",
            "portfolio",
            "etf",
            "dividend",
            "finance",
            "bitcoin",
            "crypto",
        ],
    },
    {
        "topic": "psychology",
        "tags": ["psychology"],
        "keywords": [
            "psychology",
            "therapy",
            "mental health",
            "habit",
            "habits",
            "motivation",
            "behavior",
            "cognitive",
        ],
    },
    {
        "topic": "blender",
        "tags": ["blender", "3d"],
        "keywords": [
            "blender",
            "geometry nodes",
            "modeling",
            "rigging",
            "shader",
            "3d",
            "sculpting",
        ],
    },
    {
        "topic": "game-dev",
        "tags": ["game-dev"],
        "keywords": [
            "game dev",
            "gamedev",
            "game development",
            "unity",
            "godot",
            "unreal",
            "procedural generation",
            "shader",
            "vfx",
            "minecraft",
            "simulation",
            "simulator",
            "spell simulator",
            "visual programming",
            "canvas",
            "game-development",
        ],
    },
    {
        "topic": "productivity",
        "tags": ["productivity"],
        "keywords": [
            "productivity",
            "focus",
            "time management",
            "workflow",
            "notion",
            "obsidian",
            "zettelkasten",
        ],
    },
]

TOPIC_LABELS = {
    "programming/cpp": "Programming / C++",
    "programming/javascript": "Programming / JavaScript",
    "ai/agents": "AI / Agents",
    "ai/rag": "AI / RAG",
    "finance/investing": "Finance / Investing",
    "psychology": "Psychology",
    "blender": "Blender",
    "game-dev": "Game dev",
    "productivity": "Productivity",
    "other": "Other",
}


class TranscriptionError(Exception):
    def __init__(self, code: str, user_message: str, technical_message: str | None = None):
        super().__init__(technical_message or user_message)
        self.code = code
        self.user_message = user_message
        self.technical_message = technical_message or user_message


@dataclass
class SubtitleTrack:
    key: str
    lang: str
    ext: str
    name: str
    url: str
    source: str


def transcribe_url(
    url: str,
    output_dir: str | Path,
    track_key: str | None = None,
    topic_override: str | None = None,
    include_timestamps: bool = True,
    include_metadata: bool = True,
    paragraph_mode: bool = False,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
    generate_study_notes: bool = False,
    custom_topics: list[dict] | None = None,
) -> dict:
    clean_url = (url or "").strip()
    if clean_url == "":
        raise TranscriptionError(
            "missing_url",
            "Paste a YouTube URL and try again.",
        )

    validate_topic_override(topic_override, custom_topics)
    validate_time_range(start_seconds, end_seconds)

    info = get_video_info(clean_url)
    track = choose_track(info, track_key=track_key)
    caption_json = fetch_json(track.url)
    segments = clean_segments(extract_segments(caption_json))
    if not segments:
        raise TranscriptionError(
            "empty_subtitles",
            "A caption file was found, but the text is empty. Try another video.",
        )

    segments = filter_segments_by_time_range(segments, start_seconds, end_seconds)
    if not segments:
        raise TranscriptionError(
            "empty_time_range",
            "No caption text was found in the selected time range.",
        )

    output_root = Path(output_dir).resolve()
    output_root.mkdir(parents=True, exist_ok=True)

    topic_data = resolve_topic(info, segments, topic_override, custom_topics)
    topic = topic_data["topic"]
    tags = topic_data["tags"]
    topic_source = topic_data["topic_source"]
    study_notes = build_study_notes(info, segments, topic_data) if generate_study_notes else empty_study_notes()
    topic_output_dir = build_topic_output_dir(output_root, topic)
    topic_output_dir.mkdir(parents=True, exist_ok=True)

    base_name = safe_filename_stem(info.get("title", ""), info["id"], track.lang)
    markdown_path = topic_output_dir / f"{base_name}.md"
    txt_path = topic_output_dir / f"{base_name}.txt"
    json_path = topic_output_dir / f"{base_name}.json"
    srt_path = topic_output_dir / f"{base_name}.srt"
    vtt_path = topic_output_dir / f"{base_name}.vtt"
    for output_path in (markdown_path, txt_path, json_path, srt_path, vtt_path):
        validate_output_path(output_root, output_path)
    created_at = utc_now_iso()

    metadata = {
        "schema_version": LIBRARY_SCHEMA_VERSION,
        "title": info.get("title", ""),
        "channel": info.get("channel", ""),
        "url": info.get("webpage_url") or info.get("original_url") or clean_url,
        "video_id": info["id"],
        "upload_date": info.get("upload_date", ""),
        "duration_seconds": info.get("duration", 0),
        "language": track.lang,
        "source": track.source,
        "topic": topic,
        "topic_source": topic_source,
        "tags": tags,
        "created_at": created_at,
        "segments_count": len(segments),
        "track_key": track.key,
        "track_name": track.name,
        "include_timestamps": include_timestamps,
        "include_metadata": include_metadata,
        "paragraph_mode": paragraph_mode,
        "time_range_start_seconds": start_seconds,
        "time_range_end_seconds": end_seconds,
        "study_notes_generated": generate_study_notes,
        "study_notes_provider": study_notes["provider"],
        "summary": study_notes["summary"],
        "key_points": study_notes["key_points"],
        "highlights": study_notes["highlights"],
        "review_questions": study_notes["review_questions"],
    }

    markdown_text = build_markdown_output(
        info,
        track,
        segments,
        metadata,
        include_timestamps=include_timestamps,
        include_metadata=include_metadata,
        paragraph_mode=paragraph_mode,
    )
    text_output = build_output_text(
        info,
        track,
        segments,
        metadata,
        include_timestamps=include_timestamps,
        include_metadata=include_metadata,
        paragraph_mode=paragraph_mode,
    )
    json_output = build_json_output(metadata, segments)
    srt_output = build_srt_output(segments)
    vtt_output = build_vtt_output(segments)
    validate_sidecar_size("Markdown", markdown_text)
    validate_sidecar_size("TXT", text_output)
    validate_sidecar_size("JSON", json_output)
    validate_sidecar_size("SRT", srt_output)
    validate_sidecar_size("VTT", vtt_output)
    markdown_path.write_text(markdown_text, encoding="utf-8")
    txt_path.write_text(text_output, encoding="utf-8")
    json_path.write_text(json_output, encoding="utf-8")
    srt_path.write_text(srt_output, encoding="utf-8")
    vtt_path.write_text(vtt_output, encoding="utf-8")

    library_entry = build_library_entry(
        output_root,
        markdown_path,
        txt_path,
        json_path,
        srt_path,
        vtt_path,
        metadata,
    )
    library_path = update_library_index(output_root, library_entry)

    return {
        "title": info.get("title", ""),
        "channel": info.get("channel", ""),
        "url": info.get("webpage_url") or info.get("original_url") or clean_url,
        "upload_date": info.get("upload_date", ""),
        "duration": info.get("duration", 0),
        "track_lang": track.lang,
        "track_source": track.source,
        "track_key": track.key,
        "track_name": track.name,
        "track_label": format_track_label(track),
        "segments_count": len(segments),
        "include_timestamps": include_timestamps,
        "include_metadata": include_metadata,
        "paragraph_mode": paragraph_mode,
        "time_range_start_seconds": start_seconds,
        "time_range_end_seconds": end_seconds,
        "study_notes_generated": generate_study_notes,
        "study_notes_provider": study_notes["provider"],
        "summary": study_notes["summary"],
        "key_points": study_notes["key_points"],
        "highlights": study_notes["highlights"],
        "review_questions": study_notes["review_questions"],
        "topic": topic,
        "topic_source": topic_source,
        "tags": tags,
        "output_path": str(markdown_path),
        "output_name": markdown_path.name,
        "output_rel_path": to_output_relative_path(output_root, markdown_path),
        "txt_output_path": str(txt_path),
        "txt_output_name": txt_path.name,
        "txt_output_rel_path": to_output_relative_path(output_root, txt_path),
        "json_output_path": str(json_path),
        "json_output_name": json_path.name,
        "json_output_rel_path": to_output_relative_path(output_root, json_path),
        "srt_output_path": str(srt_path),
        "srt_output_name": srt_path.name,
        "srt_output_rel_path": to_output_relative_path(output_root, srt_path),
        "vtt_output_path": str(vtt_path),
        "vtt_output_name": vtt_path.name,
        "vtt_output_rel_path": to_output_relative_path(output_root, vtt_path),
        "library_index_path": str(library_path),
        "library_index_rel_path": to_output_relative_path(output_root, library_path),
        "transcript_text": markdown_text,
    }


def get_video_info(url: str) -> dict:
    options = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
    }

    def _extract() -> dict:
        with YoutubeDL(options) as ydl:
            return ydl.extract_info(url, download=False)

    return retry_call(_extract, action_name="video metadata")


def list_tracks_for_url(url: str) -> dict:
    clean_url = (url or "").strip()
    if clean_url == "":
        raise TranscriptionError(
            "missing_url",
            "Paste a YouTube URL and try again.",
        )

    info = get_video_info(clean_url)
    tracks = [serialize_track(track) for track in list_readable_tracks(info)]
    return {
        "video": {
            "title": info.get("title", ""),
            "channel": info.get("channel", ""),
            "url": info.get("webpage_url") or info.get("original_url") or clean_url,
            "upload_date": info.get("upload_date", ""),
            "duration": info.get("duration", 0),
        },
        "tracks": tracks,
    }


def list_topic_options(custom_topics: list[dict] | None = None) -> list[dict]:
    labels = topic_label_map(custom_topics)
    return [
        {
            "value": topic,
            "label": labels.get(topic, topic),
        }
        for topic in supported_topics(custom_topics)
    ]


def iter_tracks(info: dict) -> Iterable[SubtitleTrack]:
    for source_name in ("subtitles", "automatic_captions"):
        source_tracks = info.get(source_name) or {}
        for lang, entries in source_tracks.items():
            for index, entry in enumerate(entries):
                ext = entry.get("ext")
                url = entry.get("url")
                if not ext or not url:
                    continue
                yield SubtitleTrack(
                    key=build_track_key(source_name, lang, ext, index),
                    lang=lang,
                    ext=ext,
                    name=entry.get("name") or lang,
                    url=url,
                    source=source_name,
                )


def choose_track(info: dict, track_key: str | None = None) -> SubtitleTrack:
    tracks = list_readable_tracks(info)
    if not tracks:
        raise TranscriptionError(
            "no_subtitles",
            "This video does not have accessible subtitles or automatic captions, so this version cannot transcribe it.",
        )

    clean_track_key = (track_key or "").strip()
    if clean_track_key:
        for track in tracks:
            if track.key == clean_track_key:
                return track
        raise TranscriptionError(
            "invalid_track",
            "The selected caption track was no longer found. Check captions again.",
        )

    priority_pool = [
        track for lang in DEFAULT_LANG_PRIORITY for track in tracks if track.lang == lang
    ]
    if priority_pool:
        return prefer_manual_track(priority_pool)

    return prefer_manual_track(tracks)


def list_readable_tracks(info: dict) -> list[SubtitleTrack]:
    tracks = list(iter_tracks(info))
    json_tracks = [track for track in tracks if track.ext == "json3"]
    if not json_tracks:
        raise TranscriptionError(
            "no_json_subtitles",
            "Video captions were found, but their format could not be read. Try another video.",
        )
    return json_tracks


def build_track_key(source: str, lang: str, ext: str, index: int) -> str:
    raw_key = f"{source}|{lang}|{ext}|{index}"
    return re.sub(r"[^A-Za-z0-9_.|:-]+", "_", raw_key)


def serialize_track(track: SubtitleTrack) -> dict:
    return {
        "key": track.key,
        "lang": track.lang,
        "ext": track.ext,
        "name": track.name,
        "source": track.source,
        "label": format_track_label(track),
    }


def format_track_label(track: SubtitleTrack) -> str:
    source_label = "manual" if track.source == "subtitles" else "automatic"
    name = track.name if track.name != track.lang else ""
    parts = [track.lang]
    if name:
        parts.append(name)
    parts.append(source_label)
    return " - ".join(parts)


def prefer_manual_track(tracks: list[SubtitleTrack]) -> SubtitleTrack:
    manual = [track for track in tracks if track.source == "subtitles"]
    if manual:
        return manual[0]
    return tracks[0]


def fetch_json(url: str) -> dict:
    clean_url = (url or "").strip()
    parsed = urlparse(clean_url)
    if parsed.scheme not in {"http", "https"}:
        raise TranscriptionError(
            "invalid_caption_url",
            "This caption URL uses an unsupported scheme. Try another video or caption track.",
            f"Unsupported caption URL scheme: {parsed.scheme or '<empty>'}",
        )

    def _load() -> dict:
        request = urllib.request.Request(clean_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=60) as response:
            raw_body = response.read(MAX_CAPTION_JSON_BYTES + 1)
            if len(raw_body) > MAX_CAPTION_JSON_BYTES:
                raise TranscriptionError(
                    "subtitle_too_large",
                    "This caption file is too large to process safely.",
                    f"Caption JSON exceeded {MAX_CAPTION_JSON_BYTES} bytes.",
                )
            try:
                return json.loads(raw_body.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise TranscriptionError(
                    "invalid_subtitle_format",
                    "This caption file could not be parsed.",
                    str(exc),
                ) from exc

    return retry_call(_load, action_name="subtitle track")


def retry_call(func, action_name: str, retries: int = 3, base_delay: float = 2.0):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            return func()
        except Exception as exc:
            mapped = map_exception(exc, action_name)
            last_error = mapped
            if mapped.code in ("invalid_url", "no_subtitles", "private_video"):
                raise mapped
            if attempt == retries:
                break
            time.sleep(base_delay * attempt)

    if isinstance(last_error, TranscriptionError):
        raise last_error

    raise TranscriptionError(
        "unknown_error",
        "Could not finish transcription. Try again in a few seconds.",
        str(last_error),
    )


def map_exception(exc: Exception, action_name: str) -> TranscriptionError:
    if isinstance(exc, TranscriptionError):
        return exc

    message = str(exc)
    lowered = message.lower()

    if "too many requests" in lowered or "http error 429" in lowered or "429" == lowered.strip():
        return TranscriptionError(
            "rate_limited",
            "YouTube is temporarily rate-limiting requests. Wait a minute and try again.",
            message,
        )

    if "unsupported url" in lowered or "invalid url" in lowered or "incomplete youtube id" in lowered:
        return TranscriptionError(
            "invalid_url",
            "This does not look like a valid YouTube video URL.",
            message,
        )

    if "private video" in lowered or "login" in lowered or "sign in" in lowered:
        return TranscriptionError(
            "private_video",
            "This video is private or restricted, so its captions could not be fetched.",
            message,
        )

    if isinstance(exc, urllib.error.HTTPError) and exc.code == 404:
        return TranscriptionError(
            "subtitle_not_found",
            "The caption URL was not found. The video may no longer have accessible captions.",
            message,
        )

    return TranscriptionError(
        "unknown_error",
        f"Could not fetch {action_name}. Try again.",
        message,
    )


def clean_text(value: str) -> str:
    value = value.replace("\n", " ").replace("\r", " ")
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s+([,.!?;:])", r"\1", value)
    value = re.sub(r"([({\[])\s+", r"\1", value)
    value = re.sub(r"\s+([)}\]])", r"\1", value)
    value = re.sub(r"([,.!?;:])(?=[A-Za-z0-9])", r"\1 ", value)
    return value.strip()


def extract_segments(caption_json: dict) -> list[tuple[float, str]]:
    segments: list[tuple[float, str]] = []
    previous_text = ""
    total_chars = 0

    for event in caption_json.get("events", []):
        segs = event.get("segs")
        if not segs:
            continue

        text = clean_text("".join(seg.get("utf8", "") for seg in segs))
        if not text:
            continue

        if text == previous_text:
            continue

        start_seconds = round(event.get("tStartMs", 0) / 1000, 1)
        segments.append((start_seconds, text))
        total_chars += len(text)
        if len(segments) > MAX_CAPTION_SEGMENTS or total_chars > MAX_TRANSCRIPT_CHARS:
            raise TranscriptionError(
                "subtitle_too_large",
                "This caption file is too large to process safely.",
                "Caption segments exceeded configured safety limits.",
            )
        previous_text = text

    return segments


def clean_segments(segments: list[tuple[float, str]]) -> list[tuple[float, str]]:
    cleaned: list[tuple[float, str]] = []
    recent_seen: dict[str, float] = {}

    for start_seconds, text in segments:
        cleaned_text = clean_text(text)
        fingerprint = segment_fingerprint(cleaned_text)
        if not fingerprint:
            continue

        previous_seen_at = recent_seen.get(fingerprint)
        if previous_seen_at is not None and start_seconds - previous_seen_at <= 6:
            continue

        cleaned.append((start_seconds, cleaned_text))
        recent_seen[fingerprint] = start_seconds

    return cleaned


def segment_fingerprint(text: str) -> str:
    lowered = text.lower()
    lowered = re.sub(r"[^a-z0-9]+", " ", lowered)
    return re.sub(r"\s+", " ", lowered).strip()


def validate_time_range(start_seconds: float | None, end_seconds: float | None):
    if start_seconds is not None and start_seconds < 0:
        raise TranscriptionError(
            "invalid_time_range",
            "Start seconds must be zero or greater.",
        )

    if end_seconds is not None and end_seconds < 0:
        raise TranscriptionError(
            "invalid_time_range",
            "End seconds must be zero or greater.",
        )

    if start_seconds is not None and end_seconds is not None and end_seconds <= start_seconds:
        raise TranscriptionError(
            "invalid_time_range",
            "End seconds must be greater than start seconds.",
        )


def filter_segments_by_time_range(
    segments: list[tuple[float, str]],
    start_seconds: float | None = None,
    end_seconds: float | None = None,
) -> list[tuple[float, str]]:
    filtered = []
    for segment_start, text in segments:
        if start_seconds is not None and segment_start < start_seconds:
            continue
        if end_seconds is not None and segment_start > end_seconds:
            continue
        filtered.append((segment_start, text))
    return filtered


def build_transcript_lines(
    segments: list[tuple[float, str]],
    include_timestamps: bool = True,
    paragraph_mode: bool = False,
    markdown_timestamps: bool = False,
) -> list[str]:
    if not paragraph_mode:
        return [
            format_segment_line(start_seconds, text, include_timestamps, markdown_timestamps)
            for start_seconds, text in segments
        ]

    lines = []
    for start_seconds, text in group_segments_into_paragraphs(segments):
        lines.append(format_segment_line(start_seconds, text, include_timestamps, markdown_timestamps))
        lines.append("")

    if lines and lines[-1] == "":
        lines.pop()
    return lines


def format_segment_line(
    start_seconds: float,
    text: str,
    include_timestamps: bool,
    markdown_timestamps: bool,
) -> str:
    if not include_timestamps:
        return text

    if markdown_timestamps:
        return f"[{format_timestamp(start_seconds)}] {text}"

    return f"[{start_seconds:8.1f}s] {text}"


def group_segments_into_paragraphs(
    segments: list[tuple[float, str]],
    max_gap_seconds: float = 5.0,
    max_chars: int = 900,
) -> list[tuple[float, str]]:
    paragraphs: list[tuple[float, str]] = []
    current_start: float | None = None
    current_parts: list[str] = []
    previous_start: float | None = None

    for start_seconds, text in segments:
        clean_segment = clean_text(text)
        if not clean_segment:
            continue

        current_text = " ".join(current_parts)
        gap_is_large = previous_start is not None and start_seconds - previous_start > max_gap_seconds
        paragraph_is_long = bool(current_text) and len(current_text) + len(clean_segment) + 1 > max_chars
        sentence_closed = current_text.endswith((".", "!", "?")) and len(current_text) >= 280

        if current_parts and (gap_is_large or paragraph_is_long or sentence_closed):
            paragraphs.append((current_start or 0.0, current_text))
            current_start = None
            current_parts = []

        if current_start is None:
            current_start = start_seconds

        current_parts.append(clean_segment)
        previous_start = start_seconds

    if current_parts:
        paragraphs.append((current_start or 0.0, " ".join(current_parts)))

    return paragraphs


def empty_study_notes() -> dict:
    return {
        "provider": "",
        "summary": "",
        "key_points": [],
        "highlights": [],
        "review_questions": [],
    }


def build_study_notes(info: dict, segments: list[tuple[float, str]], topic_data: dict) -> dict:
    candidates = rank_study_note_candidates(info, segments, topic_data)
    selected = candidates[:5]
    selected.sort(key=lambda item: item["start_seconds"])

    key_points = [item["text"] for item in selected[:5]]
    highlights = [
        {
            "start_seconds": item["start_seconds"],
            "start": format_timestamp(item["start_seconds"]),
            "text": item["text"],
        }
        for item in selected[:4]
    ]
    summary = build_extractive_summary(info, selected, topic_data)

    topic_label = TOPIC_LABELS.get(topic_data["topic"], topic_data["topic"])
    review_questions = [
        f"What is the main idea behind {topic_label} in this video?",
        "Which practical steps or decisions are suggested by the transcript?",
        "Which terms or examples would be worth reviewing again?",
    ]

    return {
        "provider": "local-heuristic-v1",
        "summary": summary,
        "key_points": key_points,
        "highlights": highlights,
        "review_questions": review_questions,
    }


def rank_study_note_candidates(info: dict, segments: list[tuple[float, str]], topic_data: dict) -> list[dict]:
    title_words = important_words(info.get("title", ""))
    topic_words = important_words(" ".join([topic_data["topic"], *topic_data.get("tags", [])]))
    signal_words = {
        "important",
        "because",
        "therefore",
        "learn",
        "build",
        "problem",
        "solution",
        "example",
        "workflow",
        "process",
        "strategy",
        "risk",
        "mistake",
        "should",
        "need",
        "use",
        "create",
        "understand",
    }

    ranked = []
    seen = set()
    for start_seconds, text in segments:
        sentence = clean_text(text)
        words = important_words(sentence)
        fingerprint = segment_fingerprint(sentence)
        if len(sentence) < 40 or fingerprint in seen:
            continue

        score = 0
        score += len(words & title_words) * 3
        score += len(words & topic_words) * 2
        score += len(words & signal_words) * 2
        score += min(len(sentence), 220) / 120

        if "?" in sentence:
            score += 1
        if re.search(r"\b(first|second|third|finally|step|reason)\b", sentence.lower()):
            score += 1

        ranked.append(
            {
                "score": score,
                "start_seconds": start_seconds,
                "text": sentence,
            }
        )
        seen.add(fingerprint)

    ranked.sort(key=lambda item: (-item["score"], item["start_seconds"]))
    return ranked


def build_extractive_summary(info: dict, selected: list[dict], topic_data: dict) -> str:
    title = info.get("title", "") or "This video"
    topic_label = TOPIC_LABELS.get(topic_data["topic"], topic_data["topic"])

    if not selected:
        return f"{title} is a transcript in the {topic_label} topic. No strong local summary points were detected."

    first_points = " ".join(item["text"] for item in selected[:2])
    return f"{title} focuses on {topic_label}. {first_points}"


def important_words(text: str) -> set[str]:
    stop_words = {
        "the",
        "and",
        "for",
        "that",
        "this",
        "with",
        "you",
        "your",
        "are",
        "was",
        "were",
        "from",
        "have",
        "has",
        "but",
        "not",
        "can",
        "will",
        "just",
        "into",
        "about",
        "what",
        "when",
        "where",
        "how",
        "why",
    }
    return {
        word
        for word in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", text.lower())
        if word not in stop_words
    }


def build_output_text(
    info: dict,
    track: SubtitleTrack,
    segments: list[tuple[float, str]],
    metadata: dict,
    include_timestamps: bool = True,
    include_metadata: bool = True,
    paragraph_mode: bool = False,
) -> str:
    lines = []
    if include_metadata:
        lines.extend(
            [
                f"Title: {info.get('title', '')}",
                f"Channel: {info.get('channel', '')}",
                f"URL: {info.get('webpage_url') or info.get('original_url') or ''}",
                f"Published: {info.get('upload_date', '')}",
                f"Duration_seconds: {info.get('duration', '')}",
                f"Caption_language: {track.lang}",
                f"Source: {track.source}",
                "",
            ]
        )

    append_plain_study_notes(lines, metadata)
    lines.append("TRANSCRIPT:")

    lines.extend(
        build_transcript_lines(
            segments,
            include_timestamps=include_timestamps,
            paragraph_mode=paragraph_mode,
            markdown_timestamps=False,
        )
    )

    lines.append("")
    return "\n".join(lines)


def append_plain_study_notes(lines: list[str], metadata: dict):
    if not metadata.get("study_notes_generated"):
        return

    lines.extend(["", "SUMMARY:", metadata.get("summary", ""), "", "KEY POINTS:"])
    for point in metadata.get("key_points", []):
        lines.append(f"- {point}")

    lines.extend(["", "HIGHLIGHTS:"])
    for highlight in metadata.get("highlights", []):
        lines.append(f"- [{highlight.get('start', '')}] {highlight.get('text', '')}")

    lines.extend(["", "REVIEW QUESTIONS:"])
    for question in metadata.get("review_questions", []):
        lines.append(f"- {question}")

    lines.append("")


def build_markdown_output(
    info: dict,
    track: SubtitleTrack,
    segments: list[tuple[float, str]],
    metadata: dict,
    include_timestamps: bool = True,
    include_metadata: bool = True,
    paragraph_mode: bool = False,
) -> str:
    title = info.get("title", "") or "YouTube transcript"
    lines = build_frontmatter(metadata) if include_metadata else []
    if include_metadata:
        lines.append("")
    lines.extend([f"# {title}", ""])

    if include_metadata:
        lines.extend(
            [
                "## Summary",
                "",
                metadata.get("summary") or "Summary has not been generated yet.",
                "",
                "## Key Topics",
                "",
            ]
        )

        for tag in metadata["tags"]:
            lines.append(f"- {tag}")

        lines.append("")

        append_markdown_study_notes(lines, metadata)

    lines.extend(["## Transcript", ""])

    lines.extend(
        build_transcript_lines(
            segments,
            include_timestamps=include_timestamps,
            paragraph_mode=paragraph_mode,
            markdown_timestamps=True,
        )
    )

    lines.append("")
    return "\n".join(lines)


def append_markdown_study_notes(lines: list[str], metadata: dict):
    if not metadata.get("study_notes_generated"):
        return

    lines.extend(["## Study Notes", "", "### Key Points", ""])
    for point in metadata.get("key_points", []):
        lines.append(f"- {point}")

    lines.extend(["", "### Highlights", ""])
    for highlight in metadata.get("highlights", []):
        lines.append(f"- [{highlight.get('start', '')}] {highlight.get('text', '')}")

    lines.extend(["", "### Review Questions", ""])
    for question in metadata.get("review_questions", []):
        lines.append(f"- {question}")

    lines.append("")


def build_json_output(metadata: dict, segments: list[tuple[float, str]]) -> str:
    payload = {
        "schema_version": metadata["schema_version"],
        "metadata": metadata,
        "segments": [
            {
                "start_seconds": start_seconds,
                "start": format_timestamp(start_seconds),
                "text": text,
            }
            for start_seconds, text in segments
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def build_srt_output(segments: list[tuple[float, str]]) -> str:
    blocks = []
    for index, (start_seconds, text) in enumerate(segments, start=1):
        end_seconds = estimate_segment_end(segments, index - 1)
        blocks.extend(
            [
                str(index),
                f"{format_subtitle_timestamp(start_seconds, ',')} --> {format_subtitle_timestamp(end_seconds, ',')}",
                text,
                "",
            ]
        )
    return "\n".join(blocks)


def build_vtt_output(segments: list[tuple[float, str]]) -> str:
    lines = ["WEBVTT", ""]
    for index, (start_seconds, text) in enumerate(segments):
        end_seconds = estimate_segment_end(segments, index)
        lines.extend(
            [
                f"{format_subtitle_timestamp(start_seconds, '.')} --> {format_subtitle_timestamp(end_seconds, '.')}",
                text,
                "",
            ]
        )
    return "\n".join(lines)


def estimate_segment_end(segments: list[tuple[float, str]], index: int) -> float:
    start_seconds = segments[index][0]
    if index + 1 < len(segments):
        return max(start_seconds + 0.5, segments[index + 1][0] - 0.1)
    return start_seconds + 3.0


def format_subtitle_timestamp(seconds: float, decimal_separator: str) -> str:
    safe_seconds = max(0.0, float(seconds))
    total_ms = int(round(safe_seconds * 1000))
    hours = total_ms // 3_600_000
    minutes = (total_ms % 3_600_000) // 60_000
    secs = (total_ms % 60_000) // 1000
    millis = total_ms % 1000
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{decimal_separator}{millis:03d}"


def build_frontmatter(metadata: dict) -> list[str]:
    lines = ["---"]
    scalar_fields = [
        "schema_version",
        "title",
        "channel",
        "url",
        "video_id",
        "upload_date",
        "duration_seconds",
        "language",
        "source",
        "track_key",
        "track_name",
        "topic",
        "topic_source",
        "created_at",
        "segments_count",
        "include_timestamps",
        "include_metadata",
        "paragraph_mode",
        "time_range_start_seconds",
        "time_range_end_seconds",
        "study_notes_generated",
        "study_notes_provider",
        "summary",
    ]

    for field in scalar_fields:
        lines.append(f"{field}: {yaml_scalar(metadata.get(field, ''))}")

    lines.append("tags:")
    for tag in metadata.get("tags", []):
        lines.append(f"  - {yaml_scalar(tag)}")

    lines.append("---")
    return lines


def yaml_scalar(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    return json.dumps(str(value).replace("\r", " ").replace("\n", " "), ensure_ascii=False)


def format_timestamp(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def classify_topic(
    info: dict,
    segments: list[tuple[float, str]],
    custom_topics: list[dict] | None = None,
) -> dict:
    title = info.get("title", "")
    channel = info.get("channel", "")
    transcript_sample = " ".join(text for _, text in segments[:80])
    haystack = f"{title} {channel} {transcript_sample}".lower()

    best_rule = None
    best_score = 0
    matched_keywords: list[str] = []

    for rule in iter_topic_rules(custom_topics):
        current_matches = [keyword for keyword in rule["keywords"] if keyword in haystack]
        score = len(current_matches)
        if score > best_score:
            best_rule = rule
            best_score = score
            matched_keywords = current_matches

    if best_rule is None:
        return {
            "topic": "other",
            "tags": ["other"],
            "matched_keywords": [],
            "topic_source": "auto",
        }

    tags = dedupe_tags([*best_rule["tags"], *topic_parts(best_rule["topic"])])
    return {
        "topic": best_rule["topic"],
        "tags": tags,
        "matched_keywords": matched_keywords,
        "topic_source": "auto",
    }


def resolve_topic(
    info: dict,
    segments: list[tuple[float, str]],
    topic_override: str | None = None,
    custom_topics: list[dict] | None = None,
) -> dict:
    clean_override = (topic_override or "").strip()
    if clean_override == "":
        return classify_topic(info, segments, custom_topics)

    validate_topic_override(clean_override, custom_topics)

    return {
        "topic": clean_override,
        "tags": tags_for_topic(clean_override, custom_topics),
        "matched_keywords": [],
        "topic_source": "manual",
    }


def supported_topics(custom_topics: list[dict] | None = None) -> list[str]:
    topics: list[str] = []
    for rule in iter_topic_rules(custom_topics):
        topic = str(rule.get("topic", "")).strip()
        if topic and topic not in topics:
            topics.append(topic)

    if "other" not in topics:
        topics.append("other")

    return topics


def validate_topic_override(topic_override: str | None, custom_topics: list[dict] | None = None):
    clean_override = (topic_override or "").strip()
    if clean_override and clean_override not in supported_topics(custom_topics):
        raise TranscriptionError(
            "invalid_topic",
            "The selected topic is invalid. Refresh the page and choose a topic from the list.",
        )


def tags_for_topic(topic: str, custom_topics: list[dict] | None = None) -> list[str]:
    for rule in iter_topic_rules(custom_topics):
        if rule["topic"] == topic:
            return dedupe_tags([*rule["tags"], *topic_parts(topic)])

    if topic == "other":
        return ["other"]

    return dedupe_tags(topic_parts(topic))


def iter_topic_rules(custom_topics: list[dict] | None = None) -> list[dict]:
    rules = list(TOPIC_RULES)
    rules.extend(normalize_custom_topic_rules(custom_topics))
    return rules


def normalize_custom_topic_rules(custom_topics: list[dict] | None = None) -> list[dict]:
    if not isinstance(custom_topics, list):
        return []

    rules = []
    for raw_topic in custom_topics:
        if not isinstance(raw_topic, dict):
            continue

        topic = str(raw_topic.get("topic", "")).strip().lower()
        if not topic or topic == "other":
            continue

        topic = "/".join(
            re.sub(r"[^a-z0-9_-]+", "-", part).strip("-")
            for part in topic.split("/")
            if re.sub(r"[^a-z0-9_-]+", "-", part).strip("-")
        )
        if not topic:
            continue

        tags = raw_topic.get("tags")
        if not isinstance(tags, list):
            tags = topic_parts(topic)
        keywords = raw_topic.get("keywords")
        if not isinstance(keywords, list):
            keywords = []

        clean_keywords = [
            str(keyword).strip().lower()
            for keyword in keywords
            if str(keyword).strip()
        ]
        clean_keywords.extend(topic_parts(topic))

        rules.append(
            {
                "topic": topic,
                "tags": dedupe_tags([str(tag) for tag in tags] + topic_parts(topic)),
                "keywords": list(dict.fromkeys(clean_keywords)),
                "label": str(raw_topic.get("label", "")).strip(),
            }
        )

    return rules


def topic_label_map(custom_topics: list[dict] | None = None) -> dict:
    labels = dict(TOPIC_LABELS)
    for rule in normalize_custom_topic_rules(custom_topics):
        label = rule.get("label") or humanize_topic(rule["topic"])
        labels[rule["topic"]] = label
    return labels


def humanize_topic(topic: str) -> str:
    return " / ".join(part.replace("-", " ").title() for part in topic_parts(topic))


def dedupe_tags(tags: list[str]) -> list[str]:
    seen = set()
    deduped = []
    for tag in tags:
        normalized = re.sub(r"[^a-z0-9_-]+", "-", tag.lower()).strip("-")
        if normalized and normalized not in seen:
            seen.add(normalized)
            deduped.append(normalized)
    return deduped


def topic_parts(topic: str) -> list[str]:
    return [part for part in topic.split("/") if part]


def build_topic_output_dir(output_root: Path, topic: str) -> Path:
    path = output_root
    for part in topic_parts(topic):
        safe_part = re.sub(r"[^A-Za-z0-9_-]+", "-", part).strip("-").lower()
        if safe_part:
            path = path / safe_part
    return path


def build_library_entry(
    output_root: Path,
    markdown_path: Path,
    txt_path: Path,
    json_path: Path,
    srt_path: Path,
    vtt_path: Path,
    metadata: dict,
) -> dict:
    md_rel_path = to_output_relative_path(output_root, markdown_path)
    txt_rel_path = to_output_relative_path(output_root, txt_path)
    json_rel_path = to_output_relative_path(output_root, json_path)
    srt_rel_path = to_output_relative_path(output_root, srt_path)
    vtt_rel_path = to_output_relative_path(output_root, vtt_path)

    return {
        "schema_version": LIBRARY_SCHEMA_VERSION,
        "title": metadata["title"],
        "channel": metadata["channel"],
        "url": metadata["url"],
        "video_id": metadata["video_id"],
        "upload_date": metadata["upload_date"],
        "duration_seconds": metadata["duration_seconds"],
        "language": metadata["language"],
        "source": metadata["source"],
        "track_key": metadata["track_key"],
        "track_name": metadata["track_name"],
        "topic": metadata["topic"],
        "topic_source": metadata["topic_source"],
        "tags": metadata["tags"],
        "segments_count": metadata["segments_count"],
        "include_timestamps": metadata["include_timestamps"],
        "include_metadata": metadata["include_metadata"],
        "paragraph_mode": metadata["paragraph_mode"],
        "time_range_start_seconds": metadata["time_range_start_seconds"],
        "time_range_end_seconds": metadata["time_range_end_seconds"],
        "study_notes_generated": metadata["study_notes_generated"],
        "study_notes_provider": metadata["study_notes_provider"],
        "summary": metadata["summary"],
        "key_points": metadata["key_points"],
        "highlights": metadata["highlights"],
        "review_questions": metadata["review_questions"],
        "created_at": metadata["created_at"],
        "path": md_rel_path,
        "txt_path": txt_rel_path,
        "json_path": json_rel_path,
        "srt_path": srt_rel_path,
        "vtt_path": vtt_rel_path,
        "summary": metadata["summary"],
    }


def update_library_index(output_root: Path, entry: dict) -> Path:
    library_path = output_root / "library.json"
    if library_path.exists():
        try:
            data = json.loads(library_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = []
    else:
        data = []

    if not isinstance(data, list):
        data = []

    entry_key = (entry["video_id"], entry["language"], entry["path"])
    kept_entries = [
        item
        for item in data
        if not (
            isinstance(item, dict)
            and (item.get("video_id"), item.get("language"), item.get("path")) == entry_key
        )
    ]
    kept_entries.append(entry)
    kept_entries.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)

    temp_path = library_path.with_suffix(".json.tmp")
    temp_path.write_text(
        json.dumps(kept_entries, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temp_path.replace(library_path)
    return library_path


def to_output_relative_path(output_root: Path, path: Path) -> str:
    return path.resolve().relative_to(output_root.resolve()).as_posix()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def slugify_filename_part(value: str, fallback: str) -> str:
    ascii_value = (
        unicodedata.normalize("NFKD", value)
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    ascii_value = re.sub(r"[^a-z0-9]+", "-", ascii_value).strip("-")
    if ascii_value == "":
        ascii_value = fallback
    return ascii_value[:80].strip("-")


def safe_filename_stem(video_title: str, video_id: str, lang: str) -> str:
    safe_title = slugify_filename_part(video_title, video_id.lower())
    safe_video_id = safe_identifier_part(video_id, "video")
    safe_lang = re.sub(r"[^A-Za-z0-9_-]+", "_", lang)
    return f"{safe_title}_{safe_video_id}_{safe_lang}_transcript"


def safe_filename(video_title: str, video_id: str, lang: str) -> str:
    return f"{safe_filename_stem(video_title, video_id, lang)}.txt"


def safe_identifier_part(value: str, fallback: str) -> str:
    safe_value = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "")).strip("_-")
    return (safe_value or fallback)[:80]


def validate_output_path(output_root: Path, path: Path):
    try:
        to_output_relative_path(output_root, path)
    except ValueError as exc:
        raise TranscriptionError(
            "invalid_output_path",
            "The generated output path is invalid.",
            str(exc),
        ) from exc


def validate_sidecar_size(label: str, text: str):
    if len(text) > MAX_SIDECAR_CHARS:
        raise TranscriptionError(
            "transcript_too_large",
            "This transcript is too large to write safely.",
            f"{label} output exceeded {MAX_SIDECAR_CHARS} characters.",
        )
