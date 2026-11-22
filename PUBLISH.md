# Publish Zhiheng Lite

The source repository is small. The runnable Windows release is about 2.3 GB,
so it cannot be committed to Git or uploaded as one GitHub release asset.

## What goes where

```text
GitHub / Gitee / GitCode
  source code, docs, tests, placeholder folders

Release assets
  ZhihengLite-0.3.0-win64.zip.part1
  ZhihengLite-0.3.0-win64.zip.part2
  SHA256SUMS.txt
```

## GitHub

Published: https://github.com/cpj15016981978-source/zhiheng-lite

Push this folder:

```bash
git remote add origin https://github.com/cpj15016981978-source/zhiheng-lite.git
git branch -M main
git push -u origin main
```

3. Create a release tag `v0.3.0`.
4. Upload the split parts and `SHA256SUMS.txt` as release assets.
5. Put the download links in `README.md`.

GitHub limits each release asset to 2 GB; each part is below 1.5 GB.

## Gitee

Published: https://gitee.com/zhiheng-lite/zhiheng-lite

1. Push the same source repository.
2. Gitee release asset limits are smaller; do not upload the 2.3 GB package
   there.
4. Put the model/runtime package on a domestic file host and link it from the
   Gitee release notes, or publish the source-only repo and point to GitHub
   Releases for the binary.

## GitCode

Same as Gitee: source repository plus an external binary link.

## Rebuild the Windows package

```powershell
cd D:\DSH\ZhihengLite
tar.exe -a -c -f D:\DSH\dist\ZhihengLite-0.3.0-win64.zip `
  app.py core_model.py cli.py config.json README.md CONTRIBUTING.md `
  ARCHITECTURE.md SECURITY.md LICENSE MODEL_NOTICE.md .gitignore `
  static tests models runtime
```

Then split it:

```powershell
.\tools\split_release.ps1
```

## Checks

```text
Run: python cli.py doctor
Run: python cli.py serve
Open: http://127.0.0.1:8760
Confirm: /api/health provider=core, ok=true
Confirm: no Ollama process required
```

## Before publishing

```text
[ ] Replace <account> placeholders
[ ] Add real repository URL to README
[ ] Confirm Qwen3 Apache-2.0 attribution
[ ] Confirm llama.cpp MIT notice
[ ] Upload both parts + SHA256SUMS.txt
[ ] Test download/join/run on a clean machine
```
