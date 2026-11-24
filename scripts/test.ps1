# 全量校验（PowerShell）。用法：  .\scripts\test.ps1
# 依次执行：后端 pytest → 前端类型检查 → 前端构建 → 协议级端到端 → 回放页面端到端
param(
    [switch]$SkipE2E
)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root '.venv\Scripts\python.exe'
$failed = @()

function Step($name, [scriptblock]$body) {
    Write-Host "`n=== $name ===" -ForegroundColor Cyan
    & $body
    if ($LASTEXITCODE -ne 0) { $script:failed += $name }
}

Push-Location $root
try {
    Step '后端 pytest' { & $py -m pytest server/tests -q }

    Push-Location (Join-Path $root 'web')
    try {
        Step '前端类型检查' { & pnpm exec tsc --noEmit }
        Step '前端构建' { & pnpm build }
    } finally { Pop-Location }

    if (-not $SkipE2E) {
        Step '端到端冒烟' { & $py scripts\e2e_smoke.py --port 8123 --ticks 20 }
        # 回放：自造样本回放 → 起服务 → 真浏览器播放并断言（docs/REPLAY.md §5.5）。
        Step '回放端到端' { & $py scripts\e2e_replay.py --port 8124 }
    }
} finally { Pop-Location }

Write-Host ''
if ($failed.Count -eq 0) {
    Write-Host '全部通过 ✅' -ForegroundColor Green
    exit 0
} else {
    Write-Host ("失败步骤：" + ($failed -join ', ')) -ForegroundColor Red
    exit 1
}
