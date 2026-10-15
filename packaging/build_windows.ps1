param([string]$Python = "", [string]$DistributionSubdir = "dist")
$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$distributionRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot $DistributionSubdir))
$workRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot "build"))
foreach ($targetPath in @($distributionRoot, $workRoot)) {
    if (-not $targetPath.StartsWith($projectRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Build output is outside the project workspace: $targetPath"
    }
}
$bundleRoot = Join-Path $distributionRoot "MiniCodex"
$executable = Join-Path $bundleRoot "MiniCodex.exe"
$active = Get-Process -Name MiniCodex -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $executable }
if ($active) { throw "Target application is running. Use -DistributionSubdir dist/<version> to build separately." }
if (-not $Python) { $Python = Join-Path $projectRoot ".venv\Scripts\python.exe" }
$originalPath = $env:PATH
$pythonDirectory = Split-Path -Parent (Get-Command $Python -ErrorAction Stop).Source
# Avoid collecting unrelated native DLLs from developer tool bundles on PATH.
# Qt's Windows ICU dependency must resolve against Windows, not a Poppler ICU build.
$env:PATH = "$pythonDirectory;$env:SystemRoot\System32;$env:SystemRoot"
Push-Location $projectRoot
try {
    & $Python -m PyInstaller --noconfirm --clean --onedir --windowed --name MiniCodex --paths $projectRoot --distpath $distributionRoot --workpath $workRoot --specpath $workRoot packaging/windows/entry.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed" }
    & $Python packaging/collect_licenses.py --output $bundleRoot
    if ($LASTEXITCODE -ne 0) { throw "License collection failed" }
    Copy-Item -LiteralPath README.md -Destination (Join-Path $bundleRoot "README.md")
    Copy-Item -LiteralPath docs/security.md -Destination (Join-Path $bundleRoot "SECURITY.md")
    Copy-Item -LiteralPath docs/m1-status.md -Destination (Join-Path $bundleRoot "M1-STATUS.md")
    Copy-Item -LiteralPath docs/m2-status.md -Destination (Join-Path $bundleRoot "M2-STATUS.md")
    Copy-Item -LiteralPath docs/m3-status.md -Destination (Join-Path $bundleRoot "M3-STATUS.md")
    Copy-Item -LiteralPath docs/m4-status.md -Destination (Join-Path $bundleRoot "M4-STATUS.md")
    Copy-Item -LiteralPath docs/ui-v0.5.3.md -Destination (Join-Path $bundleRoot "UI-UPDATE.md")
    Copy-Item -LiteralPath docs/docker-sandbox.md -Destination (Join-Path $bundleRoot "DOCKER-SANDBOX.md")
    Copy-Item -LiteralPath docs/release-v0.5.4.md -Destination (Join-Path $bundleRoot "RELEASE-NOTES.md")
    Write-Output "Built: $executable"
} finally {
    $env:PATH = $originalPath
    Pop-Location
}
