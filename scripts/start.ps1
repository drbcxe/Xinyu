param([switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
$projectDir = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$pythonExe = Join-Path $projectDir '.venv\Scripts\python.exe'
$nodeExe = 'D:\DevTools\nodejs\node.exe'
$pnpmScript = 'D:\DevTools\npm-global\node_modules\pnpm\bin\pnpm.mjs'
foreach ($required in @($pythonExe, $nodeExe, $pnpmScript)) {
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
    $records = @()
    $specs = @(
        @{Role='api'; Exe=$pythonExe; Arguments=@('-u','-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8000'); Dir=$projectDir},
        @{Role='worker'; Exe=$pythonExe; Arguments=@('-u','-m','scripts.worker'); Dir=$projectDir},
        @{Role='web'; Exe=$nodeExe; Arguments=@($pnpmScript,'dev','--host','localhost'); Dir=(Join-Path $projectDir 'admin-ui')}
    )
    foreach ($spec in $specs) {
        $arguments = $spec.Arguments | ForEach-Object { '"' + $_.Replace('"','\"') + '"' }
        $process = Start-Process -FilePath $spec.Exe -ArgumentList $arguments -WorkingDirectory $spec.Dir -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir "$($spec.Role).out.log") -RedirectStandardError (Join-Path $logDir "$($spec.Role).err.log") -PassThru
        $records += @{role=$spec.Role; pid=$process.Id; started=$process.StartTime.ToUniversalTime().Ticks}
        $records | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runtimeDir 'processes.json') -Encoding utf8
    }
    Write-Output 'Xinyu services started. Open http://localhost:5173/assistant . Logs: .runtime/logs/'
    if (-not $NoBrowser) { Start-Process 'http://localhost:5173/assistant' -WindowStyle Hidden }
} finally { Pop-Location }
