param([string]$BundleDirectory = 'dist/0.5.3/MiniCodex', [string]$Compiler = '', [switch]$TestBuild)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$bundleRoot = [IO.Path]::GetFullPath((Join-Path $projectRoot $BundleDirectory))
if (-not $bundleRoot.StartsWith($projectRoot + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Bundle escaped workspace' }
if (-not (Test-Path -LiteralPath (Join-Path $bundleRoot 'MiniCodex.exe'))) { throw 'Build the directory bundle first' }
if (-not $Compiler) {
    $localCompiler = Join-Path $projectRoot '.tools\inno-compiler\ISCC.exe'
    if (Test-Path -LiteralPath $localCompiler) { $Compiler = $localCompiler }
    else { $Compiler = (Get-Command ISCC.exe -ErrorAction Stop).Source }
}
$projectText = Get-Content -LiteralPath (Join-Path $projectRoot 'pyproject.toml') -Raw
$version = [regex]::Match($projectText, '(?m)^version = "([0-9.]+)"').Groups[1].Value
if (-not $version) { throw 'Missing project version' }
$outputRoot = Join-Path $projectRoot ('dist\' + $version)
if ($TestBuild) { $outputRoot = Join-Path $projectRoot 'build\installer-check' }
New-Item -ItemType Directory -Path $outputRoot -Force | Out-Null
$compilerArgs = @('/Qp', ('/DAppVersion=' + $version), ('/DBundleDir=' + $bundleRoot), ('/DOutputRoot=' + $outputRoot))
if ($TestBuild) { $compilerArgs += '/DTestBuild=1' }
$compilerArgs += (Join-Path $projectRoot 'packaging\windows\installer.iss')
& $Compiler @compilerArgs
if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed' }
Write-Output ('Installer output: ' + $outputRoot)
