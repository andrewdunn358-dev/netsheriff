<#
    Net Sheriff — session reporter

    Runs on a client's domain controller via Tactical RMM (as SYSTEM) on a
    schedule. Parses `net session` and posts IP-to-username pairs to NxReport,
    which timestamps them so DNS logs can be attributed to real people.

    Why `net session`: it's the same source NxFilter's own mapper uses, and it
    needs no agent on workstations and no DHCP reservations.

    Run every 1-2 minutes. Sessions are transient — a machine that's idle or
    off simply won't appear, which is why frequent sampling matters.

    Configure below, or set the matching environment variables.
#>

$PortalUrl  = if ($env:NS_PORTAL_URL)  { $env:NS_PORTAL_URL }  else { "https://portal.netsheriff.co.uk" }
$Tenant     = if ($env:NS_TENANT)      { $env:NS_TENANT }      else { "NCS" }
$AgentToken = if ($env:NS_AGENT_TOKEN) { $env:NS_AGENT_TOKEN } else { "CHANGE-ME" }

# Accounts that aren't people. Machine accounts (trailing $) are excluded
# automatically. Extend per site as needed.
$Exclude = @(
    'Administrator','NCSaccounts','NCSAdmin','NCSAdministrators',
    'NCSAdministratotr','PayrollConstruction','Retail','scanner','MWService'
)

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# `net session` output is columnar and localised; match the leading \\IP and
# the username that follows rather than splitting on fixed positions.
$raw = & net session 2>$null
$sessions = @()
foreach ($line in $raw) {
    if ($line -match '^\\\\(\d{1,3}(?:\.\d{1,3}){3})\s+(\S+)') {
        $ip = $Matches[1]
        $user = $Matches[2]
        if ($user -like '*$')            { continue }  # machine account
        if ($Exclude -contains $user)    { continue }
        if ($user -match '^(MSOL_|HealthMailbox)') { continue }
        $sessions += @{ ip = $ip; username = $user }
    }
}

if ($sessions.Count -eq 0) {
    Write-Output "No sessions to report."
    exit 0
}

# Duplicates are normal (one machine can hold several sessions); the server
# collapses them, but trimming here keeps the payload small.
$unique = $sessions | Sort-Object { "$($_.ip)|$($_.username)" } -Unique

$body = @{ tenant = $Tenant; sessions = $unique } | ConvertTo-Json -Depth 4 -Compress

try {
    $resp = Invoke-RestMethod -Uri "$PortalUrl/api/ip-users" -Method Post `
        -ContentType 'application/json' -Body $body `
        -Headers @{ 'X-Agent-Token' = $AgentToken } -TimeoutSec 30
    Write-Output "Reported $($unique.Count) session(s): created=$($resp.created) extended=$($resp.extended)"
    exit 0
} catch {
    Write-Output "Failed to report sessions: $($_.Exception.Message)"
    exit 1
}
