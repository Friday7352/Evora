[CmdletBinding()]
param(
    [switch] $Start,
    [switch] $Stop
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSCommandPath
$TaskName = 'WhisperTranscriptionService'

function Test-WhisperAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if ($Start -or $Stop) {
    if (-not (Test-WhisperAdmin)) {
        $action = if ($Start) { '-Start' } else { '-Stop' }
        Start-Process -FilePath 'powershell.exe' -Verb RunAs -WindowStyle Hidden -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-STA', '-WindowStyle', 'Hidden',
            '-File', ('"{0}"' -f $PSCommandPath), $action
        )
        exit 0
    }
    if ($Start) { Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop }
    if ($Stop) { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue }
    exit 0
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
Import-Module (Join-Path $Root 'Whisper.Ui.psm1') -Force
$Theme = Get-FrivoTheme
$form = New-FrivoForm -Theme $Theme -Title 'Whisper' -Width 620 -Height 390
$header = New-FrivoHeader -Theme $Theme -Form $form -Title 'Whisper' -Subtitle 'Local transcription service for Frivo'
$header.Logo.BackColor = $Theme.Accent
Set-FrivoRounded -Control $header.Logo -Radius 12
$mark = New-FrivoLabel -Theme $Theme -Parent $header.Logo -Text 'W' -X 0 -Y 5 -W 36 -H 28 -Font $Theme.FontMid -Color ([System.Drawing.Color]::White)
$mark.TextAlign = [System.Drawing.ContentAlignment]::MiddleCenter

$card = New-FrivoCard -Theme $Theme -Parent $form -X 24 -Y 86 -W 572 -H 170
$dot = New-Object System.Windows.Forms.Panel
$dot.Location = New-Object System.Drawing.Point(22, 22); $dot.Size = New-Object System.Drawing.Size(12, 12)
$dot.BackColor = $Theme.Faint; Set-FrivoRounded -Control $dot -Radius 12; $card.Controls.Add($dot)
$status = New-FrivoLabel -Theme $Theme -Parent $card -Text 'Checking Whisper...' -X 48 -Y 16 -W 490 -H 24 -Font $Theme.FontMid -Color $Theme.Ink
$details = New-FrivoLabel -Theme $Theme -Parent $card -Text '' -X 22 -Y 52 -W 526 -H 90 -Font $Theme.FontSmall -Color $Theme.Dim

$btnRefresh = New-FrivoButton -Theme $Theme -Parent $form -Text 'Refresh' -X 24 -Y 284 -W 108 -H 36
$btnStop = New-FrivoButton -Theme $Theme -Parent $form -Text 'Stop service' -X 354 -Y 284 -W 110 -H 36
$btnStart = New-FrivoButton -Theme $Theme -Parent $form -Text 'Start service' -X 476 -Y 284 -W 120 -H 36 -Primary $true

function Update-WhisperStatus {
    $taskState = 'Not registered'
    try { $taskState = (Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop).State.ToString() } catch { }
    try {
        $health = Invoke-RestMethod -Uri 'http://127.0.0.1:9000/health' -TimeoutSec 2 -ErrorAction Stop
        if ($health.ok) {
            $dot.BackColor = $Theme.Signal
            $status.Text = 'Whisper is running'
            $details.Text = "Frivo can connect at http://localhost:9000`r`nModel: $($health.model)    Device: $($health.device)`r`nThe service starts automatically with Windows."
            $btnStart.Enabled = $false; $btnStop.Enabled = $true
            return
        }
    } catch { }
    $dot.BackColor = $Theme.Warn
    $status.Text = if ($taskState -eq 'Running') { 'Whisper is starting...' } else { 'Whisper is not running' }
    $details.Text = "Service task: $taskState`r`nThe first model download can take several minutes. Keep this window open to see when Whisper is ready."
    $btnStart.Enabled = $true; $btnStop.Enabled = ($taskState -eq 'Running')
}

$btnRefresh.Add_Click({ Update-WhisperStatus })
$btnStart.Add_Click({ Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-STA', '-WindowStyle', 'Hidden', '-File', ('"{0}"' -f $PSCommandPath), '-Start'); Update-WhisperStatus })
$btnStop.Add_Click({ Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-STA', '-WindowStyle', 'Hidden', '-File', ('"{0}"' -f $PSCommandPath), '-Stop'); Update-WhisperStatus })
$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 2000
$timer.Add_Tick({ Update-WhisperStatus })
$timer.Start()
$form.Add_FormClosed({ $timer.Stop(); $timer.Dispose() })
Update-WhisperStatus
[void]$form.ShowDialog()
