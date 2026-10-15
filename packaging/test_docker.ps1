param(
    [string]$Python = '',
    [string]$Image = 'python:3.12-slim',
    [switch]$PullImage,
    [string]$Report = ''
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $Python) { $Python = Join-Path $projectRoot '.venv\Scripts\python.exe' }
$originalPath = $env:PATH
$originalDockerTests = $env:MINICODEX_DOCKER_TESTS
$originalDockerImage = $env:MINICODEX_DOCKER_IMAGE
$originalBytecode = $env:PYTHONDONTWRITEBYTECODE
$originalQt = $env:QT_QPA_PLATFORM
Push-Location $projectRoot
try {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        $dockerBins = @(
            (Join-Path $env:LOCALAPPDATA 'Programs\DockerDesktop\resources\bin'),
            (Join-Path $env:ProgramFiles 'Docker\Docker\resources\bin')
        )
        foreach ($dockerBin in $dockerBins) {
            if (Test-Path -LiteralPath (Join-Path $dockerBin 'docker.exe')) {
                $env:PATH = $dockerBin + ';' + $env:PATH
                break
            }
        }
    }
    $docker = (Get-Command docker -ErrorAction Stop).Source
    $backend = & $docker info --format '{{.OSType}}'
    if ($LASTEXITCODE -ne 0 -or $backend.Trim() -ne 'linux') {
        throw 'Start Docker Desktop with its Linux engine before testing.'
    }
    if ($PullImage) {
        & $docker pull $Image
        if ($LASTEXITCODE -ne 0) { throw 'Explicit image preparation failed.' }
    }
    & $docker image inspect --format '{{.Id}}' $Image
    if ($LASTEXITCODE -ne 0) { throw 'Local image missing. Prepare it with -PullImage first.' }
    $env:MINICODEX_DOCKER_TESTS = '1'
    $env:MINICODEX_DOCKER_IMAGE = $Image
    $env:PYTHONDONTWRITEBYTECODE = '1'
    $env:QT_QPA_PLATFORM = 'offscreen'
    $pytestArgs = @('-B', '-m', 'pytest', '-q', '--tb=short', '-p', 'no:cacheprovider',
        'tests/unit/test_docker.py', 'tests/integration/test_docker_sandbox.py')
    if ($Report) { $pytestArgs += @('--junitxml', $Report) }
    & $Python @pytestArgs
    if ($LASTEXITCODE -ne 0) { throw 'Docker sandbox verification failed.' }
} finally {
    $env:PATH = $originalPath
    $env:MINICODEX_DOCKER_TESTS = $originalDockerTests
    $env:MINICODEX_DOCKER_IMAGE = $originalDockerImage
    $env:PYTHONDONTWRITEBYTECODE = $originalBytecode
    $env:QT_QPA_PLATFORM = $originalQt
    Pop-Location
}
