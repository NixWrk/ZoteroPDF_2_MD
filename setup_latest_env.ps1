param(
    [switch]$Recreate
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$venvDir = Join-Path $repoRoot ".venv_latest"
$pythonExe = Join-Path $venvDir "Scripts\python.exe"
$tmpDir = Join-Path $repoRoot ".tmp_local2"

if ($Recreate -and (Test-Path -LiteralPath $venvDir)) {
    Remove-Item -LiteralPath $venvDir -Recurse -Force
}

if (!(Test-Path -LiteralPath $tmpDir)) {
    New-Item -ItemType Directory -Path $tmpDir | Out-Null
}
$env:TEMP = $tmpDir
$env:TMP = $tmpDir

if (!(Test-Path -LiteralPath $pythonExe)) {
    python -m venv $venvDir --without-pip
    & $pythonExe -m ensurepip --upgrade
}

& $pythonExe -m pip install --upgrade pip setuptools wheel
& $pythonExe -m pip install --upgrade torch --index-url https://download.pytorch.org/whl/cu128
& $pythonExe -m pip install --upgrade marker-pdf transformers accelerate huggingface_hub
& $pythonExe -m pip install -e $repoRoot

& $pythonExe -c "import torch,transformers,huggingface_hub,accelerate,importlib.metadata as m; print('torch',torch.__version__); print('transformers',transformers.__version__); print('huggingface_hub',huggingface_hub.__version__); print('accelerate',accelerate.__version__); print('marker-pdf',m.version('marker-pdf')); print('surya-ocr',m.version('surya-ocr'))"
