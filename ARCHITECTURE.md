# Architecture

```text
Browser (static/index.html)
        │  POST /api/chat
        ▼
app.py  ──────────────►  core_model  ──►  llama.cpp  ──►  models/core.gguf
        │
        └── GET /api/health

cli.py  ── serve / chat / model status / doctor / version
```

## Components

### `app.py`

Small standard-library HTTP server:

- serves the single-page UI
- exposes `/api/health`
- loads exactly one bundled core model through llama.cpp
- binds loopback by default
- does not write files

### `cli.py`

The supported command-line surface:

```text
serve        start the UI
chat         one-shot or REPL
model status check the bundled core model
doctor       environment diagnostics
version      configured model and port
```

### `config.json`

The only configuration source:

```text
provider, core model path, runtime path, host, port, num_ctx, timeout, system prompt
```

No environment-specific paths.

## State

There is no server-side state:

```text
No messages persisted
No database
No embeddings
No scheduler
No user profile
```

The browser keeps the current conversation in memory only.

## Trust boundaries

```text
Browser input  → untrusted text
llama.cpp      → local process, bundled runtime
Server         → loopback only by default
Filesystem     → read-only for the server
Shell          → not used
```

## Non-goals

- multi-user hosting
- remote access by default
- plugin marketplace
- long-term memory
- autonomous code execution

Those belong to separate projects or forks.
