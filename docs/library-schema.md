# Library Schema

TubeScribe Local stores transcripts as Markdown-first artifacts under the configured output folder. The default folder is `Main/outputs`.

The active machine-readable index is:

```text
<output-folder>/library.json
```

`library.json` is a rebuildable index. Markdown files and their sidecar files are the durable local artifacts. If the index drifts, use `Repair Index` in the Library tab to rebuild it from transcript Markdown files.

## Folder Layout

Transcript files are grouped by topic:

```text
outputs/
  ai/
    agents/
      example_VIDEOID_en_transcript.md
      example_VIDEOID_en_transcript.txt
      example_VIDEOID_en_transcript.json
      example_VIDEOID_en_transcript.srt
      example_VIDEOID_en_transcript.vtt
  library.json
```

Changing `Output Folder` in Settings changes which folder is scanned and which `library.json` is active. Existing files are not moved automatically.

## Markdown Frontmatter

Generated Markdown starts with project-owned YAML-style frontmatter. This is a TubeScribe convention, not a universal Markdown requirement.

Required or strongly recommended fields:

```yaml
---
schema_version: 2
title: "Video title"
channel: "Channel name"
url: "https://www.youtube.com/watch?v=..."
video_id: "..."
upload_date: "20260531"
duration_seconds: 1234
language: "en-orig"
source: "automatic_captions"
track_key: "automatic_captions|en-orig|json3|0"
track_name: "English"
topic: "ai/agents"
topic_source: "auto"
created_at: "2026-05-31T10:00:00Z"
segments_count: 300
include_timestamps: true
include_metadata: true
paragraph_mode: false
time_range_start_seconds: null
time_range_end_seconds: null
study_notes_generated: false
study_notes_provider: "local-heuristic-v1"
summary: "Short summary when available."
tags:
  - ai
  - agents
---
```

Known `topic_source` values:

- `auto`: local keyword classifier picked the topic.
- `manual`: the user selected the topic before transcription.
- `ai`: the user ran Library topic classification with an AI engine.
- `rebuild`: the index repair flow inferred missing metadata from file path or sidecars.

Readers should tolerate missing fields because older transcripts and transcripts generated with metadata disabled may not have full frontmatter.

## JSON Sidecar

The `.json` sidecar stores:

```json
{
  "schema_version": 2,
  "metadata": {
    "title": "Video title",
    "topic": "ai/agents"
  },
  "segments": [
    {
      "start_seconds": 1.2,
      "start": "00:00:01",
      "text": "Caption text"
    }
  ]
}
```

The sidecar is useful for agents and future search features because it preserves structured segment timestamps.

## library.json Entry

Each index entry points to relative paths inside the active output folder:

```json
{
  "schema_version": 2,
  "title": "Video title",
  "channel": "Channel name",
  "url": "https://www.youtube.com/watch?v=...",
  "video_id": "...",
  "upload_date": "20260531",
  "duration_seconds": 1234,
  "language": "en-orig",
  "source": "automatic_captions",
  "track_key": "automatic_captions|en-orig|json3|0",
  "track_name": "English",
  "topic": "ai/agents",
  "topic_source": "auto",
  "tags": ["ai", "agents"],
  "segments_count": 300,
  "include_timestamps": true,
  "include_metadata": true,
  "paragraph_mode": false,
  "time_range_start_seconds": null,
  "time_range_end_seconds": null,
  "study_notes_generated": false,
  "study_notes_provider": "local-heuristic-v1",
  "summary": "Short summary when available.",
  "key_points": [],
  "highlights": [],
  "review_questions": [],
  "created_at": "2026-05-31T10:00:00Z",
  "path": "ai/agents/example_VIDEOID_en_transcript.md",
  "txt_path": "ai/agents/example_VIDEOID_en_transcript.txt",
  "json_path": "ai/agents/example_VIDEOID_en_transcript.json",
  "srt_path": "ai/agents/example_VIDEOID_en_transcript.srt",
  "vtt_path": "ai/agents/example_VIDEOID_en_transcript.vtt"
}
```

API responses decorate entries with a `downloads` object when referenced files exist. The persisted `library.json` should store relative paths, not server URLs.

## Repair Index

`POST /api/library/rebuild` scans the active output folder for transcript-like Markdown files and writes a fresh `library.json`.

It indexes Markdown files when at least one is true:

- the file is already present in the previous `library.json`;
- frontmatter contains transcript metadata such as `url`, `video_id`, `topic`, or `language`;
- the filename ends with `_transcript.md`;
- the file has a `## Transcript` section.

The repair flow does not move, edit, or delete transcript files. It only replaces `library.json` with a rebuilt index.
