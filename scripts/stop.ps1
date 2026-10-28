$ErrorActionPreference = 'Stop'
$projectDir = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$recordFile = Join-Path $projectDir '.runtime\processes.json'
if (-not (Test-Path -LiteralPath $recordFile)) { Write-Output 'No recorded Xinyu processes.'; return }
$records = @(Get-Content -LiteralPath $recordFile -Raw | ConvertFrom-Json)
foreach ($record in $records) {
    $process = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
    if ($process -and $process.StartTime.ToUniversalTime().Ticks -eq [long]$record.started) {
        $snapshot = @(Get-CimInstance Win32_Process)
        $descendants = [System.Collections.Generic.List[object]]::new()
        $queue = [System.Collections.Generic.Queue[object]]::new()
        $visited = [System.Collections.Generic.HashSet[uint32]]::new()
        $queue.Enqueue(@{Id=[uint32]$record.pid; Created=$process.StartTime.ToUniversalTime()})
        $visited.Add([uint32]$record.pid) | Out-Null
        while ($queue.Count -gt 0) {
            $parent = $queue.Dequeue()
            foreach ($child in @($snapshot | Where-Object { $_.ParentProcessId -eq $parent.Id -and $_.CreationDate.ToUniversalTime() -ge $parent.Created })) {
                if (-not $visited.Add([uint32]$child.ProcessId)) { continue }
                $descendants.Add($child)
                $queue.Enqueue(@{Id=[uint32]$child.ProcessId; Created=$child.CreationDate.ToUniversalTime()})
            }
        }
        for ($index = $descendants.Count - 1; $index -ge 0; $index--) {
            $child = $descendants[$index]
            $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($child.ProcessId)"
            if ($current -and $current.ParentProcessId -eq $child.ParentProcessId -and $current.CreationDate -eq $child.CreationDate) {
                Stop-Process -Id $child.ProcessId -ErrorAction SilentlyContinue
            }
        }
        $currentRoot = Get-Process -Id $record.pid -ErrorAction SilentlyContinue
        if ($currentRoot -and $currentRoot.StartTime.ToUniversalTime().Ticks -eq [long]$record.started) {
            Stop-Process -Id $record.pid -ErrorAction SilentlyContinue
        }
        Write-Output "Stopped Xinyu $($record.role)."
    }
}
Set-Content -LiteralPath $recordFile -Value '[]' -Encoding utf8
