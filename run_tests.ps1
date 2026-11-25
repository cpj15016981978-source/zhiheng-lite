# 吹雪后端测试入口（环境固化版 · 2026-11-17）
# 解决：WorkBuddy 默认解释器缺 zhconv/tkinter，跑测试收集期假 FAIL 的问题。
# 做法：先调 tools/test_env.py 自动选一个依赖齐全的解释器，再用它跑测试。
#
# 用法：
#   .\run_tests.ps1                      # 自检 + 全量测试
#   .\run_tests.ps1 -Quick               # 自检 + 核心档（跳过已知 flaky 的 agentd）
#   .\run_tests.ps1 -EnvOnly             # 只做环境自检，不跑测试（排查用）
#   .\run_tests.ps1 -Filter test_persona # 只跑匹配模块名的测试
#   $env:PAI_TEST_PYTHON="D:\xxx\python.exe"; .\run_tests.ps1   # 手动指定解释器
param(
    [switch]$Quick,
    [switch]$EnvOnly,
    [string]$Filter = ""
)

$ErrorActionPreference = "Stop"
$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ROOT

function Fail($msg) { Write-Output "FAIL: $msg"; exit 1 }

# 1) 环境自检：用系统 python 跑 test_env.py（它只依赖标准库）
$probe = & python "$ROOT\tools\test_env.py" --profile backend --json 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Output $probe
    Fail "测试环境自检失败：没有依赖齐全的解释器（见上方各候选缺口）"
}
$envInfo = $probe | ConvertFrom-Json
$PY = $envInfo.selected
Write-Output "选用解释器: $PY"
Write-Output ("必需模块: " + ($envInfo.required_modules -join ", "))
Write-Output ("锁状态: " + $(if ($envInfo.selected_lock_ok) { "OK（与 python.lock.json 一致）" } else { "⚠ 版本与锁不符" }))
if ($EnvOnly) { Write-Output "PASS (仅环境自检)"; exit 0 }

# 2) 跑测试
if ($Filter) {
    # 指定模块：unittest 模块名形式，最快
    $args = @("-m", "unittest", "tests.$Filter", "-v")
} elseif ($Quick) {
    # Quick 档：唯一名单在 gates.json；不再维护第二份 CORE 名单。
    $args = @("$ROOT\tools\gates.py", "--tier", "quick")
} else {
    $args = @("$ROOT\tools\gates.py", "--tier", "full")
}
Write-Output ("执行: " + $PY + " " + ($args -join " "))
Write-Output "────────────────────────────────────────"

# unittest 的进度输出走 stderr；Stop 模式下 PowerShell 会把它当终止错误刷红字。
# 调用原生 Python 时临时切 Continue，退出码仍由 $LASTEXITCODE 如实判定。
$prevEAP = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& $PY @args
$rc = $LASTEXITCODE
$ErrorActionPreference = $prevEAP

Write-Output "────────────────────────────────────────"
if ($rc -eq 0) {
    Write-Output "PASS: 后端测试通过"
} else {
    Write-Output "FAIL: 后端测试有失败项（退出码 $rc）"
    Write-Output "提示：若失败集中在 test_agentd，重试或加 -Filter 单跑定位（该用例偶发 flaky）"
    Write-Output "提示：若报缺 docx/vosk/zhconv，是能力依赖未装，见 requirements.txt"
    Write-Output "提示：契约缺口（jarvis/meta/stream_asr/mail_calendar 未登记 CAPABILITY）是代码待办，非环境问题"
}
exit $rc
