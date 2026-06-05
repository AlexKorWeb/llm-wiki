# install-autostart.ps1 -- register the Telegram bot to auto-start at logon.
# ASCII-ONLY (Windows PowerShell 5.1 mis-decodes non-ASCII in BOM-less .ps1).
#
# What it sets up:
#   A Scheduled Task that, at every user logon, runs:
#       wscript.exe "<scripts>\run-bot.vbs"
#   run-bot.vbs launches run-bot.ps1 with a HIDDEN window (no console ever shows);
#   run-bot.ps1 then supervises the windowless pythonw bot. No window appears at
#   any layer, and the bot survives crashes (auto-restart with backoff).
#
# Run once:
#   powershell -ExecutionPolicy Bypass -File scripts/install-autostart.ps1
#
# Re-running is safe (idempotent: it replaces any existing task).
# To remove autostart:  Unregister-ScheduledTask -TaskName "LLM Wiki Telegram Bot" -Confirm:$false
# To stop the bot now:  powershell -File scripts/stop-bot.ps1   (or /stop in Telegram)

[CmdletBinding()]
param(
    [string]$TaskName = 'LLM Wiki Telegram Bot',
    [switch]$NoStart   # register only; do not start the bot immediately
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Vbs       = Join-Path $ScriptDir 'run-bot.vbs'
$Ps1       = Join-Path $ScriptDir 'run-bot.ps1'

foreach ($f in @($Vbs, $Ps1)) {
    if (-not (Test-Path $f)) { throw "Missing required file: $f" }
}

$User = whoami

# Remove any existing task with this name (idempotent re-install).
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed existing task '$TaskName'." -ForegroundColor DarkYellow
}

# wscript.exe is a GUI-subsystem host: launching the bot through it (via the .vbs)
# means no console window is ever created on the interactive desktop.
$action = New-ScheduledTaskAction -Execute 'wscript.exe' -Argument "`"$Vbs`""

# AtLogOn for THIS user, in the user session (the bot needs the user's git
# credentials and claude auth, which live in the user profile).
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
$trigger.Delay = 'PT30S'   # let network/desktop settle before the first poll

$principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Limited

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings `
    -Description 'Auto-start the LLM-wiki Telegram ingest bot at logon, fully hidden (wscript -> hidden PowerShell -> windowless pythonw). Single-instance, auto-restart on crash.' | Out-Null

Write-Host "Registered task '$TaskName' for user $User (runs hidden at logon)." -ForegroundColor Green

if ($NoStart) {
    Write-Host "Not started (use: Start-ScheduledTask -TaskName `"$TaskName`")." -ForegroundColor Yellow
} else {
    Start-ScheduledTask -TaskName $TaskName
    Start-Sleep -Seconds 6
    $bot = Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -like '*tg_bot.py*' }
    if ($bot) {
        Write-Host "Bot is running (pythonw PID $(@($bot)[0].ProcessId)), no window." -ForegroundColor Green
    } else {
        Write-Host "Started the task, but no bot process yet. Check scripts\logs\launcher_*.log." -ForegroundColor Yellow
    }
}
