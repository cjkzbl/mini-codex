param([string]$BundleDirectory = "dist/0.5.5/MiniCodex")
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$executable = Join-Path (Join-Path $projectRoot $BundleDirectory) "MiniCodex.exe"
$report = Join-Path $projectRoot "artifacts\package-smoke.json"
$preview = Join-Path $projectRoot "artifacts\package-preview.png"
if (-not (Test-Path -LiteralPath $executable)) { throw "Build MiniCodex first" }
if (Test-Path -LiteralPath $report) { Remove-Item -LiteralPath $report }
$originalPath = $env:PATH
$originalQt = $env:QT_QPA_PLATFORM
try {
    # A self-contained distribution must not depend on the venv or Python on PATH.
    $env:PATH = "$env:SystemRoot\System32;$env:SystemRoot"
    $env:QT_QPA_PLATFORM = "offscreen"
    $process = Start-Process -FilePath $executable -ArgumentList @("--smoke-test", "`"$report`"", "--screenshot", "`"$preview`"") -WindowStyle Hidden -PassThru
    if (-not $process.WaitForExit(30000)) {
        $process.Kill()
        throw "Packaged application timed out"
    }
    if ($process.ExitCode -ne 0) {
        if (Test-Path -LiteralPath $report) { Get-Content -LiteralPath $report -Encoding UTF8 -Raw | Write-Output }
        throw "Packaged application failed: $($process.ExitCode)"
    }
    $result = Get-Content -LiteralPath $report -Encoding UTF8 -Raw | ConvertFrom-Json
    if (-not $result.passed -or -not $result.frozen) { throw "Invalid package smoke result" }
    $result | Format-List
} finally {
    $env:PATH = $originalPath
    $env:QT_QPA_PLATFORM = $originalQt
}
