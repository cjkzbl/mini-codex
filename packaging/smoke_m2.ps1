param([string]$BundleDirectory = "dist/0.5.4/MiniCodex", [string]$ProjectPython = "")
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $ProjectPython) { $ProjectPython = Join-Path $projectRoot ".venv\Scripts\python.exe" }
$executable = Join-Path (Join-Path $projectRoot $BundleDirectory) "MiniCodex.exe"
$report = Join-Path $projectRoot "artifacts\m2-package-smoke.json"
$preview = Join-Path $projectRoot "artifacts\m2-package-preview.png"
if (-not (Test-Path -LiteralPath $executable)) { throw "Build MiniCodex first" }
if (-not (Test-Path -LiteralPath $ProjectPython)) { throw "Target project Python is missing" }
$originalPath = $env:PATH
$originalQt = $env:QT_QPA_PLATFORM
try {
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
    $env:QT_QPA_PLATFORM = "offscreen"
    $process = Start-Process -FilePath $executable -ArgumentList @("--smoke-test", "`"$report`"", "--screenshot", "`"$preview`"", "--smoke-m2", "--smoke-python", "`"$ProjectPython`"") -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(30000)) { $process.Kill(); throw "M2 package smoke timed out" }
    if ($process.ExitCode -ne 0) { throw "M2 package smoke failed: $($process.ExitCode)" }
    $result = Get-Content -LiteralPath $report -Encoding UTF8 -Raw | ConvertFrom-Json
    if (-not $result.passed -or -not $result.frozen -or -not $result.m2 -or $result.approvals_submitted -ne 2) { throw "Invalid M2 smoke report" }
    $result | Format-List
} finally {
    $env:PATH = $originalPath
    $env:QT_QPA_PLATFORM = $originalQt
}
