$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = Join-Path $repoRoot ".venv_latest\Scripts\python.exe"

if (!(Test-Path -LiteralPath $pythonExe)) {
    throw "Missing .venv_latest. Build it first."
}

Set-Location -LiteralPath $repoRoot
& $pythonExe "app.py"
