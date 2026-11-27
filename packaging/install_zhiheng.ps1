# -*- coding: utf-8 -*-
param(
  [string]$Target = (Join-Path $env:LOCALAPPDATA 'Programs\Zhiheng'),
  [switch]$NoShortcut
)
$ErrorActionPreference = 'Stop'
$Root = $PSScriptRoot
$AppSrc = Join-Path $Root 'app'
if (-not (Test-Path $AppSrc)) { $AppSrc = $Root }
$PySrc = Join-Path $Root 'python'
if (-not (Test-Path $PySrc)) {
  if ($env:PAI_PYTHON -and (Test-Path $env:PAI_PYTHON)) {
    $PySrc = $env:PAI_PYTHON
  } else {
    throw "缺少内置 Python：请使用完整安装包，或设置 PAI_PYTHON 指向 python 目录"
  }
}
$Log = Join-Path $Root 'install.log'
function Log($m) { Add-Content -Path $Log -Value ("{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) }
Log "install/upgrade -> $Target"

$Exists = Test-Path $Target
if ($Exists) {
  $Py = Join-Path $Target 'python\python.exe'
  if (Test-Path $Py) {
    Log 'backup before upgrade'
    Push-Location $Target
    try {
      $out = & $Py -c "from pai import backup; print(backup.create())" 2>&1
      Log ($out -join "`n")
      if ($LASTEXITCODE -ne 0) { throw "backup failed: $out" }
    } finally { Pop-Location }
  }
}

New-Item -ItemType Directory -Force -Path $Target | Out-Null
if (-not (Test-Path (Join-Path $Target 'python\python.exe'))) {
  if (-not (Test-Path $PySrc)) { throw "缺少 Python 运行时：$PySrc" }
  robocopy $PySrc (Join-Path $Target 'python') /E /NFL /NDL /NJH /NJS /NP | Out-Null
  if ($LASTEXITCODE -ge 8) { throw "复制 Python 失败：$LASTEXITCODE" }
}

$Exclude = @('data', '.git', '__pycache__', 'python', 'install.log')
robocopy $AppSrc $Target /E /XD $Exclude /XF *.bak /NFL /NDL /NJH /NJS /NP | Out-Null
if ($LASTEXITCODE -ge 8) { throw "复制程序失败：$LASTEXITCODE" }

$Launch = @'
@echo off
cd /d "%~dp0"
start "" "%~dp0python\python.exe" webapp.py --no-browser
'@
Set-Content -Path (Join-Path $Target 'start-zhiheng.bat') -Value $Launch -Encoding ascii

if (-not $NoShortcut) {
  $desktop = [Environment]::GetFolderPath('Desktop')
  $lnk = Join-Path $desktop '知衡.lnk'
  $w = New-Object -ComObject WScript.Shell
  $s = $w.CreateShortcut($lnk)
  $s.TargetPath = Join-Path $Target 'start-zhiheng.bat'
  $s.WorkingDirectory = $Target
  $s.Description = '知衡 · 本机 AI'
  $icon = Join-Path $Target 'web\zhiheng.ico'
  if (Test-Path $icon) { $s.IconLocation = "$icon,0" }
  $s.Save()
  Log "shortcut $lnk"
}

Log 'done'
Write-Host "已安装/升级到：$Target"
Write-Host "启动：$Target\start-zhiheng.bat"
