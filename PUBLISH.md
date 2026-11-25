# Publish LowSpec Muse

## What goes where

```text
GitHub / Gitee
  app.py, static/, tests/, docs, README, LICENSE

Release assets (if a bundled runtime is added)
  Windows package parts + SHA256SUMS.txt
```

## GitHub

1. Create or use a repository, for example `zhiheng-lite`.
2. Push the source folder.
3. Do not commit `muse_config.json`, `local_office_memory.db`, `workspace/`,
   `scripts/`, model files, or runtime binaries.
4. If publishing a bundled release, use release assets rather than Git history.

## Rebuild the audit surface

```powershell
python -m unittest discover -s tests -v
python app.py
```

Expected:

```text
http://127.0.0.1:8080
/api/health returns ok
```

## Before publishing

```text
[ ] .gitignore covers muse_config.json, *.db, workspace/, scripts/
[ ] No API key in tracked files
[ ] No chat/memory database in tracked files
[ ] README run instructions match app.py port
[ ] SECURITY.md describes plaintext key and SQLite persistence
```
