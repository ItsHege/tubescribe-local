# Changelog

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
