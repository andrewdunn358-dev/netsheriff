<#
    Net Sheriff - screen-time agent installer

    Run this ONCE per machine via Tactical (as SYSTEM is fine - this installer
    needs admin; only the sampling task it creates runs as the user).

    Why a scheduled task and not a Tactical task: the sampler reads the
    foreground window, which only exists in the interactive user's session.
    Run as SYSTEM it sees session 0 and finds nothing (proven in testing).
    So this registers a scheduled task that runs *as the logged-on user*,
    at logon, repeating every minute.

    The task is harmless while the server-side switch is off: the agent still
    posts, the server refuses the data. So you can deploy to every machine
    now and turn monitoring on per client later, from the server.

    Set the three values, or pass them as Tactical script arguments.
#>
param(
    [string]$PortalUrl  = "https://portal.netsheriff.co.uk",
    [string]$Tenant     = "NCS",
    [string]$AgentToken = "CHANGE-ME"
)

$ErrorActionPreference = 'Stop'
$dir      = "C:\Program Files\NetSheriff"
$script   = Join-Path $dir "activity-agent.ps1"
$taskName = "NetSheriff Activity"

New-Item -ItemType Directory -Path $dir -Force | Out-Null

# The sampler, written locally with this site's settings baked in. Kept in
# Program Files (admin-writable only) so a standard user can't edit what it
# reports. Mirrors agent/report-activity.ps1 - keep them in step.
@"
`$PortalUrl='$PortalUrl'; `$Tenant='$Tenant'; `$AgentToken='$AgentToken'; `$SampleSecs=60
`$AppNames=@{'chrome'='Chrome';'msedge'='Edge';'firefox'='Firefox';'outlook'='Outlook';'excel'='Excel';'winword'='Word';'powerpnt'='PowerPoint';'teams'='Teams';'ms-teams'='Teams';'explorer'='File Explorer';'sage'='Sage'}
`$Browsers=@('chrome','msedge','firefox')
`$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol='Tls12'
Add-Type @'
using System; using System.Runtime.InteropServices;
public class Fg {
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern int GetWindowThreadProcessId(IntPtr h, out uint pid);
}
'@
`$h=[Fg]::GetForegroundWindow(); if(`$h -eq [IntPtr]::Zero){exit 0}
`$pid_=0; [void][Fg]::GetWindowThreadProcessId(`$h,[ref]`$pid_)
`$proc=Get-Process -Id `$pid_ -ErrorAction SilentlyContinue; if(-not `$proc){exit 0}
`$pn=`$proc.ProcessName.ToLower()
if(`$pn -eq 'wscript' -or `$pn -eq 'powershell' -or `$pn -eq 'conhost'){exit 0}
`$app=if(`$AppNames.ContainsKey(`$pn)){`$AppNames[`$pn]}else{`$proc.ProcessName}
`$isBrowser=if(`$Browsers -contains `$pn){1}else{0}
`$u="`$env:USERNAME"; if(-not `$u){exit 0}
`$body=@{tenant=`$Tenant; samples=@(@{username=`$u; hostname=`$env:COMPUTERNAME; app=`$app; is_browser=`$isBrowser; sampled_at=(Get-Date).ToString('yyyy-MM-dd HH:mm:ss'); seconds=`$SampleSecs})} | ConvertTo-Json -Depth 4 -Compress
try{ Invoke-RestMethod -Uri "`$PortalUrl/api/activity" -Method Post -ContentType 'application/json' -Body `$body -Headers @{'X-Agent-Token'=`$AgentToken} -TimeoutSec 20 | Out-Null }catch{}
"@ | Set-Content -Path $script -Encoding UTF8

# Launch via a tiny VBScript shim that runs PowerShell truly windowless.
# -WindowStyle Hidden still briefly creates a console that grabs focus at
# launch - which is why every sample recorded 'powershell': the agent caught
# its own host window. WScript's Run with window-style 0 creates no window at
# all, so nothing ever steals foreground from the user's actual app.
$shim = Join-Path $dir "run.vbs"
@"
CreateObject("WScript.Shell").Run "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File ""$script""", 0, False
"@ | Set-Content -Path $shim -Encoding ascii

$action  = New-ScheduledTaskAction -Execute "wscript.exe" `
    -Argument "`"$shim`""
# A time-based trigger that starts now and repeats every minute for ~10 years,
# NOT an at-logon trigger. The logon trigger only fires on a fresh interactive
# logon (remote-background sessions don't count), so it can sit "Ready" and
# never run - which is exactly what happened. This fires regardless of logon
# state; if nobody's at the machine the sampler just finds no foreground
# window and exits, which is harmless.
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 1) `
    -RepetitionDuration (New-TimeSpan -Days 3650)
# 'Users' group principal = runs in the context of whichever user is
# interactively logged on, so the foreground-window read works.
$principal = New-ScheduledTaskPrincipal -GroupId "S-1-5-32-545" -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet -Hidden -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 2)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

# Fire it once now rather than waiting for the first scheduled tick. Without
# this there's a one-minute dead zone after install where nothing has run,
# which made every post-install check look broken. Start-ScheduledTask runs
# it in the same interactive-user context the trigger uses, so a successful
# run here also proves the principal is right before we walk away.
Start-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
Start-Sleep -Seconds 6
$info = Get-ScheduledTaskInfo -TaskName $taskName
Write-Output "Installed '$taskName' (runs as interactive user, every minute, hidden)."
Write-Output "First run result: $($info.LastTaskResult) (0 = ok)."
Write-Output "Monitoring is gated server-side by tenant '$Tenant' - dormant until enabled there."
