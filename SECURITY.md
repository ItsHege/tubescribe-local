# Security Policy

## Local-first Boundary

This tool is designed as a local desktop web app. The Python server binds to `127.0.0.1` by default and is not intended to be exposed as a public internet service.

## Sensitive Data

Do not commit:

- `Main/local_settings.json`
- `Main/batch_jobs.json`
- `Main/outputs/`
- `Main/server.*.log`
- `Main/server.pid`
- any API keys, cookies, OAuth tokens, private transcripts, or generated batch history

API model profiles are stored locally in `Main/local_settings.json`. The app returns only `api_key_set` to the browser and does not expose the key through `/api/settings`.

Before publishing this project publicly, run the preflight in `OPEN_SOURCE_CHECKLIST.md`. For a public GitHub repository, enable secret scanning and push protection in GitHub security settings.

If a real secret is ever committed, rotate or revoke it first. Removing a file from the working tree is not enough if it has entered Git history.

## External Requests

The base transcription workflow contacts YouTube through `yt-dlp` to fetch metadata and caption data. API model profiles are used only for explicit user actions, such as Study Guide generation or Topic Classification, when an API engine is selected.

## Reporting Issues

If you find a security issue, please do not post secrets or private transcript content in a public issue. Open a minimal report describing:

- affected endpoint or workflow;
- expected behavior;
- observed behavior;
- whether transcript text, API keys, file paths, or local files could be exposed.

## Scope Notes

This project does not currently implement user accounts, remote authentication, hosted multi-user storage, or production hardening. Treat it as a local single-user tool unless the architecture is changed deliberately.

## Supply Chain Notes

- Keep dependency changes small and review `Main/requirements.txt` before release.
- If GitHub Actions are added later, use least-privilege workflow permissions and avoid exposing secrets to untrusted pull requests.
- Consider OpenSSF Scorecard once the public repository exists.
