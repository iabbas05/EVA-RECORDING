$ErrorActionPreference = 'Stop'
$exe = Join-Path $PSScriptRoot 'EVA.exe'
if (-not (Test-Path -LiteralPath $exe)) { throw 'Extract the complete EVA release and run Install_EVA.bat first.' }
Start-Process -FilePath $exe -WindowStyle Hidden
