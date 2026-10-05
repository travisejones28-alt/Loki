param([switch]$SkipInstall, [switch]$SkipTests)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if ($env:OS -ne "Windows_NT") { throw "Build Loki.exe on Windows. PyInstaller cannot cross-compile a Windows executable from Linux." }

Push-Location $PSScriptRoot
try {
    $LokiPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $LokiPython)) {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            & py -3.12 -m venv .venv
        } elseif (Get-Command python -ErrorAction SilentlyContinue) {
            & python -m venv .venv
        } else {
            throw "Install Python 3.12 for Windows with the Python launcher, then run this command again."
        }
        if ($LASTEXITCODE -ne 0) { throw "Could not create the Python environment. Install Python 3.12 and try again." }
    }
    if (-not $SkipInstall) {
        & $LokiPython -m pip install -r requirements-dev.txt
        if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
    }
    & $LokiPython tools\generate_resources.py
    if ($LASTEXITCODE -ne 0) { throw "Resource generation failed." }
    if (-not $SkipTests) {
        $PreviousQtPlatform = $env:QT_QPA_PLATFORM
        try {
            $env:QT_QPA_PLATFORM = "offscreen"
            & $LokiPython -m pytest -q
            if ($LASTEXITCODE -ne 0) { throw "Tests failed; no executable was built." }
            & $LokiPython -m ruff check .
            if ($LASTEXITCODE -ne 0) { throw "Source checks failed; no executable was built." }
            & $LokiPython -m ruff format --check .
            if ($LASTEXITCODE -ne 0) { throw "Formatting checks failed; no executable was built." }
        } finally { $env:QT_QPA_PLATFORM = $PreviousQtPlatform }
    }
    & $LokiPython -m PyInstaller --noconfirm --clean Loki.spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

    Copy-Item -LiteralPath README.md -Destination dist\Loki\README.md -Force
    $SmokeFolder = Join-Path $env:TEMP ("Loki-Build-Smoke-" + [guid]::NewGuid().ToString("N"))
    $PreviousQtPlatform = $env:QT_QPA_PLATFORM
    try {
        $env:QT_QPA_PLATFORM = "offscreen"
        $SmokeProcess = Start-Process -FilePath (Join-Path $PSScriptRoot "dist\Loki\Loki.exe") -ArgumentList @("--smoke-test", "--data-dir", ('"' + $SmokeFolder + '"')) -PassThru
        if (-not $SmokeProcess.WaitForExit(30000)) {
            $SmokeProcess.Kill()
            throw "Packaged GUI did not finish its smoke test within 30 seconds."
        }
        if ($SmokeProcess.ExitCode -ne 0) { throw "Packaged GUI smoke test failed." }
    } finally {
        $env:QT_QPA_PLATFORM = $PreviousQtPlatform
        if (Test-Path -LiteralPath $SmokeFolder) { Remove-Item -LiteralPath $SmokeFolder -Recurse -Force }
    }
    Compress-Archive -Path dist\Loki -DestinationPath dist\Loki-Windows.zip -Force
    Write-Host "Built and checked: dist\Loki\Loki.exe"
    Write-Host "Share: dist\Loki-Windows.zip (extract the whole folder before running)"
} finally { Pop-Location }

