# 一键启动（PowerShell）。用法：  .\scripts\dev.ps1
# 默认：构建前端 → 起 uvicorn（同一端口同时提供页面 / API / WS）
# 参数：-Port 8000  -NoBuild  -Reload
param(
    [int]$Port = 8000,
    [switch]$NoBuild,
    [switch]$Reload
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw "找不到虚拟环境：$py" }

if (-not $NoBuild) {
    Write-Host '[1/2] 构建前端 web/dist ...' -ForegroundColor Cyan
    Push-Location (Join-Path $root 'web')
    try {
        & pnpm build
        if ($LASTEXITCODE -ne 0) { throw '前端构建失败' }
    } finally { Pop-Location }
}

Write-Host "[2/2] 启动服务 http://127.0.0.1:$Port/" -ForegroundColor Cyan
$uvArgs = @('-m', 'generals', '--port', $Port)
if ($Reload) { $uvArgs += '--reload' }
Push-Location $root
try {
    & $py @uvArgs
} finally { Pop-Location }
