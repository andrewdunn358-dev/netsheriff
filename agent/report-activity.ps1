<#
    Net Sheriff - screen time reporter

    Runs on domain-joined Windows workstations via Tactical RMM.
    Samples which application is in the foreground and reports it.

    PRIVACY: window titles are NOT stored or transmitted. The title is read
    only to match against the leisure-site list below; anything that doesn't
    match is discarded before sending. Document names, email subjects and
    customer names never leave the machine.

    Run every 1 minute. Each run represents 60 seconds of observed time.
    Time is only counted when a sample is taken, so a sleeping or missed
    machine undercounts rather than overcounts.
#>

$PortalUrl  = "https://portal.netsheriff.co.uk"
$Tenant     = "NCS"
$AgentToken = "CHANGE-ME"
$SampleSecs = 60

# Sites we report time against. Anything not on this list is recorded as
# application time only, with no site.
$LeisureSites = @{
    'facebook'  = 'Facebook';   'instagram' = 'Instagram'
    'twitter'   = 'X/Twitter';  ' x '       = 'X/Twitter'
    'tiktok'    = 'TikTok';     'snapchat'  = 'Snapchat'
    'youtube'   = 'YouTube';    'netflix'   = 'Netflix'
    'reddit'    = 'Reddit';     'linkedin'  = 'LinkedIn'
    'amazon'    = 'Amazon';     'ebay'      = 'eBay'
    'hotukdeals'= 'HotUKDeals'; 'asos'      = 'ASOS'
    'bet365'    = 'Betting';    'paddypower'= 'Betting'
    'whatsapp'  = 'WhatsApp';   'pinterest' = 'Pinterest'
    'twitch'    = 'Twitch';     'spotify'   = 'Spotify'
}

# Friendly application names; anything else falls back to the process name.
$AppNames = @{
    'chrome' = 'Chrome'; 'msedge' = 'Edge'; 'firefox' = 'Firefox'
    'outlook' = 'Outlook'; 'excel' = 'Excel'; 'winword' = 'Word'
    'powerpnt' = 'PowerPoint'; 'teams' = 'Teams'; 'ms-teams' = 'Teams'
    'explorer' = 'File Explorer'; 'sage' = 'Sage'
}
$Browsers = @('chrome','msedge','firefox')

$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;
public class Fg {
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] public static extern int GetWindowThreadProcessId(IntPtr h, out uint pid);
}
"@

$hwnd = [Fg]::GetForegroundWindow()
if ($hwnd -eq [IntPtr]::Zero) { Write-Output "No foreground window."; exit 0 }

$sb = New-Object System.Text.StringBuilder 512
[void][Fg]::GetWindowText($hwnd, $sb, $sb.Capacity)
$title = $sb.ToString()

$pid_ = 0
[void][Fg]::GetWindowThreadProcessId($hwnd, [ref]$pid_)
$proc = (Get-Process -Id $pid_ -ErrorAction SilentlyContinue)
if (-not $proc) { Write-Output "No process."; exit 0 }

$procName = $proc.ProcessName.ToLower()
$app = if ($AppNames.ContainsKey($procName)) { $AppNames[$procName] } else { $proc.ProcessName }

# Only browsers get site matching, and only against the known list.
$site = $null
if ($Browsers -contains $procName -and $title) {
    $lower = $title.ToLower()
    foreach ($k in $LeisureSites.Keys) {
        if ($lower.Contains($k)) { $site = $LeisureSites[$k]; break }
    }
}
# Title is discarded here. It is never added to the payload.
$title = $null

# Who is actually at the machine (the console session), not the service account.
$user = (Get-CimInstance Win32_ComputerSystem).UserName
if (-not $user) { Write-Output "Nobody signed in."; exit 0 }
$user = $user.Split('\')[-1]

$body = @{
    tenant  = $Tenant
    samples = @(@{
        username   = $user
        hostname   = $env:COMPUTERNAME
        app        = $app
        site       = $site
        sampled_at = (Get-Date).ToString('yyyy-MM-dd HH:mm:ss')
        seconds    = $SampleSecs
    })
} | ConvertTo-Json -Depth 4 -Compress

try {
    $r = Invoke-RestMethod -Uri "$PortalUrl/api/activity" -Method Post `
        -ContentType 'application/json' -Body $body `
        -Headers @{ 'X-Agent-Token' = $AgentToken } -TimeoutSec 20
    Write-Output "Sampled: $user - $app$(if($site){" ($site)"}) [stored=$($r.stored)]"
    exit 0
} catch {
    Write-Output "Failed: $($_.Exception.Message)"
    exit 1
}
