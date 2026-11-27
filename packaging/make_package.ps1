# -*- coding: utf-8 -*-
param(
  [string]$Out = 'D:\DSH\ZhihengPackage',
  [string]$PythonSrc = $env:PAI_PYTHON
)
$ErrorActionPreference = 'Stop'
$Repo = Split-Path -Parent $PSScriptRoot
if (-not $PythonSrc) {
  $cmd = Get-Command python -ErrorAction SilentlyContinue
  if ($cmd) { $PythonSrc = Split-Path -Parent $cmd.Source }
}
if (-not $PythonSrc -or -not (Test-Path $PythonSrc)) { throw '缺少 Python 源目录：请设置 PAI_PYTHON' }
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$App = Join-Path $Out 'app'
New-Item -ItemType Directory -Force -Path $App | Out-Null
$Exclude = @('data', '.git', '__pycache__', 'python', 'install.log', 'ZhihengPackage')
robocopy $Repo $App /E /XD $Exclude /XF *.bak /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "复制程序失败：$LASTEXITCODE" }
$Py = Join-Path $Out 'python'
if (-not (Test-Path (Join-Path $Py 'python.exe'))) {
  robocopy $PythonSrc $Py /E /NFL /NDL /NJH /NJS /NP | Out-Null
  if ($LASTEXITCODE -ge 8) { throw "复制 Python 失败：$LASTEXITCODE" }
}
Copy-Item (Join-Path $PSScriptRoot 'install_zhiheng.ps1') (Join-Path $Out 'install_zhiheng.ps1') -Force
Copy-Item (Join-Path $PSScriptRoot 'uninstall_zhiheng.ps1') (Join-Path $Out 'uninstall_zhiheng.ps1') -Force
@'
@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_zhiheng.ps1"
pause
'@ | Set-Content -Path (Join-Path $Out 'install.bat') -Encoding ascii
Write-Host "安装包已生成：$Out"
Write-Host "双击：$Out\install.bat"
