# TubeScribe Local

A simple local web tool for fetching YouTube subtitles or automatic captions and saving them as a Markdown transcript. TXT, JSON, SRT, and VTT sidecar files are created next to the Markdown file.

## Start

1. Run `start-tool.bat`.
2. Wait a few seconds for the browser to open.
3. Paste a YouTube URL and click `Transcribe`.

Important: do not open `index.html` directly. The frontend needs the local server.

To choose a specific caption track, click `Check Captions`, select a track, then click `Transcribe`.

For several individual video URLs, use the `Batch` queue. Batch now runs on the local backend, exposes a job status, can pause/resume between videos, can optionally expand playlists, and can download completed results as a ZIP.

If automatic topic assignment is wrong, choose a topic in the `Topic` field before transcribing. The new transcript will be written into that topic folder under the configured output folder.

## How Transcribe Works

When you click `Transcribe`, the browser sends the URL and selected options to the local Python server at `http://127.0.0.1:8765`.

The server then:

1. validates the request and selected options;
2. asks `yt-dlp` for YouTube video metadata without downloading the video;
3. finds readable `json3` subtitle or automatic caption tracks;
4. uses the selected caption track, or chooses the best available track by language/source priority;
5. downloads the caption JSON and extracts timestamped text segments;
6. cleans punctuation spacing and removes nearby duplicate caption lines;
7. optionally filters the transcript by `Start seconds` / `End seconds`;
8. classifies the topic through local keyword rules unless a manual topic was selected;
9. optionally creates local study notes;
10. writes Markdown as the primary file and TXT/JSON/SRT/VTT as sidecar files;
11. updates `outputs/library.json` so agents and the Library UI can find the transcript later.

The current `Transcribe` flow is caption-based. It does not download audio and does not run Whisper yet. API models are used only for explicit Study Guide or Topic Classification actions when an API profile is selected.

## Options

- `Settings` gear: add one or more OpenAI-compatible model profiles and choose the default Study Guide engine.
- `Output Folder`: choose where new Markdown/TXT/JSON/SRT/VTT files and `library.json` are stored. Relative paths are resolved from the `Main` folder.
- `Batch Limit`: choose the maximum URLs allowed in one backend batch run.
- `Expand playlists`: explicitly expand pasted YouTube playlists into individual video URLs before the backend batch starts.
- Default checkboxes in Settings: choose the default state for timestamps, metadata, paragraph mode, and study notes.
- `Timestamps`: include or hide transcript timestamps.
- `Metadata`: include or hide Markdown frontmatter and summary/topic placeholders.
- `Paragraph mode`: group short caption lines into readable paragraphs in Markdown and TXT output.
- `Start seconds` and `End seconds`: export only a selected time range. Leave either field empty for an open-ended range.
- `Study notes`: generate local extractive study material: summary, key points, highlights, and review questions.

## What It Does

- Fetches video metadata.
- Finds accessible subtitles or automatic captions.
- Lets you check and select a caption track.
- Lets you manually override the detected topic.
- Runs a small backend batch queue for multiple individual video URLs, with optional playlist expansion, polling status, pause/resume between videos, cancel for queued items, and ZIP download for completed results.
- Generates clean Markdown with optional timestamps, metadata, paragraph mode, time range filtering, and local study notes.
- Writes the Markdown file into a topic folder under the configured output folder.
- Creates TXT, JSON, SRT, and VTT sidecar exports.
- Updates `outputs/library.json` for agents.
- Shows detected topic and tags.
- Shows a local library with search, Excel-style multi-select dropdown filters for topics and tags, channel/language filters, Markdown preview, copy, downloads, and YouTube source links.
- Generates a local Markdown study guide from the library, optionally scoped to the selected topic checkbox filters.
- Lets you choose between local heuristic study guide generation and any configured OpenAI-compatible API model profile.
- Lets you classify or reclassify any library entry with the selected AI engine and save the resulting topic, tags, summary, and future recognition keywords.
- Shows a clear error if the video has no accessible captions.

## Notes

- If YouTube temporarily rate-limits requests, the app will show a message and you can try again later.
- Private videos or unavailable captions cannot be transcribed by this caption-only version.
- Batch mode expands playlists only when `Expand playlists` is checked. Playlist expansion uses `yt-dlp` flat playlist metadata and still respects the batch limit.
- Batch mode intentionally runs sequentially to reduce noisy YouTube traffic. The per-run limit is configurable in Settings.
- Pausing a batch takes effect between videos. The currently running video may finish because `yt-dlp` is already working on that request.
- Canceling a batch stops queued items. Completed files can be downloaded through `Download ZIP`.
- Batch jobs are saved locally in `batch_jobs.json` so completed job metadata can survive a server restart. In-progress jobs are marked interrupted after restart rather than resumed automatically.
- When the tool page is closed, the local server shuts down after a short idle period.
- Generated Markdown/TXT/JSON/SRT/VTT filenames are based on the video title, video ID, language, and `_transcript` suffix.
- Topic assignment is keyword-based in this version, so manual override can still be useful.
- Study notes are generated locally with a simple heuristic provider. They do not send transcript text to a cloud model.
- Library study guides are generated from existing Markdown files and are shown for preview/copy; they are not saved as permanent files yet.
- API model profiles, output folder, batch limit, playlist expansion preference, and default checkbox settings are stored locally in `local_settings.json`. This file is ignored by git because it can contain API keys.
- Local models usually need a local OpenAI-compatible HTTP server. For example, run a model through Ollama, LM Studio, vLLM, or a similar server, then add that server URL as a model profile.
- Choosing an API profile sends selected transcript excerpts to that configured endpoint. Keep `Local heuristic` selected if you want everything to stay on this machine.
- Clicking `Classify Topic` or `Reclassify Topic` sends that transcript excerpt to the selected API profile when an API engine is selected; the local heuristic stays on this machine.
- Manual topic selection applies only to the new transcript; existing files are not moved automatically.
- If a selected caption track becomes stale because YouTube metadata changed, click `Check Captions` again.

## Tests

From the `Main` folder:

```powershell
python -m unittest discover -s tests
python -m py_compile app.py transcriber.py launch_tool.pyw
node --check static\app.js
```

The current automated tests use mocked metadata/captions and temporary HTTP servers/files, so they do not call YouTube and do not touch the real `outputs` folder.

## Open Source Notes

- License: Apache License 2.0. See `../LICENSE`.
- Example local settings: `local_settings.example.json`.
- Do not commit `local_settings.json`, `batch_jobs.json`, `outputs/`, server logs, or API keys.
