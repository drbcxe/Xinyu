$ErrorActionPreference = 'Stop'
$projectDir = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$recordFile = Join-Path $projectDir '.runtime\processes.json'
if (-not (Test-Path -LiteralPath $recordFile)) { Write-Output 'No recorded Xinyu processes.'; return }
$records = @(Get-Content -LiteralPath $recordFile -Raw | ConvertFrom-Json)
foreach ($record in $records) {
    $process = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
    if ($process -and [Math]::Abs($process.StartTime.ToUniversalTime().Ticks - [long]$record.started) -lt 20000000) {
        $children = @(Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $record.pid })
        foreach ($child in $children) {
            $childProcess = Get-CimInstance Win32_Process -Filter "ProcessId=$($child.ProcessId)"
            if ($childProcess -and $childProcess.ParentProcessId -eq $record.pid) {
                Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $child.ProcessId } | ForEach-Object { Stop-Process -Id $_.ProcessId -ErrorAction SilentlyContinue }
                Stop-Process -Id $child.ProcessId -ErrorAction SilentlyContinue
            }
        }
        Stop-Process -Id $record.pid -ErrorAction SilentlyContinue
        Write-Output "Stopped Xinyu $($record.role)."
    }
}
Set-Content -LiteralPath $recordFile -Value '[]' -Encoding utf8
