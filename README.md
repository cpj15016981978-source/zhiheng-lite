# Zhiheng Lite

A minimal, local-first AI **office pipeline**.

One local model. No account. No database. No memory. No cloud API required.

> This is the clean open-source distribution. The author's personal Windows app is a separate, private runtime and is not part of this repository.

## What it is

```text
Text input  →  task prompt  →  bundled llama.cpp  →  one core model  →  Markdown output
```

Three office tasks:

```text
会议纪要 → 摘要 / 结论 / 待办
文档     → 一句话结论 / 关键点 / 风险
文本     → 待办表格
```

- One model, configured in `config.json`
- Stateless: input and output live only in the browser tab
- No chat history, no memory, no database
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
python cli.py serve                       # start the office UI
python cli.py office --task minutes FILE   # run a pipeline on a text file
python cli.py office --task summary FILE
python cli.py office --task actions FILE
python cli.py model status                 # check the bundled core model
python cli.py doctor                       # check the bundled core runtime and model
python cli.py version                      # show the configured model
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
  "timeout": 300
}
```

There is no model picker. Replace `models/core.gguf` only when you intend to
ship a different single core model.

## Design boundaries

```text
No memory
No chat history
No database
No accounts
No API keys
No background jobs
No arbitrary shell
```

The model is loaded by the bundled llama.cpp runtime. The server only accepts
loopback connections by default.

## Project layout

```text
app.py               office server
core_model.py        bundled llama.cpp client
office_pipeline.py   task prompts + input validation
cli.py               command-line entry point
config.json          core model, runtime, host, port
runtime/             llama-server binary (release asset)
models/              core.gguf (release asset)
static/index.html    single-page office UI
tests/               standard-library tests
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
single release asset.

- GitHub: https://github.com/cpj15016981978-source/zhiheng-lite
- Gitee mirror: https://gitee.com/zhiheng-lite/zhiheng-lite
- Release: https://github.com/cpj15016981978-source/zhiheng-lite/releases

Download both parts, join, then extract:

```bat
copy /b ZhihengLite-0.4.0-win64.zip.part1 + ZhihengLite-0.4.0-win64.zip.part2 ZhihengLite-0.4.0-win64.zip
```

Checksums are in `SHA256SUMS.txt` on the release page. See [PUBLISH.md](PUBLISH.md)
for source-only and Gitee notes.
