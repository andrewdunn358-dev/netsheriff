<#
    Net Sheriff - session reporter (NCS)
    Paste into Tactical Script Manager. Run on NCSServer every 1-2 minutes.
    Shell: PowerShell. Run as: SYSTEM.
#>

$PortalUrl  = "https://portal.netsheriff.co.uk"
$Tenant     = "NCS"
$AgentToken = "X66MOPjk1ORCiOJfNqhfnFSoFMPfs-zhCI4IaDN1ZIw"

$Exclude = @(
    'Administrator','NCSaccounts','NCSAdmin','NCSAdministrators',
    'NCSAdministratotr','PayrollConstruction','Retail','scanner','MWService'
)

$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$raw = & net session 2>$null
$sessions = @()
foreach ($line in $raw) {
    if ($line -match '^\\\\(\d{1,3}(?:\.\d{1,3}){3})\s+(\S+)') {
        $ip = $Matches[1]; $user = $Matches[2]
        if ($user -like '*$')                     { continue }
        if ($Exclude -contains $user)             { continue }
        if ($user -match '^(MSOL_|HealthMailbox)'){ continue }
        $sessions += @{ ip = $ip; username = $user }
    }
}

if ($sessions.Count -eq 0) { Write-Output "No sessions to report."; exit 0 }

$unique = $sessions | Sort-Object { "$($_.ip)|$($_.username)" } -Unique
$body = @{ tenant = $Tenant; sessions = $unique } | ConvertTo-Json -Depth 4 -Compress

try {
    $resp = Invoke-RestMethod -Uri "$PortalUrl/api/ip-users" -Method Post `
        -ContentType 'application/json' -Body $body `
        -Headers @{ 'X-Agent-Token' = $AgentToken } -TimeoutSec 30
    Write-Output "Reported $($unique.Count): created=$($resp.created) extended=$($resp.extended)"
    exit 0
} catch {
    Write-Output "Failed: $($_.Exception.Message)"
    exit 1
}
