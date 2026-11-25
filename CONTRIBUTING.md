# Contributing

Thanks for considering a contribution.

## Scope

LowSpec Muse intentionally stays small:

- one Python HTTP app (`app.py`)
- SQLite memory
- Groq / OpenRouter free API plus local Ollama fallback
- step-by-step plans
- file approval into `workspace/`

Please open an issue before adding a feature that changes these boundaries.

## Development

```bash
python -m unittest discover -s tests -v
python app.py
```

Requires Python 3.10+, plus `ollama` and `schedule`.

Open `http://127.0.0.1:8080` after starting the app.

## Pull requests

1. Keep the change focused.
2. Add or update a standard-library test.
3. Run the full test suite.
4. Update `README.md` if behavior changes.
5. Do not commit `muse_config.json`, `local_office_memory.db`, `workspace/`,
   `scripts/`, model files, credentials, or chat data.

## Security-sensitive changes

Changes to the server, model calls, file access, or binding address need a short
threat note in the PR:

```text
What can now be reached?
What is trusted input?
What happens if it fails?
```

Never add arbitrary shell execution to the core.

## Style

- Python standard library only where practical
- Small functions, explicit errors
- No hidden network calls
- English identifiers; Chinese docs are welcome

## License

By contributing, you agree that your contribution is licensed under MIT.
