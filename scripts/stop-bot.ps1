# stop-bot.ps1 -- reliably stop the bot and its supervisor.
# ASCII-ONLY (Windows PowerShell 5.1 mis-decodes non-ASCII in BOM-less .ps1).
#
# Why a helper: the bot is launched via wscript (fire-and-forget), so the running
# pythonw bot and its run-bot.ps1 supervisor are NOT children of the scheduled
# task. Stop-ScheduledTask therefore does NOT stop them. This script finds and
# stops every layer by command line, in the right order.
#
# Order matters: kill the SUPERVISOR (run-bot.ps1) first so it cannot restart the
# bot during the backoff window, then kill the bot itself.
#
# Usage:  powershell -ExecutionPolicy Bypass -File scripts/stop-bot.ps1
# (Graceful alternative: send /stop to the bot in Telegram.)

[CmdletBinding()]
param(
    [string]$TaskName = 'LLM Wiki Telegram Bot'
)

$ErrorActionPreference = 'Stop'
$me = $PID   # never touch the process running THIS script

function Stop-Matching([string]$procName, [string]$needle, [string]$label) {
    $procs = Get-CimInstance Win32_Process -Filter "Name='$procName'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -like "*$needle*" -and $_.ProcessId -ne $me }
    $n = 0
    foreach ($p in $procs) {
        try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop; $n++ }
        catch { Write-Host "  could not stop PID $($p.ProcessId): $($_.Exception.Message)" -ForegroundColor DarkYellow }
    }
    Write-Host ("Stopped {0} {1} process(es)." -f $n, $label)
    return $n
}

# 1) Tell the scheduler too (harmless if it tracks nothing).
try { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue } catch {}

# 2) Supervisors first (vbs host + powershell launcher), then the bot.
[void](Stop-Matching 'wscript.exe'    'run-bot.vbs' 'vbs-host')
[void](Stop-Matching 'powershell.exe' 'run-bot.ps1' 'supervisor')
Start-Sleep -Seconds 1
[void](Stop-Matching 'pythonw.exe'    'tg_bot.py'   'bot')
[void](Stop-Matching 'python.exe'     'tg_bot.py'   'bot(console)')

Start-Sleep -Seconds 1
$left = @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*tg_bot.py*' }).Count
if ($left -eq 0) {
    Write-Host "Bot stopped. (It will start again at next logon, or run: Start-ScheduledTask -TaskName `"$TaskName`")" -ForegroundColor Green
} else {
    Write-Host "WARNING: $left bot process(es) still alive. Re-run this script." -ForegroundColor Red
}
