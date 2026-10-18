param([ValidateSet('start', 'stop', 'status')][string]$Action = 'start')
$ErrorActionPreference = 'Stop'
$evccPath = Join-Path $PSScriptRoot 'runtime\evcc'
$recordPath = Join-Path $evccPath 'process.json'
$evccUrl = 'http://127.0.0.1:7070'
$endpointPath = Join-Path $evccPath 'endpoint.json'
if (Test-Path -LiteralPath $endpointPath) {
    $evccUrl = (Get-Content -LiteralPath $endpointPath -Raw | ConvertFrom-Json).url
}
$managedProcess = $null
if (Test-Path -LiteralPath $recordPath) {
    $record = Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json
    $candidateProcess = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
    if ($candidateProcess -and $candidateProcess.StartTime.ToUniversalTime().Ticks -eq ([datetime]$record.started).ToUniversalTime().Ticks) {
        $managedProcess = $candidateProcess
    }
}
switch ($Action) {
    'start' {
        if ($managedProcess) { Write-Output ('evcc already running: ' + $evccUrl); exit 0 }
        $binary = Join-Path $evccPath 'evcc.exe'
        if (-not (Test-Path -LiteralPath $binary)) { throw 'Run python lab/install_evcc.py and python lab/evcc_config.py first.' }
        $repoPath = Split-Path $PSScriptRoot -Parent
        $seedPython = Join-Path $repoPath '.venv-lab\Scripts\python.exe'
        if (-not (Test-Path -LiteralPath $seedPython)) {
            $seedPython = Join-Path (Split-Path $repoPath -Parent) 'ha-venv\Scripts\python.exe'
        }
        & $seedPython -X utf8 (Join-Path $PSScriptRoot 'evcc_config.py') --seed-only
        if ($LASTEXITCODE -ne 0) { throw 'evcc fixture settings failed.' }
        $processArgs = @('--config', ('"' + (Join-Path $evccPath 'evcc.yaml') + '"'),
            '--database', ('"' + (Join-Path $evccPath 'evcc.db') + '"'))
        # evcc binds all interfaces. Keep its standard administrator authentication.
        $startedProcess = Start-Process -FilePath $binary -ArgumentList $processArgs -WorkingDirectory $evccPath -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $evccPath 'stdout.log') `
            -RedirectStandardError (Join-Path $evccPath 'stderr.log')
        @{pid=$startedProcess.Id; started=$startedProcess.StartTime.ToUniversalTime().ToString('o')} |
            ConvertTo-Json | Set-Content -LiteralPath $recordPath -Encoding utf8
        Write-Output ('Starting evcc: ' + $evccUrl)
    }
    'stop' {
        if ($managedProcess) { Stop-Process -Id $managedProcess.Id; Write-Output 'evcc stopped.' }
        else { Write-Output 'No managed evcc process is running.' }
    }
    'status' {
        if ($managedProcess) { Write-Output ('evcc running, PID ' + $managedProcess.Id + ': ' + $evccUrl) }
        else { Write-Output 'No managed evcc process is running.' }
    }
}
