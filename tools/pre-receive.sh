#!/bin/sh
# 服务端不可绕过的门禁：--no-verify 只骗本地钩子，骗不过 pre-receive。
# 安装：tools/install_bare_origin.ps1
set -u
unset PAI_GATE_SKIP PAI_GATE_SKIP_REASON 2>/dev/null || true
PY=${PAI_GATE_PYTHON:-python}

win_path() {
  case "$1" in
    /?/*) echo "${1:1:1}:/${1:3}" ;;
    *) echo "$1" ;;
  esac
}

BARE=$(win_path "$PWD")
tmp=$(mktemp -d "$PWD/../pai-gate.XXXXXX") || { echo "pre-receive: mktemp failed" >&2; exit 1; }
trap 'rm -rf "$tmp"' EXIT
rc=0
while read old new ref; do
  case "$new" in ""|0000000000000000000000000000000000000000) continue;; esac
  if ! git --git-dir="$PWD" archive --format=tar "$new" > "$tmp.tar"; then
    echo "pre-receive: archive $new failed" >&2; rc=1; continue
  fi
  if ! tar -xf "$tmp.tar" -C "$tmp"; then
    echo "pre-receive: checkout $new failed" >&2; rc=1; continue
  fi
  if ! (cd "$tmp" && PAI_COMMIT_REPO="$BARE" GIT_DIR="$BARE" \
        GIT_WORK_TREE="$(win_path "$tmp")" "$PY" tools/gates.py --tier push); then
    echo "pre-receive: quick gate RED, push rejected ($ref)" >&2; rc=1
  fi
done
exit $rc
