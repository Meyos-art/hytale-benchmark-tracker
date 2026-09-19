$ErrorActionPreference = "Stop"

Set-Location -LiteralPath $PSScriptRoot

$pythonCommand = Get-Command py -ErrorAction SilentlyContinue
if ($pythonCommand) {
    $pythonExecutable = "py"
    $pythonArguments = @("-3")
} else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw "Python 3.10 or later was not found. Install Python, then run this script again."
    }
    $pythonExecutable = "python"
    $pythonArguments = @()
}

if (-not (Test-Path -LiteralPath ".venv")) {
    & $pythonExecutable @pythonArguments -m venv .venv
}

& ".\.venv\Scripts\python.exe" -m pip install --upgrade "pip==26.2.0"
& ".\.venv\Scripts\python.exe" -m pip install --editable .

if (-not (Test-Path -LiteralPath "config.json")) {
    Copy-Item -LiteralPath "config.example.json" -Destination "config.json"
    Write-Host "Created config.json from config.example.json."
}

Write-Host ""
Write-Host "Installation complete."
Write-Host "1. Edit config.json."
Write-Host "2. Add your Google service account JSON file."
Write-Host "3. Run: .\.venv\Scripts\python.exe log_to_sheets.py"
