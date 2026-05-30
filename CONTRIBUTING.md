# Contributing

Thanks for considering a contribution.

## Development Setup

1. Install Python 3.11+.
2. Install dependencies:

```powershell
cd Main
pip install -r requirements.txt
```

3. Run the local app:

```powershell
python app.py
```

4. Open:

```text
http://127.0.0.1:8765
```

## Tests

Run from `Main`:

```powershell
python -m unittest discover -s tests
python -m py_compile app.py transcriber.py launch_tool.pyw
node --check static\app.js
```

The automated tests use mocked YouTube/API behavior and temporary files. Do not add tests that make bulk YouTube requests.

## Pull Request Guidelines

- Keep the local-first workflow intact.
- Do not commit generated transcripts, local settings, batch state, logs, cache files, API keys, cookies, or private user data.
- Keep UI copy in English.
- Prefer small focused changes over rewrites.
- Add or update tests when changing backend parsing, output paths, API contracts, batch behavior, settings, or library metadata.
- Avoid sending transcript text to external services unless the user explicitly chooses an API engine.

## License

Unless explicitly stated otherwise, contributions are submitted under the Apache License 2.0.
