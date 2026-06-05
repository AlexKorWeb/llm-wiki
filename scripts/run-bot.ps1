# run-bot.ps1 -- robust launcher for tg_bot.py (Telegram -> LLM wiki INGEST)
# ASCII-ONLY by design: Windows PowerShell 5.1 mis-decodes non-ASCII in BOM-less
# .ps1 files and fails to parse. Keep every character in this file 7-bit ASCII.
#
# Responsibilities:
#   1. Run the bot with pythonw.exe (WINDOWLESS): no console window appears, so it
#      cannot be accidentally closed. A console window would otherwise let a stray
#      close/Ctrl+C kill the bot (STATUS_CONTROL_C_EXIT / 0xC000013A).
#   2. Put claude.exe, git.exe and the correct python on PATH for the child process
#      (tg_bot.py resolves `claude` via shutil.which and calls `git` directly).
#   3. Single-instance guard: never start a second poller (Telegram returns 409
#      Conflict if two processes long-poll the same bot token).
#   4. Supervise: restart on crash (exit code != 0) with escalating backoff, but
#      RESPECT a clean /stop (exit code 0) and stop supervising.
#   5. Circuit breaker: give up after repeated rapid failures (e.g. missing token)
#      so a permanent config error does not become a hot restart loop.
#   6. Log launcher-level events to scripts\logs\launcher_<date>.log.

$ErrorActionPreference = 'Stop'

# --- Paths (this script lives in <wiki>\scripts) ---------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BotScript = Join-Path $ScriptDir 'tg_bot.py'
$LogsDir   = Join-Path $ScriptDir 'logs'
if (-not (Test-Path $LogsDir)) { New-Item -ItemType Directory -Path $LogsDir | Out-Null }

function Write-Log([string]$msg) {
    $stamp   = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    $logFile = Join-Path $LogsDir ('launcher_' + (Get-Date -Format 'yyyy-MM-dd') + '.log')
    $line    = "$stamp [launcher] $msg"
    Add-Content -Path $logFile -Value $line -Encoding UTF8
    Write-Host $line
}

# --- Run a python interpreter windowless and return its exit code -----------
# argLine is a single, already-quoted command line. Passing it as one string
# (not an array) avoids PowerShell 5.1 joining array elements without quoting,
# which would split a -c "import a, b" snippet into broken argv tokens.
function Invoke-Windowless([string]$exe, [string]$argLine) {
    $p = Start-Process -FilePath $exe -ArgumentList $argLine -WindowStyle Hidden -PassThru -Wait
    return $p.ExitCode
}

# --- Resolve pythonw.exe: prefer the interpreter that actually has telegram --
# Returns the WINDOWLESS interpreter (pythonw.exe) so no console is ever shown.
function Resolve-PythonW {
    $candidates = @(
        (Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3.14-64\pythonw.exe'),
        (Join-Path $env:LOCALAPPDATA 'Python\pythoncore-3.12-64\pythonw.exe')
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) {
            if ((Invoke-Windowless $c '-c "import telegram, dotenv"') -eq 0) { return $c }
        }
    }
    # Fallback: py launcher resolves python.exe; swap to the pythonw.exe sibling.
    foreach ($ver in @('-3.14', '-3.12', '-3')) {
        $probe = & py $ver -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $probe) {
            $pw = $probe.Trim() -replace 'python\.exe$', 'pythonw.exe'
            if ((Test-Path $pw) -and (Invoke-Windowless $pw '-c "import telegram, dotenv"') -eq 0) {
                return $pw
            }
        }
    }
    return $null
}

# --- Augment PATH so the child sees claude + git ----------------------------
$claudeBin = Join-Path $env:USERPROFILE '.local\bin'         # claude.exe
$gitCmd    = 'C:\Program Files\Git\cmd'                       # git.exe
$prepend   = @()
if (Test-Path (Join-Path $claudeBin 'claude.exe')) { $prepend += $claudeBin }
elseif (Test-Path (Join-Path $claudeBin 'claude'))  { $prepend += $claudeBin }
if (Test-Path (Join-Path $gitCmd 'git.exe'))        { $prepend += $gitCmd }
if ($prepend.Count -gt 0) {
    $env:PATH = ($prepend -join ';') + ';' + $env:PATH
}

# --- Single-instance guard --------------------------------------------------
$myPid = $PID
$existing = Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -like '*tg_bot.py*' -and $_.ProcessId -ne $myPid }
if ($existing) {
    $pids = ($existing | ForEach-Object { $_.ProcessId }) -join ', '
    Write-Log "tg_bot.py already running (PID $pids). Exiting launcher; not starting a second poller."
    exit 0
}

$pythonw = Resolve-PythonW
if (-not $pythonw) {
    Write-Log "FATAL: no pythonw.exe with telegram+dotenv found. Run: pip install -r requirements.txt"
    exit 1
}
Write-Log "Launcher start. pythonw=$pythonw claudeOnPath=$([bool](Get-Command claude -ErrorAction SilentlyContinue)) gitOnPath=$([bool](Get-Command git -ErrorAction SilentlyContinue))"

# --- Supervision loop -------------------------------------------------------
$rapidFailures   = 0          # consecutive failures that died quickly
$maxRapid        = 8          # give up after this many rapid failures in a row
$healthySeconds  = 60         # ran at least this long => treat as a real session
$backoffSchedule = @(10, 20, 40, 60, 60, 120, 120, 300)  # seconds, indexed by rapidFailures

Set-Location $ScriptDir
while ($true) {
    $start = Get-Date
    Write-Log "Starting tg_bot.py (windowless) ..."
    try {
        # -WindowStyle Hidden + pythonw.exe => no console window at all, so the
        # bot cannot be accidentally closed. -Wait blocks until the bot exits.
        $proc = Start-Process -FilePath $pythonw -ArgumentList @("`"$BotScript`"") `
                              -WorkingDirectory $ScriptDir -WindowStyle Hidden -PassThru -Wait
        $code = $proc.ExitCode
    } catch {
        $code = 1
        Write-Log "Launcher caught exception starting pythonw: $($_.Exception.Message)"
    }
    $ranSec = [int]((Get-Date) - $start).TotalSeconds

    if ($code -eq 0) {
        Write-Log "tg_bot.py exited cleanly (code 0, ran ${ranSec}s) -- deliberate /stop. Supervisor exiting."
        break
    }

    Write-Log "tg_bot.py exited with code $code after ${ranSec}s."
    if ($ranSec -ge $healthySeconds) {
        # It was up and healthy; a later crash is transient. Reset breaker.
        $rapidFailures = 0
    } else {
        $rapidFailures++
    }

    if ($rapidFailures -ge $maxRapid) {
        Write-Log "Circuit breaker: $rapidFailures rapid failures in a row. Likely a permanent error (bad token, missing deps, no network). Supervisor giving up. Check the latest tg_bot_*.log."
        break
    }

    $idx   = [Math]::Min($rapidFailures, $backoffSchedule.Count - 1)
    $delay = $backoffSchedule[$idx]
    Write-Log "Restarting in ${delay}s (rapidFailures=$rapidFailures/$maxRapid)."
    Start-Sleep -Seconds $delay
}
