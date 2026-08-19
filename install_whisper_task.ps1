# === install_whisper_task.ps1 ===
# 2070 BOX      ->  Whisper\install_whisper_task.ps1  (run once, as admin)
# =============================================================================
# Run this ONCE on the 2070 box, as Administrator.
#
# Registers the Whisper service as a scheduled task, so it starts at boot and
# can be triggered from the Ryzen server without a remote desktop session.
#
# Why a scheduled task rather than launching it remotely over WinRM: a
# process started inside a remote session is killed when that session
# closes. A scheduled task is owned by the machine and keeps running.
# =============================================================================

$ErrorActionPreference = "Stop"

# --- Adjust these if your paths differ -------------------------------------
$WhisperDir = "C:\Users\Administrator\Desktop\Whisper"
$PythonExe  = "C:\Users\Administrator\AppData\Local\Python\pythoncore-3.14-64\python.exe"
$TaskName   = "VoiceConsoleWhisper"
# ---------------------------------------------------------------------------

if (-not (Test-Path $PythonExe)) {
    Write-Host "Python not found at:" -ForegroundColor Red
    Write-Host "  $PythonExe"
    Write-Host "Find yours with:  (Get-Command python).Source"
    exit 1
}
if (-not (Test-Path (Join-Path $WhisperDir "whisper_server.py"))) {
    Write-Host "whisper_server.py not found in:" -ForegroundColor Red
    Write-Host "  $WhisperDir"
    exit 1
}

$LogFile   = Join-Path $WhisperDir "whisper_service.log"
$ModelCache = Join-Path $WhisperDir "model_cache"

Write-Host ""
Write-Host "Which account should the service run under?" -ForegroundColor Cyan
Write-Host ""
Write-Host "  [1] Your account, no console  (recommended)"
Write-Host "      Uses your profile and its model caches, starts at boot, and"
Write-Host "      keeps running when you log off or close windows. No password"
Write-Host "      is stored."
Write-Host ""
Write-Host "  [2] SYSTEM, at boot"
Write-Host "      Also console-free. Runs as the machine rather than as you,"
Write-Host "      which is fine now that model caches are pinned to a fixed"
Write-Host "      folder, but has no access to anything user-specific."
Write-Host ""
$mode = Read-Host "Choose (1/2)"

# Pin the model caches to a fixed folder inside the Whisper directory.
#
# This is the thing that most often breaks a service that worked fine by
# hand: Hugging Face and SpeechBrain cache models under the *user's* home
# directory, so a task running as a different account looks in a different
# place, finds nothing, and fails to load. Naming an explicit folder means
# every account uses the same one.
$envPrefix = "set HF_HOME=$ModelCache&& set HUGGINGFACE_HUB_CACHE=$ModelCache&& set TORCH_HOME=$ModelCache&& "

$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$envPrefix`"$PythonExe`" -u `"$WhisperDir\whisper_server.py`" >> `"$LogFile`" 2>&1`"" `
    -WorkingDirectory $WhisperDir

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)   # never kill it for running long

if ($mode -eq "2") {
    $trigger   = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
    $modeLabel = "SYSTEM, at boot"
} else {
    $me = "$env:USERDOMAIN\$env:USERNAME"

    # S4U ("service for user"), NOT Interactive.
    #
    # Interactive attaches a console window to your desktop session, and
    # anything that closes that window or ends the session kills the
    # service. The giveaway in the log is:
    #     forrtl: error (200): program aborting due to window-CLOSE event
    # which is NumPy's Fortran runtime reporting exactly that. Closing a
    # PowerShell window, logging off, or disconnecting RDP was enough.
    #
    # S4U runs under your account with no console and no stored password,
    # whether or not you are logged on - so it survives all of the above
    # while still using your profile and its model caches.
    $trigger   = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId $me -LogonType S4U -RunLevel Highest
    $modeLabel = "$me, at boot (no console)"
}

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Write-Host "Replacing existing task '$TaskName'..."
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Local Whisper transcription service for Voice Console" | Out-Null

Write-Host ""
Write-Host "Registered '$TaskName' - running as $modeLabel" -ForegroundColor Green
Write-Host "  Model cache: $ModelCache"
Write-Host "  Log file:    $LogFile"
Write-Host ""
Write-Host "To allow starting it from the Ryzen server, run this too:"
Write-Host "  Enable-NetFirewallRule -DisplayGroup 'Remote Scheduled Tasks Management'"
Write-Host ""

$answer = Read-Host "Start it now and watch for it coming up? (y/n)"
if ($answer -ne "y") { exit 0 }

# Truncate first, so what's printed below is this run and not the last one.
"" | Set-Content -Path $LogFile
Start-ScheduledTask -TaskName $TaskName

Write-Host ""
Write-Host "Waiting for it to answer on port 9000..."
Write-Host "(first run downloads about 1.5GB into the new cache folder, so"
Write-Host " the first start can take several minutes - waiting up to 15)"

$up = $false
for ($i = 0; $i -lt 450; $i++) {
    Start-Sleep -Seconds 2
    try {
        $r = Invoke-RestMethod -Uri "http://localhost:9000/health" -TimeoutSec 2
        Write-Host ""
        Write-Host "Up. Model '$($r.model)' on $($r.device)." -ForegroundColor Green
        if ($r.speaker_labelling) { Write-Host "Speaker labelling: on ($($r.speaker_backend))" }
        $up = $true
        break
    } catch {
        # Show the cache growing, so a long wait is visibly a download
        # rather than a hang.
        if ($i % 15 -eq 0 -and (Test-Path $ModelCache)) {
            $mb = [math]::Round(((Get-ChildItem $ModelCache -Recurse -File -ErrorAction SilentlyContinue |
                    Measure-Object Length -Sum).Sum / 1MB), 0)
            Write-Host ""
            Write-Host ("  {0,4}s   cache {1:N0} MB" -f ($i * 2), $mb) -NoNewline
        } else {
            Write-Host "." -NoNewline
        }
    }
}

if (-not $up) {
    Write-Host ""
    Write-Host "Still not answering. Last 30 lines of the log:" -ForegroundColor Yellow
    Write-Host "---------------------------------------------"
    if (Test-Path $LogFile) { Get-Content $LogFile -Tail 30 } else { Write-Host "(log file is empty)" }
    Write-Host "---------------------------------------------"
    Write-Host ""
    Write-Host "If the log is empty, the task never launched Python - check"
    Write-Host "Task Scheduler for '$TaskName' and look at its Last Run Result."
}