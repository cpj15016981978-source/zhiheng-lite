# Architecture

```text
Browser (static/index.html)
        │  HTTP 127.0.0.1:8080
        ▼
app.py
  ├─ SQLite: local_office_memory.db
  │    memories / plans / plan_steps / pending_files / tasks / scripts
  ├─ API mode: Groq / OpenRouter
  └─ local mode: Ollama
```

## Components

### `app.py`

Small standard-library HTTP server:

- serves the single-page UI from `static/index.html`
- exposes chat, memory, plan, file-approval, task, and settings endpoints
- binds loopback only by default (`HOST = "127.0.0.1"`, `PORT = 8080`)
- persists conversations and tasks in `local_office_memory.db`
- stores provider settings, including the API key, in `muse_config.json`
- writes approved files only under `workspace/`
- auto mode tries API first, then falls back to local Ollama

### `muse_config.json`

The only runtime configuration source:

```text
mode, provider, api_key, api_model, local_main, local_code, host, port, system
```

This file is local-only and is git-ignored. It is not encrypted.

### `start.bat`

Installs the Python dependencies, starts Ollama if available, opens
`http://127.0.0.1:8080`, and runs `app.py`.

## State

The app is stateful:

```text
SQLite memory and plans: local_office_memory.db
Provider key:            muse_config.json
Approved output files:   workspace/
Generated code:          scripts/
Scheduled tasks:         SQLite tasks table + background scheduler
```

No server-side state is exposed outside loopback by default.

## Trust boundaries

```text
Browser input     → untrusted text
Groq/OpenRouter   → external services when API mode is used
Ollama            → local process
SQLite            → local memory store
Filesystem        → only workspace/ is writable after approval
Shell             → not used by the app
```

## Non-goals

- multi-user hosting
- remote access by default
- plugin marketplace
- MCP
- encrypted credential storage
- OS-level sandboxing
