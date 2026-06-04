# Changelog

## v0.2.1 - 2026-06-04

### Security

- Hardened local static file serving so ignored runtime JSON files are not exposed through the HTTP fallback.
- Rejected disallowed-origin POST requests before local API state changes are processed.
- Added request body size limits for JSON API endpoints.
- Sanitized transcript output filenames derived from `yt-dlp` metadata before sidecar files are written.
- Restricted caption JSON fetching to HTTP/HTTPS URLs and added caption/sidecar size limits.
- Sanitized Library source links so unsafe URL schemes are not returned or rendered.

### Validation

- Added regression tests for local file exposure, cross-origin POST rejection, request size limits, unsafe source URLs, output path sanitization, and caption input limits.
- `python -m unittest discover -s tests`
- `python -m py_compile app.py transcriber.py launch_tool.pyw`
- `node --check static\app.js`

## v0.2.0 - 2026-05-31

### Added

- Added a GitHub Actions test workflow for unit tests, Python compile checks, and frontend JavaScript syntax checks.
- Refreshed the primary web UI with the new workspace layout, compact Library cards, improved Settings, and clearer long-running action feedback.
- Added local diagnostics for the caption engine, including yt-dlp package/CLI status in Settings.
- Added troubleshooting notes for local startup, captions, rate limits, output folders, and local model endpoints.
- Added Library `Repair Index` flow to rebuild `library.json` from transcript Markdown files.
- Documented the Markdown/frontmatter, sidecar JSON, and `library.json` schema in `docs/library-schema.md`.
- Added README screenshots and a short example Markdown transcript structure in `docs/examples/sample-transcript.md`.
- Added a saved model profile `Test` action that checks OpenAI-compatible chat completions without sending transcript data.
- Added stricter AI topic classification JSON schema validation before metadata is saved.
- Added Study Guide generation progress with step states, elapsed waiting time, and a single-flight UI lock to prevent duplicate model requests.

### Fixed

- Made API-generated Study Guides configurable per model profile with source count, input character budget and output token budget, so small local models can be kept safe while large-context LM Studio models can receive much more transcript context.

## v0.1.0 - 2026-05-31

### Added

- Promoted the refreshed web UI to the primary app interface.
- Added a top navigation workspace with Transcribe, Batch, and Library tabs.
- Added dark mode, command palette entry point, recent transcript cards, toast notifications, and transcript search helpers.
- Added a compact Library layout where transcript details use the full card width and downloads/actions live in a bottom action row.
- Kept `/v2` as a compatibility alias for users who already opened the preview route.

### Changed

- Removed the old UI version switch from the public interface.
- Updated HTTP route tests to treat `/` as the primary UI and `/v2` as an alias.

### Validation

- `python -m unittest discover -s tests`
- `python -m py_compile app.py transcriber.py launch_tool.pyw`
- `node --check static\app.js`
- Browser smoke for `/` and `/v2/`

## v0.0.0 - 2026-05-30

- Initial open-source baseline.
