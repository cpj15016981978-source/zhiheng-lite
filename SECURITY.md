# Security

## Reporting

Please do not open a public issue for a vulnerability. Contact the maintainer
privately and include:

- affected version/commit
- reproduction steps
- impact
- suggested fix, if any

## Current trust model

Zhiheng Lite is a single-user, loopback-only app:

```text
Browser  → 127.0.0.1  →  bundled llama.cpp  →  one core model
```

It does not:

- listen on a public interface by default
- store credentials
- execute shell commands
- write chat data to disk
- load remote plugins

## Known limits

- The server has no authentication; do not expose it beyond loopback without a
  reverse proxy and access control.
- The model can produce harmful or incorrect text; do not treat output as
  verified fact.
- The bundled llama.cpp runtime is a separate local process with its own security model.
- `config.json` is trusted local configuration.

## Hardening checklist

```text
Keep host = 127.0.0.1
Do not put API keys in this repository
Do not mount the app directory into a public server
Run under a low-privilege OS account
Keep Python and the bundled runtime updated
```

## Out of scope

The following are not security guarantees of this project:

- model alignment or content moderation
- OS-level sandboxing
- multi-tenant isolation
- protection from a malicious local user
