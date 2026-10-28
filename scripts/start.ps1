param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$projectDir = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$pythonExe = Join-Path $projectDir '.venv\Scripts\python.exe'
$nodeExe = 'D:\DevTools\nodejs\node.exe'
$viteScript = Join-Path $projectDir 'admin-ui\node_modules\vite\bin\vite.js'
foreach ($required in @($pythonExe, $nodeExe, $viteScript)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Required runtime missing: $required" }
}
$runtimeDir = Join-Path $projectDir '.runtime'
$logDir = Join-Path $runtimeDir 'logs'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
if (Test-Path -LiteralPath (Join-Path $runtimeDir 'processes.json')) {
    & (Join-Path $PSScriptRoot 'stop.ps1')
}
$env:PYTHONUTF8 = '1'
$tempDir = Join-Path $runtimeDir 'tmp'
New-Item -ItemType Directory -Path $tempDir -Force | Out-Null
$env:TEMP = $tempDir
$env:TMP = $tempDir
$env:Path = "D:\DevTools\nodejs;D:\DevTools\npm-global;D:\DevTools\pnpm\bin;$env:Path"
Push-Location $projectDir
try {
    & 'D:\docker_desktop\resources\bin\docker.exe' compose up -d --wait postgres redis qdrant
    if ($LASTEXITCODE -ne 0) { throw 'Docker dependencies did not become healthy' }
    & $pythonExe -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw 'Database migration failed' }
    & $pythonExe -m scripts.prepare_ai
    if ($LASTEXITCODE -ne 0) { throw 'AI infrastructure did not become ready' }
    $occupiedPorts = @(Get-NetTCPConnection -LocalPort 8000,5173 -State Listen -ErrorAction SilentlyContinue)
    if ($occupiedPorts.Count -gt 0) { throw 'Port 8000 or 5173 is already in use; existing untracked processes were preserved.' }
    $records = @()
    $specs = @(
        @{Role='api'; Exe=$pythonExe; Arguments=@('-u','-m','scripts.supervise','api'); Dir=$projectDir},
        @{Role='worker'; Exe=$pythonExe; Arguments=@('-u','-m','scripts.supervise','worker'); Dir=$projectDir},
        @{Role='web'; Exe=$pythonExe; Arguments=@('-u','-m','scripts.supervise','web','--node',$nodeExe); Dir=$projectDir}
    )
    foreach ($spec in $specs) {
        $arguments = $spec.Arguments | ForEach-Object { '"' + $_.Replace('"','\"') + '"' }
        $process = Start-Process -FilePath $spec.Exe -ArgumentList $arguments -WorkingDirectory $spec.Dir -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir "$($spec.Role).out.log") -RedirectStandardError (Join-Path $logDir "$($spec.Role).err.log") -PassThru
        $records += @{role=$spec.Role; pid=$process.Id; started=$process.StartTime.ToUniversalTime().Ticks}
        $records | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runtimeDir 'processes.json') -Encoding utf8
    }
    $startupReady = $false
    $startupDeadline = [DateTime]::UtcNow.AddSeconds(40)
    while ([DateTime]::UtcNow -lt $startupDeadline) {
        foreach ($record in $records) {
            if (-not (Get-Process -Id $record.pid -ErrorAction SilentlyContinue)) { throw "Xinyu $($record.role) exited during startup; inspect the bounded supervisor log." }
        }
        try {
            $apiReady = (Invoke-WebRequest -Uri 'http://127.0.0.1:8000/api/health' -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200
            $webReady = (Invoke-WebRequest -Uri 'http://localhost:5173/' -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200
            if ($apiReady -and $webReady) { $startupReady = $true; break }
        } catch { }
        Start-Sleep -Milliseconds 500
    }
    if (-not $startupReady) { throw 'Xinyu API/web did not become ready within the startup window.' }
    Write-Output 'Xinyu services started and HTTP endpoints checked. Open http://localhost:5173/assistant . Logs: .runtime/logs/'
    if (-not $NoBrowser) { Start-Process 'http://localhost:5173/assistant' -WindowStyle Hidden }
} catch {
    & (Join-Path $PSScriptRoot 'stop.ps1')
    throw
} finally { Pop-Location }
