# Zhiheng Lite

A minimal, local-first AI office assistant.

One local model. No account. No database. No long-term memory. No cloud API required.

> This is the clean open-source distribution. The author's personal Windows app is a separate, private runtime and is not part of this repository.

## What it is

```text
Browser UI  →  small Python server  →  bundled llama.cpp runtime  →  one core model
```

- Single model, configured in `config.json`
- Stateless: chat history lives only in the browser tab
- No database, no embeddings, no scheduler
- No telemetry, no API keys, no accounts
- MIT licensed; fork and replace anything

## Requirements

- Python 3.10+
- A release build that contains:
  - `runtime/llama-server.exe`
  - `models/core.gguf`

No Python packages are required beyond the standard library.
No Ollama install, no model picker, no API key.

Source checkouts do not contain the model or runtime (they are large binary
release assets). For local development you can set `"dev_ollama": true` in
`config.json` to use an existing Ollama instance.

## Quickstart

```bash
git clone <your-fork-url> zhiheng-lite
cd zhiheng-lite
# edit config.json if needed
python cli.py doctor
python cli.py serve
```

Open <http://127.0.0.1:8760>.

Or start the server directly:

```bash
python app.py
```

## CLI

```bash
python cli.py serve              # start the web UI
python cli.py chat "hello"       # one-shot chat
python cli.py chat               # interactive REPL
python cli.py model status       # check the bundled core model
python cli.py doctor             # check the bundled core runtime and model
python cli.py version            # show the configured model
```

## Configuration

Only `config.json`:

```json
{
  "provider": "core",
  "model": "qwen3-4b-instruct-q4",
  "runtime": "runtime/llama-server.exe",
  "model_path": "models/core.gguf",
  "local_port": 8761,
  "dev_ollama": false,
  "host": "127.0.0.1",
  "port": 8760,
  "num_ctx": 4096,
  "max_tokens": 2048,
  "timeout": 300,
  "system": "You are Zhiheng Lite, a local office assistant."
}
```

There is no model picker. Replace `models/core.gguf` only when you intend to
ship a different single core model.

## Design boundaries

```text
No memory
No database
No accounts
No API keys
No background jobs
No arbitrary shell
```

The model is loaded by the bundled llama.cpp runtime. The server only accepts
loopback connections by default. If you only have the source checkout, use
the release assets or set `dev_ollama` for development.

## Project layout

```text
app.py              server
core_model.py       bundled llama.cpp client
cli.py              command-line entry point
config.json         core model, runtime, host, port
runtime/            llama-server binary (release asset)
models/             core.gguf (release asset)
static/index.html   single-page chat UI
tests/              standard-library tests
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Security

See [SECURITY.md](SECURITY.md). Please do not report vulnerabilities in public issues.

## License

MIT. See [LICENSE](LICENSE).
## Download

Source code is here. The Windows runtime is a release asset because it contains
`llama-server` and `core.gguf` (about 2.3 GB), which GitHub cannot store as a
single release asset. See [PUBLISH.md](PUBLISH.md) for the split parts and
checksums.
