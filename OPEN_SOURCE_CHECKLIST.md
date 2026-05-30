# Open Source Release Checklist

Use this before the first public GitHub push.

## Include

- `LICENSE`
- `NOTICE`
- `README.md`
- `SECURITY.md`
- `CONTRIBUTING.md`
- `.gitignore`
- `Main/app.py`
- `Main/transcriber.py`
- `Main/index.html`
- `Main/static/`
- `Main/tests/`
- `Main/requirements.txt`
- `Main/start-tool.bat`
- `Main/start-tool.ps1`
- `Main/launch_tool.pyw`
- `Main/README.md`
- `Main/local_settings.example.json`

## Exclude

- `Main/local_settings.json`
- `Main/batch_jobs.json`
- `Main/outputs/`
- `Main/server.*.log`
- `Main/server.pid`
- `**/__pycache__/`
- `*.pyc`
- `.codex/`
- local agent memory, planning notes, and private workspace handoff files

## Checks

From `Main`:

```powershell
python -m unittest discover -s tests
python -m py_compile app.py transcriber.py launch_tool.pyw
node --check static\app.js
```

From the repository root:

```powershell
git status --ignored
```

Confirm that ignored runtime files are not staged.

## Security Preflight

Before the first public push:

- Start from a clean repository if possible. This folder is currently local-first; if no Git history is needed, prefer `git init` after this checklist rather than publishing accidental local history.
- Never run `git add .` blindly. Stage the public include list intentionally.
- Confirm these files are ignored and not staged:
  - `Main/local_settings.json`
  - `Main/local_settings.json.tmp`
  - `Main/batch_jobs.json`
  - `Main/batch_jobs.json.tmp`
  - `Main/outputs/`
  - `Main/server.*.log`
  - `Main/server.pid`
  - `.codex/`
  - `agents/`
  - `notes/`
  - `tasks/`
  - local planning/memory files
- Search the staged/public files for secrets and personal paths before commit:

```powershell
git diff --cached --name-only
git diff --cached
```

Suggested local text scan before staging:

```powershell
rg -n --hidden --glob '!Main/outputs/**' --glob '!Main/__pycache__/**' "[A-Z]:\\\\Users|[A-Z]:\\\\AI|OneDrive|api_key|OPENAI_API_KEY|sk-|token|secret|password|cookie|Bearer"
```

- If a real secret was ever committed to a Git repo, rotate/revoke it first. Do not rely only on deleting the file.
- If a real secret was committed and pushed, follow GitHub sensitive-data removal guidance and assume forks/clones may still contain it.
- Enable GitHub secret scanning and push protection for the public repo.
- If GitHub Actions are added later, harden workflow permissions and avoid untrusted pull request access to secrets.
- Consider OpenSSF Scorecard after the first public GitHub repository exists.

## Manual Review

- Open `Main/local_settings.json` locally and confirm no API key is staged.
- Confirm `Main/outputs/` is not staged.
- Confirm `Main/batch_jobs.json` is not staged.
- Confirm public README does not include private local paths beyond generic examples.
- Confirm the chosen license is Apache License 2.0.
- Confirm no generated transcript content is staged unless intentionally published as sample data.
- Confirm `Main/local_settings.example.json` contains only dummy/example values.
