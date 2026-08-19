# === uninstall_whisper_task.ps1 ===
# 2070 BOX      ->  Whisper\uninstall_whisper_task.ps1  (run as admin)
# =============================================================================
# Undoes install_whisper_task.ps1 completely.
#
# Stops the task, removes it, and optionally closes the firewall rule that
# allowed the Ryzen server to trigger it. After this the machine is back to
# how it was: you start whisper_server.py by hand in a PowerShell window.
# =============================================================================

$ErrorActionPreference = "Stop"
$TaskName = "VoiceConsoleWhisper"

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "No task named '$TaskName' - nothing to undo." -ForegroundColor Yellow
} else {
    Write-Host "Stopping '$TaskName'..."
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2

    # Stopping the task doesn't always reap the python.exe it launched,
    # which would leave port 9000 occupied and make a manual start fail
    # with a confusing "address already in use".
    $stragglers = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object { $_.CommandLine -like "*whisper_server.py*" }
    foreach ($p in $stragglers) {
        Write-Host "  Stopping leftover python.exe (PID $($p.ProcessId))"
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }

    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed the scheduled task." -ForegroundColor Green
}

Write-Host ""
$fw = Read-Host "Also close the 'Remote Scheduled Tasks Management' firewall rule? (y/n)"
if ($fw -eq "y") {
    Disable-NetFirewallRule -DisplayGroup "Remote Scheduled Tasks Management" -ErrorAction SilentlyContinue
    Write-Host "Firewall rule disabled." -ForegroundColor Green
}

Write-Host ""
Write-Host "Done. Port 9000 check:"
try {
    Invoke-RestMethod -Uri "http://localhost:9000/health" -TimeoutSec 2 | Out-Null
    Write-Host "  Something is STILL answering on 9000 - check for a stray" -ForegroundColor Yellow
    Write-Host "  python.exe, or a PowerShell window you left running." -ForegroundColor Yellow
} catch {
    Write-Host "  Nothing listening. Start it manually with:" -ForegroundColor Green
    Write-Host "    python whisper_server.py"
}