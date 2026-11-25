# Security

## Supported platform

知衡 is currently Windows-only. The code uses Windows DPAPI, `WINFUNCTYPE`,
PowerShell and Windows registry capabilities. There is no cross-platform
security promise for this branch.

## Data locations

```text
%LOCALAPPDATA%\Zhiheng\data        user data, memory, jobs, cache
%LOCALAPPDATA%\Zhiheng\data\workspace   approved output files
providers.json / DPAPI blob        model API keys (encrypted with Windows DPAPI)
```

The project `data/` directory is legacy and is not committed. Never commit
`memory.db`, `tasks.db`, `jobs.db`, logs, chat exports, browser profiles, or
provider credentials.

## Trust model

```text
Browser  -> 127.0.0.1:8753 -> local webapp
webapp   -> local Ollama / configured API providers
webapp   -> MCP/desktop/browser tools only after approval
```

- The HTTP server binds loopback by default.
- Approval-sensitive endpoints require a same-origin page request plus a
  per-process UI nonce.
- High-risk actions require an approval token; token plaintext is not written to
  `approval_pending.json`.
- API keys are stored with Windows DPAPI; they are never returned by the
  credentials endpoint.
- The app does not run a public relay or multi-user service.

## Known limits

- Local processes can still make loopback requests; the UI nonce is not an OS
  sandbox.
- Legacy single-user approval mode relies on the local ledger.
- `agentd` token mode is optional and requires a separately running agentd.
- Model output is untrusted text and must not be treated as verified fact.
- No OS-level network egress hard block is provided.

## Reporting

Do not open a public issue for a vulnerability. Report privately with:

```text
affected version/commit
reproduction steps
impact
suggested fix
```

## Hardening checklist

```text
Keep the server on 127.0.0.1
Do not commit data/, wiki/, *.db, or keys
Run as a low-privilege local user
Keep Python, Ollama and dependencies updated
Back up %LOCALAPPDATA%\Zhiheng before upgrades
```
