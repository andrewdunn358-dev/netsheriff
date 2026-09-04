<#
    Net Sheriff - screen-time agent uninstaller
    Run via Tactical to remove the agent and its task from a machine.
    For staff monitoring, removal should be as clean and provable as install.
#>
$taskName = "NetSheriff Activity"
$dir      = "C:\Program Files\NetSheriff"

$removed = @()
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
    $removed += "scheduled task"
}
if (Test-Path $dir) {
    Remove-Item -Path $dir -Recurse -Force
    $removed += "agent files"
}

if ($removed.Count) {
    Write-Output "Removed: $($removed -join ', ')."
} else {
    Write-Output "Nothing to remove - agent was not installed."
}
# Verify
$stillThere = [bool](Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) -or (Test-Path $dir)
Write-Output ("Verification: " + $(if ($stillThere) { "FAILED - remnants remain" } else { "clean" }))
