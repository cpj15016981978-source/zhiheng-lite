# -*- coding: utf-8 -*-
param(
  [string]$Target = (Join-Path $env:LOCALAPPDATA 'Programs\Zhiheng'),
  [switch]$RemoveData
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path $Target)) { throw "未安装：$Target" }
$lnk = Join-Path ([Environment]::GetFolderPath('Desktop')) '知衡.lnk'
if (Test-Path $lnk) { Remove-Item $lnk -Force }
Remove-Item $Target -Recurse -Force
if ($RemoveData) {
  $Data = Join-Path $env:LOCALAPPDATA 'Zhiheng'
  if (Test-Path $Data) { Remove-Item $Data -Recurse -Force }
  Write-Host "已删除程序与用户数据：$Data"
} else {
  Write-Host "已删除程序；用户数据保留在：$(Join-Path $env:LOCALAPPDATA 'Zhiheng')"
}
Write-Host "卸载完成：$Target"
