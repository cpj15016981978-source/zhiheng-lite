# -*- coding: utf-8 -*-
"""安装服务端门禁：bare origin + pre-receive。

用法（PowerShell）：
  powershell -ExecutionPolicy Bypass -File tools/install_bare_origin.ps1
"""
$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$BARE = if ($env:PAI_BARE_ORIGIN) { $env:PAI_BARE_ORIGIN } else { "D:\DSH\pai-origin.git" }

if (-not (Test-Path $BARE)) {
    git init --bare $BARE | Out-Null
    Write-Output "created bare: $BARE"
} else {
    Write-Output "bare exists: $BARE"
}

$drop = Join-Path $ROOT "tools\drop.py"
if (Test-Path $drop) {
    python $drop init-origin --advisory --path $BARE
    exit $LASTEXITCODE
}
$hook = Join-Path $BARE "hooks\pre-receive"
$src = Join-Path $ROOT "tools\pre-receive.sh"
$text = [IO.File]::ReadAllText($src, [Text.Encoding]::UTF8).Replace("`r`n", "`n")
[IO.File]::WriteAllText($hook, $text, (New-Object Text.UTF8Encoding($false)))

$git = "D:\Tools\MinGit\cmd\git.exe"
if (-not (Test-Path $git)) { $git = "git" }
& $git -C $ROOT remote remove origin 2>$null
& $git -C $ROOT remote add origin $BARE
Write-Output "installed pre-receive: $hook"
Write-Output "remote: $(& $git -C $ROOT remote get-url origin)"
Write-Output "next: git push -u origin master"
