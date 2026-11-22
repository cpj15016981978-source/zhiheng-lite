param(
  [string]$Source = "D:\DSH\dist\ZhihengLite-0.3.0-win64.zip",
  [string]$OutDir = "D:\DSH\dist",
  [long]$ChunkBytes = 1500000000
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path $Source)) { throw "missing $Source" }
$base = Join-Path $OutDir (Split-Path $Source -Leaf)
Get-ChildItem $OutDir -Filter ((Split-Path $Source -Leaf) + ".part*") -ErrorAction SilentlyContinue | Remove-Item -Force
$in = [System.IO.File]::OpenRead($Source)
try {
  $buf = New-Object byte[] (4MB)
  $part = 1
  $written = 0
  $outPath = "$base.part$part"
  $out = [System.IO.File]::Create($outPath)
  try {
    while (($n = $in.Read($buf, 0, $buf.Length)) -gt 0) {
      if ($written + $n -gt $ChunkBytes) {
        $room = [int]($ChunkBytes - $written)
        if ($room -gt 0) { $out.Write($buf, 0, $room); $written += $room }
        $out.Close()
        $part++
        $written = 0
        $outPath = "$base.part$part"
        $out = [System.IO.File]::Create($outPath)
        if ($room -lt $n) { $out.Write($buf, $room, $n - $room); $written += ($n - $room) }
      } else {
        $out.Write($buf, 0, $n); $written += $n
      }
    }
  } finally { $out.Close() }
} finally { $in.Close() }
Get-ChildItem $OutDir -Filter ((Split-Path $Source -Leaf) + ".part*") |
  ForEach-Object { "{0} {1}" -f $_.Name, $_.Length }
