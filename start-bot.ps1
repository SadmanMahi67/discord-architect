$projectRoot = "F:\discord-architect\discord-architect"
$pythonExe = "F:\discord-architect\.venv\Scripts\python.exe"
$logDir = Join-Path $projectRoot "logs"
$stdoutLog = Join-Path $logDir "bot.out.log"
$stderrLog = Join-Path $logDir "bot.err.log"
$env:PYTHONUTF8 = "1"

New-Item -ItemType Directory -Path $logDir -Force | Out-Null
Set-Location $projectRoot

# Prevent duplicate background instances when startup triggers repeatedly.
$existing = Get-CimInstance Win32_Process | Where-Object {
    $_.Name -ieq "python.exe" -and $_.CommandLine -like "*bot.py*" -and $_.CommandLine -like "*discord-architect\\discord-architect*"
} | Select-Object -First 1
if ($existing) {
    exit 0
}

Start-Process -FilePath $pythonExe -ArgumentList "-X utf8 -u bot.py" -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog
