# TubeScribe Local

Local-first YouTube caption transcription tool that saves Markdown transcripts into a searchable knowledge library.

The app fetches available YouTube subtitles or automatic captions through `yt-dlp`, writes Markdown as the primary artifact, creates TXT/JSON/SRT/VTT sidecar files, and maintains a local `library.json` index for agents and repeat use.

## Features

- Markdown-first transcript export.
- TXT, JSON, SRT, and VTT sidecar exports.
- Topic folders and local `library.json` index.
- Library UI with search, preview, downloads, source links, and multi-select topic/tag filters.
- Caption track selection.
- Time range export and paragraph mode.
- Local heuristic study notes.
- Optional OpenAI-compatible API model profiles for explicit Study Guide and Topic Classification actions.
- Backend batch queue with pause/resume, cancel queued items, optional playlist expansion, persisted batch history, and ZIP download.
- Local Windows launch helpers.

## Project Layout

```text
Main/
  app.py
  transcriber.py
  index.html
  static/
  tests/
  requirements.txt
  start-tool.bat
  start-tool.ps1
  launch_tool.pyw
```

Generated files and local secrets are ignored by git:

```text
Main/local_settings.json
Main/batch_jobs.json
Main/outputs/
Main/server.*.log
Main/server.pid
```

## Quick Start

```powershell
cd Main
pip install -r requirements.txt
python app.py
```

Then open:

```text
http://127.0.0.1:8765
```

On Windows you can also run:

```powershell
.\start-tool.bat
```

## Settings

Copy `Main/local_settings.example.json` only if you want a starting point for local settings. The app can also create `Main/local_settings.json` through the Settings modal.

Do not commit `Main/local_settings.json`; it can contain API keys.

## Tests

Run from `Main`:

```powershell
python -m unittest discover -s tests
python -m py_compile app.py transcriber.py launch_tool.pyw
node --check static\app.js
```

The automated tests avoid live YouTube/API calls.

## Security And Privacy

This is a local single-user tool. It is not a production web server. Transcript text stays local unless you explicitly choose an API model profile for Study Guide generation or Topic Classification.

See `SECURITY.md` before publishing, deploying, or accepting contributions.

## License

Apache License 2.0. See `LICENSE` and `NOTICE`.
