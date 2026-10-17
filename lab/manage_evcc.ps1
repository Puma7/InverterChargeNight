param([ValidateSet('start', 'stop', 'status')][string]$Action = 'start')
$ErrorActionPreference = 'Stop'
$evccPath = Join-Path $PSScriptRoot 'runtime\evcc'
$recordPath = Join-Path $evccPath 'process.json'
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
        if ($managedProcess) { Write-Output 'evcc already running: http://127.0.0.1:7070'; exit 0 }
        $binary = Join-Path $evccPath 'evcc.exe'
        if (-not (Test-Path -LiteralPath $binary)) { throw 'Run python lab/install_evcc.py and python lab/evcc_config.py first.' }
        foreach ($box in @(1, 2)) {
            & $binary settings set --force --config (Join-Path $evccPath 'evcc.yaml') --database (Join-Path $evccPath 'evcc.db') ('lp' + $box + '.maxCurrent') '32'
            if ($LASTEXITCODE -ne 0) { throw 'evcc fixture settings failed.' }
        }
        $processArgs = @('--config', ('"' + (Join-Path $evccPath 'evcc.yaml') + '"'),
            '--database', ('"' + (Join-Path $evccPath 'evcc.db') + '"'))
        # evcc binds all interfaces. Keep its standard administrator authentication.
        $startedProcess = Start-Process -FilePath $binary -ArgumentList $processArgs -WorkingDirectory $evccPath -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $evccPath 'stdout.log') `
            -RedirectStandardError (Join-Path $evccPath 'stderr.log')
        @{pid=$startedProcess.Id; started=$startedProcess.StartTime.ToUniversalTime().ToString('o')} |
            ConvertTo-Json | Set-Content -LiteralPath $recordPath -Encoding utf8
        Write-Output 'Starting evcc: http://127.0.0.1:7070'
    }
    'stop' {
        if ($managedProcess) { Stop-Process -Id $managedProcess.Id; Write-Output 'evcc stopped.' }
        else { Write-Output 'No managed evcc process is running.' }
    }
    'status' {
        if ($managedProcess) { Write-Output ('evcc running, PID ' + $managedProcess.Id + ': http://127.0.0.1:7070') }
        else { Write-Output 'No managed evcc process is running.' }
    }
}
