$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'EVA.exe'))) {
    throw 'Use Install_EVA.bat from a complete release. Developers can rebuild with build_release.py in the source bundle.'
}
Write-Host 'EVA.exe is supplied and verified by the release installer. No local build is needed.'
