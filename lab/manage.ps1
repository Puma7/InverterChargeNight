param(
    [ValidateSet('start', 'stop', 'status', 'test')][string]$Action = 'start',
    [ValidateSet('native', 'container')][string]$Mode = 'native',
    [string]$Python = '',
    [ValidateRange(1, 65535)][int]$Port = 8123,
    [string]$InverterHost = '127.0.0.1',
    [ValidateRange(1, 65535)][int]$DeviceApiPort = 18080
)
$ErrorActionPreference = 'Stop'
$labPath = $PSScriptRoot
$repoPath = Split-Path $labPath -Parent
$runtimePath = Join-Path $labPath 'runtime'
$recordPath = Join-Path $runtimePath 'native-process.json'
if ($Mode -eq 'container') {
    switch ($Action) {
        'start' { & docker compose -f (Join-Path $labPath 'compose.yaml') up -d }
        'stop' { & docker compose -f (Join-Path $labPath 'compose.yaml') stop }
        'status' { & docker compose -f (Join-Path $labPath 'compose.yaml') ps }
        'test' { throw 'Use native tests or the Linux acceptance-test workflow.' }
    }
    exit $LASTEXITCODE
}
if (-not $Python) {
    foreach ($candidate in @((Join-Path $repoPath '.venv-lab\Scripts\python.exe'),
                             (Join-Path (Split-Path $repoPath -Parent) 'ha-venv\Scripts\python.exe'))) {
        if (Test-Path -LiteralPath $candidate) { $Python = $candidate; break }
    }
}
if (-not $Python) { throw 'Python environment missing. Follow lab/README.md (Windows setup).' }
$labUrl = 'http://127.0.0.1:' + $Port
$env:ICN_LAB_HA_PORT = [string]$Port
$env:ICN_LAB_INVERTER_HOST = $InverterHost
$env:ICN_LAB_DEVICE_PORT = [string]$DeviceApiPort
$managedProcess = $null
if (Test-Path -LiteralPath $recordPath) {
    $record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
    $candidateProcess = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
    if ($candidateProcess -and $candidateProcess.StartTime.ToUniversalTime().Ticks -eq ([datetime]$record.started).ToUniversalTime().Ticks) {
        $managedProcess = $candidateProcess
        if ($record.url) { $labUrl = $record.url }
    }
}
switch ($Action) {
    'start' {
        if ($managedProcess) { Write-Output ('Already running: ' + $labUrl); exit 0 }
        & $Python -X utf8 (Join-Path $labPath 'prepare.py')
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        $processArgs = @('-X', 'utf8', ('"' + (Join-Path $labPath 'run_native.py') + '"'))
        $startedProcess = Start-Process -FilePath $Python -ArgumentList $processArgs -WorkingDirectory $repoPath -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $runtimePath 'native.stdout.log') `
            -RedirectStandardError (Join-Path $runtimePath 'native.stderr.log')
        @{pid=$startedProcess.Id; started=$startedProcess.StartTime.ToUniversalTime().ToString('o'); url=$labUrl;
          inverter_host=$InverterHost; device_api_port=$DeviceApiPort} |
            ConvertTo-Json | Set-Content -LiteralPath $recordPath -Encoding utf8
        Write-Output ('Starting Home Assistant: ' + $labUrl)
    }
    'stop' {
        if ($managedProcess) {
            # run_native watches this file and shuts HA down cleanly.
            New-Item -ItemType File -Path (Join-Path $runtimePath 'native.stop') -Force | Out-Null
            Write-Output 'Shutdown requested.'
        } else { Write-Output 'No managed lab process is running.' }
    }
    'status' {
        if ($managedProcess) { Write-Output ('Running, PID ' + $managedProcess.Id + ': ' + $labUrl) }
        else { Write-Output 'No managed lab process is running.' }
    }
    'test' {
        & $Python -X utf8 (Join-Path $labPath 'check_real_ha.py')
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & $Python -X utf8 (Join-Path $labPath 'check_kostal.py')
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & $Python -X utf8 (Join-Path $labPath 'check_grid.py')
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & $Python -X utf8 (Join-Path $labPath 'check_recovery.py')
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        & $Python -X utf8 (Join-Path $labPath 'check_edge_cases.py')
        exit $LASTEXITCODE
    }
}
