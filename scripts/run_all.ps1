# One-shot for Windows PowerShell: install, lint, test, run the experiment pipeline and the API demo.
# Usage (from the project folder):  powershell -ExecutionPolicy Bypass -File scripts\run_all.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Invoke-Step([string]$Title, [scriptblock]$Block) {
    Write-Host "== $Title =="
    & $Block
    if ($LASTEXITCODE -ne 0) { throw "$Title failed (exit code $LASTEXITCODE)" }
}

# Find Python 3.11+: the "py" launcher first, then python / python3 on PATH.
$candidates = @(
    @{ Exe = "py"; Pre = @("-3") },
    @{ Exe = "python"; Pre = @() },
    @{ Exe = "python3"; Pre = @() }
)
$py = $null
foreach ($c in $candidates) {
    if (-not (Get-Command $c.Exe -ErrorAction SilentlyContinue)) { continue }
    $pre = $c.Pre
    $ok = & $c.Exe @pre -c "import sys; print(sys.version_info >= (3, 11))" 2>$null
    if ($ok -eq "True") { $py = $c; break }
}
if (-not $py) {
    throw "Python 3.11 or newer was not found. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), or use WSL / Google Colab (see docs/setup.md)."
}
$pre = $py.Pre
if (-not (Test-Path ".venv")) {
    Invoke-Step "create virtual environment" { & $py.Exe @pre -m venv .venv }
}
$vpy = Join-Path (Get-Location) ".venv\Scripts\python.exe"

Invoke-Step "install" {
    & $vpy -m pip install --quiet --upgrade pip
    if ($LASTEXITCODE -eq 0) { & $vpy -m pip install --quiet -c constraints.txt -e ".[dev]" }
}
# Windows Smart App Control / Application Control can refuse to load the compiled libraries that
# pip installs (pydantic-core, numpy, scikit-learn, OR-Tools, mypy). Detect that before running.
$ErrorActionPreference = "Continue"
$probe = (& $vpy -c "import pydantic_core, numpy, sklearn, ortools, mypy; print('native-libraries-ok')" 2>&1 | Out-String)
$ErrorActionPreference = "Stop"
if ($probe -match "Application Control policy") {
    Write-Host ""
    Write-Host "Windows Smart App Control / Application Control is blocking the compiled Python libraries" -ForegroundColor Yellow
    Write-Host "(pydantic, numpy, scikit-learn, OR-Tools). This is a Windows security setting, not a project" -ForegroundColor Yellow
    Write-Host "error, and FlexDrop cannot run natively on this PC while it is on. Use one of:" -ForegroundColor Yellow
    Write-Host "  1. WSL:   wsl   then   sudo apt update && sudo apt install -y python3 python3-venv python3-pip"
    Write-Host "            then run   bash scripts/run_all.sh   inside the project folder (see docs\setup.md)"
    Write-Host "  2. Google Colab: upload the ZIP and follow the Colab section of docs\setup.md"
    exit 2
}

Invoke-Step "lint" { & $vpy -m ruff check src tests migrations scripts }
Invoke-Step "type-check" { & $vpy -m mypy }
Invoke-Step "tests" { & $vpy -m pytest -q }
Invoke-Step "experiment pipeline" { & $vpy -m flexdrop --log-level WARNING pipeline --data data/sample --models models --reports reports }
Invoke-Step "API demo" { & $vpy -m flexdrop --log-level WARNING demo --models models }
Write-Host "done: see reports\SUMMARY.md"
Write-Host "browser prototype: run  .venv\Scripts\python.exe -m flexdrop studio  and open http://127.0.0.1:8000/app/"
