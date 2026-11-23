# Contributing

Thanks for considering a contribution.

## Scope

Zhiheng Lite intentionally stays small:

- One local model
- No database
- No long-term memory
- No accounts or cloud API
- Standard library only

Please open an issue before adding a feature that changes these boundaries.

## Development

```bash
python -m unittest discover -s tests -v
python cli.py doctor
python cli.py serve
```

Requires Python 3.10+ and the release assets `runtime/llama-server.exe` and `models/core.gguf`.

## Pull requests

1. Keep the change focused.
2. Add or update a standard-library test.
3. Run the full test suite.
4. Update `README.md` if behavior changes.
5. Do not commit local paths, credentials, model files, or chat data.

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

- Python standard library only
- Small functions, explicit errors
- No hidden network calls
- English identifiers and comments; Chinese docs are welcome in `docs/`

## License

By contributing, you agree that your contribution is licensed under MIT.
