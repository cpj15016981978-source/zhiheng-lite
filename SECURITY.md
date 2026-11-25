# Security

## Reporting

Please do not open a public issue for a vulnerability. Contact the maintainer
privately and include:

- affected version/commit
- reproduction steps
- impact
- suggested fix, if any

## Current trust model

LowSpec Muse is a single-user, loopback-only app:

```text
Browser -> 127.0.0.1:8080 -> app.py -> SQLite / Groq / OpenRouter / Ollama
```

It does:

- listen on loopback by default (`host: 127.0.0.1`)
- store chat, memory, plans, and tasks in `local_office_memory.db`
- store the provider API key in `muse_config.json` as plain text
- write only approved files under `workspace/`
- call external model APIs when API mode is configured

It does not:

- listen on a public interface by default
- execute shell commands
- load remote plugins
- encrypt `muse_config.json`
- provide multi-user authentication

## Known limits

- The server has no authentication or per-request approval token; do not expose
  it beyond loopback.
- `muse_config.json` contains the API key in plain text; protect the file with
  OS permissions and never commit it.
- `local_office_memory.db` contains chat and memory data; never commit it.
- The model can produce harmful or incorrect text; do not treat output as
  verified fact.
- File approval limits writes to direct files in `workspace/` with allowed
  suffixes, but there is no backup or version history.

## Hardening checklist

```text
Keep host = 127.0.0.1
Never commit muse_config.json or local_office_memory.db
Restrict file permissions on muse_config.json
Do not expose the app through a public reverse proxy without authentication
Run under a low-privilege OS account
Keep Python and Ollama updated
```

## Out of scope

The following are not security guarantees of this project:

- model alignment or content moderation
- OS-level sandboxing
- multi-tenant isolation
- protection from a malicious local user
- encrypted credential storage
