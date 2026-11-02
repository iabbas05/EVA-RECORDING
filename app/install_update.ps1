param(
    [string]$PackagePath = '',
    [string]$Target = (Join-Path $env:LOCALAPPDATA 'EVARecordingManager'),
    [int]$WaitPid = 0,
    [switch]$NoLaunch,
    [switch]$NoShortcut,
    [switch]$SkipFirewall,
    [ValidateRange(1,300)][int]$StartupTimeoutSeconds = 60,
    [ValidateSet('','obs_game','streamlabs_game')][string]$RecordingLayout = ''
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem
$Target = [IO.Path]::GetFullPath($Target)
# All mutation paths are checked before any file operations.
if ($Target -eq [IO.Path]::GetPathRoot($Target) -or $Target -eq [Environment]::GetFolderPath('UserProfile')) {
    throw 'Unsafe installation target.'
}
$allowed = @('EVA.exe','app_paths.py','manager.py','matching.py','obs_control.py','service.py','storage.py',
    'index.html','requirements.txt','requirements.lock.txt','updater.py','streamlabs_control.py','eva_portal.py',
    'google_delivery.py','diagnostics.py','version.txt','eva.ico','desktop.py','dual_recorder.py','capture_files.py',
    'delivery_queue.py','recorder_repair.py','self_test.py','install_update.ps1','Install_EVA.bat','README.md',
    'GOOGLE_SETUP.md','TEST_CHECKLIST.md','release.json','media_check.py','THIRD_PARTY_NOTICES.md',
    'START_EVA_MANAGER.bat','setup_and_run.ps1','start_streaming_pc.ps1','build_desktop.ps1')
$stage = Join-Path ([IO.Path]::GetTempPath()) ('eva-release-' + [guid]::NewGuid().ToString('N'))
$backup = Join-Path $Target ('updates\backups\' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8))
$changed = [Collections.Generic.List[string]]::new()
$smoke = $null
$wasInstalled = Test-Path (Join-Path $Target 'EVA.exe')
$committed = $false
$settingsChanged = $false
$settingsFile = Join-Path $Target 'settings.json'
$settingsExisted = Test-Path -LiteralPath $settingsFile
$settingsBytes = $null
$envBefore = $env:EVA_UPDATE_VALIDATING
$dataBefore = $env:EVA_DATA_DIR
function Get-EvaHash {
    param([string]$Path)
    $stream = [IO.File]::OpenRead($Path)
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-','').ToLowerInvariant() }
    finally { $stream.Dispose(); $sha.Dispose() }
}
function Stop-OwnedSmoke {
    param($Process)
    if ($Process -and -not $Process.HasExited) {
        # One-file EVA has a bootloader child: stop only this installer-owned process tree.
        & taskkill.exe /PID $Process.Id /T /F *> $null
        $Process.WaitForExit(10000) | Out-Null
    }
}
try {
    New-Item -ItemType Directory -Path $stage | Out-Null
    if ($PackagePath) {
        $package = [IO.Path]::GetFullPath($PackagePath)
        if ((Get-Item -LiteralPath $package).Length -gt 256MB) { throw 'EVA package exceeds 256 MiB.' }
        $zip = [IO.Compression.ZipFile]::OpenRead($package)
        try {
            $seen = @{}
            $total = 0L
            foreach ($entry in $zip.Entries) {
                if ($entry.FullName -notin $allowed -or $seen.ContainsKey($entry.FullName)) { throw 'Unexpected or duplicate file in package.' }
                $seen[$entry.FullName] = $true
                $total += $entry.Length
                if ($total -gt 512MB) { throw 'Unpacked update exceeds 512 MiB.' }
                [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, (Join-Path $stage $entry.FullName), $false)
            }
        } finally { $zip.Dispose() }
    } else {
        foreach ($name in $allowed) {
            $source = Join-Path $PSScriptRoot $name
            if (Test-Path -LiteralPath $source) { Copy-Item -LiteralPath $source -Destination (Join-Path $stage $name) }
        }
    }
    $release = Get-Content -LiteralPath (Join-Path $stage 'release.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($release.version -notmatch '^\d+\.\d+\.\d+$') { throw 'Invalid release version.' }
    if ((Get-Content -LiteralPath (Join-Path $stage 'version.txt') -Raw).Trim() -ne $release.version) { throw 'Packaged version mismatch.' }
    $names = @($release.files.PSObject.Properties.Name)
    $actual = @(Get-ChildItem -LiteralPath $stage -File | Where-Object Name -ne 'release.json' | Select-Object -ExpandProperty Name)
    if (Compare-Object $names $actual) { throw 'Manifest file list does not match payload.' }
    foreach ($name in $names) {
        if ($name -notin $allowed) { throw 'Unexpected manifest file.' }
        $hash = Get-EvaHash (Join-Path $stage $name)
        if ($hash -ne $release.files.$name) { throw "Checksum mismatch: $name" }
    }
    foreach ($name in @('EVA.exe','manager.py','index.html','desktop.py','version.txt','install_update.ps1')) {
        if ($name -notin $names) { throw "Required release file missing: $name" }
    }
    $report = Join-Path $stage 'self-test.json'
    $verification = Start-Process -FilePath (Join-Path $stage 'EVA.exe') -ArgumentList @('--self-test','--output',('"' + $report + '"')) -WindowStyle Hidden -PassThru
    if (-not $verification.WaitForExit(120000)) { $verification.Kill(); throw 'Packaged self-test timed out.' }
    if ($verification.ExitCode -ne 0 -or -not (Test-Path $report)) { throw 'Packaged EVA self-test failed.' }
    $verified = Get-Content -LiteralPath $report -Raw -Encoding UTF8 | ConvertFrom-Json
    if (-not $verified.ok -or $verified.version -ne $release.version) { throw 'Executable contains a different version or failed checks.' }
    if (-not $verified.resource_hashes) { throw 'Executable did not report its embedded source/resource checksums.' }
    foreach ($property in $verified.resource_hashes.PSObject.Properties) {
        if ($property.Name -notin $names -or $release.files.($property.Name) -ne $property.Value) {
            throw "Executable resource differs from packaged source: $($property.Name)"
        }
    }
    if ($WaitPid -gt 0) {
        $oldProcess = Get-Process -Id $WaitPid -ErrorAction SilentlyContinue
        if ($oldProcess -and -not $oldProcess.WaitForExit(60000)) { throw 'EVA did not close. Stop recording and close EVA, then run this update again.' }
        foreach ($process in @(Get-Process -Name EVA -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq (Join-Path $Target 'EVA.exe') })) {
            if (-not $process.WaitForExit(30000)) { throw 'Close all EVA windows before updating.' }
        }
    } else {
        $running = @(Get-Process -Name EVA -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq (Join-Path $Target 'EVA.exe') })
        if ($running.Count) {
            try {
                $result = Invoke-RestMethod 'http://127.0.0.1:8765/api/update/prepare' -Method Post -Headers @{Authorization='Bearer EVA-JH'} -ContentType 'application/json' -Body '{}' -TimeoutSec 15
                if (-not $result.ok) { throw 'EVA refused to prepare for update.' }
                foreach ($process in $running) { if (-not $process.WaitForExit(30000)) { throw 'EVA did not exit.' } }
            } catch { throw 'Stop recording and close EVA before running this update. Active sessions are never terminated by the installer.' }
        }
    }
    if (Test-Path (Join-Path $Target 'version.txt')) {
        $oldVersion = (Get-Content -LiteralPath (Join-Path $Target 'version.txt') -Raw).Trim()
        if ($oldVersion -match '^\d+\.\d+\.\d+$' -and [version]$oldVersion -gt [version]$release.version) { throw 'Downgrades are blocked. Use the saved backup for rollback.' }
    }
    New-Item -ItemType Directory -Force -Path $Target,$backup | Out-Null
    foreach ($name in $names) {
        $destination = [IO.Path]::GetFullPath((Join-Path $Target $name))
        if (-not $destination.StartsWith($Target + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Target escaped installation directory.' }
        if (Test-Path -LiteralPath $destination -PathType Container) { throw "A folder conflicts with application file: $name" }
        if (Test-Path -LiteralPath $destination) { Copy-Item -LiteralPath $destination -Destination (Join-Path $backup $name) }
        $changed.Add($name)
        Copy-Item -LiteralPath (Join-Path $stage $name) -Destination $destination -Force
    }
    if (Test-Path -LiteralPath (Join-Path $Target 'release.json')) { Copy-Item -LiteralPath (Join-Path $Target 'release.json') -Destination (Join-Path $backup 'release.json') }
    $changed.Add('release.json')
    Copy-Item -LiteralPath (Join-Path $stage 'release.json') -Destination (Join-Path $Target 'release.json') -Force
    # Preserve a consistent, offline copy of the SQLite database and its WAL before new-code startup.
    foreach ($name in @('eva_recording_manager.sqlite3','eva_recording_manager.sqlite3-wal','eva_recording_manager.sqlite3-shm','settings.json','mode.txt')) {
        if (Test-Path -LiteralPath (Join-Path $Target $name)) { Copy-Item -LiteralPath (Join-Path $Target $name) -Destination (Join-Path $backup $name) }
    }
    foreach ($name in $names) {
        if ((Get-EvaHash (Join-Path $Target $name)) -ne $release.files.$name) { throw "Applied file verification failed: $name" }
    }
    # Startup smoke runs against isolated data, and never signs in, records, or sends email.
    $env:EVA_UPDATE_VALIDATING = '1'
    $env:EVA_DATA_DIR = Join-Path $stage 'smoke-data'
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback,0)
    $listener.Start(); $smokePort = $listener.LocalEndpoint.Port; $listener.Stop()
    $smoke = Start-Process -FilePath (Join-Path $Target 'EVA.exe') -ArgumentList @('--server-only','--port',"$smokePort") -WindowStyle Hidden -PassThru
    $healthy = $false
    $watch = [Diagnostics.Stopwatch]::StartNew()
    while ($watch.Elapsed.TotalSeconds -lt $StartupTimeoutSeconds) {
        if ($smoke.HasExited) { break }
        try {
            $health = Invoke-RestMethod "http://127.0.0.1:$smokePort/api/health" -TimeoutSec 2
            if ($health.ok -and $health.version -eq $release.version -and $health.pairing_id -eq 'EVA-JH') { $healthy = $true; break }
        } catch {}
        Start-Sleep -Milliseconds 500
    }
    if (-not $healthy) { throw 'Applied EVA failed the startup/version check. Restoring the previous app.' }
    Stop-OwnedSmoke $smoke; $smoke = $null
    if ($RecordingLayout) {
        $configuration = @{}
        if ($settingsExisted) {
            $settingsBytes = [IO.File]::ReadAllBytes($settingsFile)
            Copy-Item -LiteralPath $settingsFile -Destination (Join-Path $backup 'settings-before-layout.json')
            $oldSettings = [IO.File]::ReadAllText($settingsFile) | ConvertFrom-Json
            foreach ($property in $oldSettings.PSObject.Properties) { $configuration[$property.Name] = $property.Value }
        }
        $configuration['recording_layout'] = $RecordingLayout
        if ($RecordingLayout -eq 'streamlabs_game') { $configuration['camera_backend'] = 'streamlabs' }
        $settingsTemp = Join-Path $Target ('settings-layout-' + [guid]::NewGuid().ToString('N') + '.tmp')
        [IO.File]::WriteAllText($settingsTemp, ($configuration | ConvertTo-Json -Depth 20))
        $settingsChanged = $true
        Move-Item -LiteralPath $settingsTemp -Destination $settingsFile -Force
    }
    $committed = $true
    [IO.File]::WriteAllText((Join-Path $backup 'transaction.json'), (@{version=$release.version;state='committed';backup=$backup} | ConvertTo-Json))
    if (-not $NoShortcut) {
      try {
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'EVA.lnk'))
        $shortcut.TargetPath = Join-Path $Target 'EVA.exe'; $shortcut.WorkingDirectory = $Target
        $shortcut.IconLocation = (Join-Path $Target 'eva.ico') + ',0'; $shortcut.Description = 'EVA'; $shortcut.Save()
      } catch { Write-Host 'EVA is installed; create a desktop shortcut to EVA.exe if needed.' -ForegroundColor Yellow }
    }
    if (-not $SkipFirewall) {
        try {
            if (-not (Get-NetFirewallRule -DisplayName 'EVA Streaming PC' -ErrorAction SilentlyContinue)) {
                New-NetFirewallRule -DisplayName 'EVA Streaming PC' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8765 -Profile Private -RemoteAddress LocalSubnet | Out-Null
            } else {
                Set-NetFirewallRule -DisplayName 'EVA Streaming PC' -Profile Private -RemoteAddress LocalSubnet | Out-Null
            }
        } catch { Write-Host 'LAN firewall setup requires Windows administrator rights. Allow TCP 8765 on Private/LocalSubnet once.' -ForegroundColor Yellow }
    }
    Write-Host "EVA $($release.version) verified and installed. Backup: $backup" -ForegroundColor Green
    [IO.File]::WriteAllText((Join-Path $Target 'updates\last-result.json'), (@{ok=$true;version=$release.version;backup=$backup} | ConvertTo-Json))
} catch {
    $failureMessage = $_.Exception.Message
    if ($settingsChanged -and -not $committed) {
        if ($settingsExisted) { [IO.File]::WriteAllBytes($settingsFile, $settingsBytes) }
        elseif (Test-Path -LiteralPath $settingsFile) { Remove-Item -LiteralPath $settingsFile -Force }
    }
    Stop-OwnedSmoke $smoke
    if (-not $committed) {
        foreach ($name in $changed) {
            $old = Join-Path $backup $name
            if (Test-Path -LiteralPath $old) { Copy-Item -LiteralPath $old -Destination (Join-Path $Target $name) -Force }
            elseif (Test-Path -LiteralPath (Join-Path $Target $name)) { Remove-Item -LiteralPath (Join-Path $Target $name) -Force }
        }
        if (Test-Path -LiteralPath $backup) { [IO.File]::WriteAllText((Join-Path $backup 'transaction.json'), (@{state='rolled_back';error=$_.Exception.Message} | ConvertTo-Json)) }
    }
    if (Test-Path -LiteralPath $Target) {
        New-Item -ItemType Directory -Force -Path (Join-Path $Target 'updates') | Out-Null
        [IO.File]::WriteAllText((Join-Path $Target 'updates\last-result.json'), (@{ok=$false;error=$failureMessage;backup=$backup} | ConvertTo-Json))
    }
    if ($wasInstalled -and ($changed.Count -gt 0 -or $WaitPid -gt 0) -and -not $NoLaunch) {
        $env:EVA_UPDATE_VALIDATING = $envBefore
        $env:EVA_DATA_DIR = $dataBefore
        Start-Process -FilePath (Join-Path $Target 'EVA.exe') -WindowStyle Hidden
    }
    throw
} finally {
    $env:EVA_UPDATE_VALIDATING = $envBefore
    $env:EVA_DATA_DIR = $dataBefore
    # Delete only the task-owned staging path after checking its resolved location.
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if ([IO.Path]::GetFullPath($stage).StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase) -and (Split-Path $stage -Leaf).StartsWith('eva-release-')) {
        Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
    }
}
if (-not $NoLaunch) { Start-Process -FilePath (Join-Path $Target 'EVA.exe') -WindowStyle Hidden }
