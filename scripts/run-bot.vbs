' run-bot.vbs -- launch the bot supervisor with NO visible window.
'
' Why this exists:
'   Task Scheduler "run only when the user is logged on" starts its action on the
'   interactive desktop. powershell.exe is a console-subsystem program, so it gets
'   a console window; -WindowStyle Hidden does not reliably suppress it under the
'   scheduler and the window stays visible the whole time the bot runs.
'
'   wscript.exe is a GUI-subsystem host (no console of its own). WshShell.Run with
'   intWindowStyle = 0 launches PowerShell with a HIDDEN window, so nothing shows.
'   bWaitOnReturn = False => wscript exits immediately; PowerShell keeps supervising
'   the windowless pythonw bot in the background.
'
' The scheduled task action is:  wscript.exe "<this file>"

Option Explicit
Dim sh, scriptDir, ps1, cmd
Set sh = CreateObject("WScript.Shell")
scriptDir = Left(WScript.ScriptFullName, InStrRev(WScript.ScriptFullName, "\"))
ps1 = scriptDir & "run-bot.ps1"
cmd = "powershell.exe -ExecutionPolicy Bypass -NoProfile -WindowStyle Hidden -File """ & ps1 & """"
sh.Run cmd, 0, False
