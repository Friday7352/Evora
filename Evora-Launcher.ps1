[CmdletBinding()]
param(
    [switch] $Start,
    [switch] $Stop
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSCommandPath
$TaskName = 'WhisperTranscriptionService'

function Test-EvoraAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

if ($Start -or $Stop) {
    if (-not (Test-EvoraAdmin)) {
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
$IconPath = Join-Path $Root 'Evora.ico'
$form = New-FrivoForm -Theme $Theme -Title 'Evora' -Width 470 -Height 440 -IconPath $IconPath -AppId 'Evora.Launcher'
$header = New-FrivoHeader -Theme $Theme -Form $form -Title 'Evora' -Subtitle 'Local transcription service for Frivo' -LogoPngPath (Join-Path $Root 'Evora.png')

$viewMain = New-Object System.Windows.Forms.Panel
$viewMain.Location = [System.Drawing.Point]::new(0, 84)
$viewMain.Size = [System.Drawing.Size]::new(470, 356)
$viewMain.BackColor = $Theme.Bg
$form.Controls.Add($viewMain)
$viewSettings = New-Object System.Windows.Forms.Panel
$viewSettings.Location = [System.Drawing.Point]::new(0, 84)
$viewSettings.Size = [System.Drawing.Size]::new(470, 356)
$viewSettings.BackColor = $Theme.Bg
$viewSettings.Visible = $false
$form.Controls.Add($viewSettings)

$serverCard = New-FrivoCard -Theme $Theme -Parent $viewMain -X 24 -Y 10 -W 422 -H 88
$dot = New-Object System.Windows.Forms.Panel
$dot.Location = [System.Drawing.Point]::new(18, 18)
$dot.Size = [System.Drawing.Size]::new(10, 10)
$dot.BackColor = $Theme.Warn
Set-FrivoRounded -Control $dot -Radius 10
$serverCard.Controls.Add($dot)
$status = New-FrivoLabel -Theme $Theme -Parent $serverCard -Text 'Checking Evora...' -X 42 -Y 12 -W 350 -H 22 -Font $Theme.FontMid -Color $Theme.Ink
[void](New-FrivoLabel -Theme $Theme -Parent $serverCard -Text 'ON THIS PC' -X 18 -Y 44 -W 270 -H 14 -Font $Theme.FontCaps -Color $Theme.Faint)
$localUrl = New-FrivoLabel -Theme $Theme -Parent $serverCard -Text 'http://localhost:9000' -X 18 -Y 60 -W 250 -H 22 -Font $Theme.FontUI -Color $Theme.Ink
$copy = New-FrivoButton -Theme $Theme -Parent $serverCard -Text 'Copy' -X 336 -Y 48 -W 68 -H 32

$modelCard = New-FrivoCard -Theme $Theme -Parent $viewMain -X 24 -Y 108 -W 422 -H 80
[void](New-FrivoLabel -Theme $Theme -Parent $modelCard -Text 'ACTIVE MODEL' -X 18 -Y 14 -W 270 -H 14 -Font $Theme.FontCaps -Color $Theme.Faint)
$modelSummary = New-FrivoLabel -Theme $Theme -Parent $modelCard -Text 'Waiting for Evora...' -X 18 -Y 34 -W 386 -H 28 -Font $Theme.FontMid -Color $Theme.Ink
$note = New-FrivoLabel -Theme $Theme -Parent $viewMain -Text 'Evora keeps transcription private on this computer.' -X 28 -Y 202 -W 414 -H 34 -Font $Theme.FontSmall -Color $Theme.Dim
$btnOpen = New-FrivoButton -Theme $Theme -Parent $viewMain -Text 'Open service status' -X 24 -Y 246 -W 422 -H 42 -Primary $true
$btnSettings = New-FrivoButton -Theme $Theme -Parent $viewMain -Text 'Settings' -X 24 -Y 300 -W 205 -H 36
$btnPower = New-FrivoButton -Theme $Theme -Parent $viewMain -Text 'Stop Evora' -X 241 -Y 300 -W 205 -H 36

$btnBack = New-FrivoButton -Theme $Theme -Parent $viewSettings -Text 'Back' -X 24 -Y 4 -W 90 -H 32
[void](New-FrivoLabel -Theme $Theme -Parent $viewSettings -Text 'MODEL' -X 30 -Y 50 -W 380 -H 16 -Font $Theme.FontCaps -Color $Theme.Faint)
$activeCard = New-FrivoCard -Theme $Theme -Parent $viewSettings -X 24 -Y 72 -W 422 -H 62
$activeModel = New-FrivoLabel -Theme $Theme -Parent $activeCard -Text 'Waiting for Evora...' -X 18 -Y 18 -W 386 -H 25 -Font $Theme.FontUI -Color $Theme.Ink
[void](New-FrivoLabel -Theme $Theme -Parent $viewSettings -Text 'DOWNLOADED MODELS' -X 30 -Y 150 -W 380 -H 16 -Font $Theme.FontCaps -Color $Theme.Faint)
$cachedCard = New-FrivoCard -Theme $Theme -Parent $viewSettings -X 24 -Y 172 -W 422 -H 70
$cachedModels = New-FrivoLabel -Theme $Theme -Parent $cachedCard -Text 'Checking the model cache...' -X 18 -Y 14 -W 386 -H 42 -Font $Theme.FontSmall -Color $Theme.Dim
[void](New-FrivoLabel -Theme $Theme -Parent $viewSettings -Text 'TROUBLESHOOTING' -X 30 -Y 258 -W 380 -H 16 -Font $Theme.FontCaps -Color $Theme.Faint)
$helpCard = New-FrivoCard -Theme $Theme -Parent $viewSettings -X 24 -Y 280 -W 422 -H 60
$btnLog = New-FrivoButton -Theme $Theme -Parent $helpCard -Text 'View service log' -X 16 -Y 12 -W 187 -H 36
$btnFolder = New-FrivoButton -Theme $Theme -Parent $helpCard -Text 'Open Evora folder' -X 219 -Y 12 -W 187 -H 36

function Get-CachedModelNames {
    $cache = Join-Path $Root 'model_cache'
    if (-not (Test-Path -LiteralPath $cache -PathType Container)) { return @() }
    $names = New-Object System.Collections.Generic.List[string]
    Get-ChildItem -LiteralPath $cache -Directory -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_.Name -match 'faster-whisper-(.+)$') { [void]$names.Add($Matches[1]) }
    }
    return @($names | Sort-Object -Unique)
}

function Update-EvoraStatus {
    $taskState = 'Not registered'
    try { $taskState = (Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop).State.ToString() } catch { }
    $health = $null
    try { $health = Invoke-RestMethod -Uri 'http://127.0.0.1:9000/health' -TimeoutSec 2 -ErrorAction Stop } catch { }
    $cached = @(Get-CachedModelNames)
    if ($cached.Count) { $cachedModels.Text = ($cached -join ', ') } else { $cachedModels.Text = 'No downloaded speech models found yet.' }
    if ($health -and $health.ok) {
        $dot.BackColor = $Theme.Signal
        $status.Text = 'Running'
        $modelSummary.Text = ('{0}  |  {1}' -f $health.model, $health.device)
        $activeModel.Text = ('Active: {0}`r`nDevice: {1} ({2})' -f $health.model, $health.device, $health.compute)
        $note.Text = 'Frivo can connect at http://localhost:9000.'
        $btnOpen.Text = 'Open service status'; $btnOpen.Enabled = $true
        $btnPower.Text = 'Stop Evora'; $btnPower.Enabled = $true
    } else {
        $dot.BackColor = if ($taskState -eq 'Running') { $Theme.Warn } else { $Theme.Faint }
        $status.Text = if ($taskState -eq 'Running') { 'Starting...' } else { 'Stopped' }
        $modelSummary.Text = 'Start Evora to view the active model.'
        $activeModel.Text = 'Evora is not running.'
        $note.Text = if ($taskState -eq 'Running') { 'The first model download can take several minutes.' } else { 'Evora can start automatically with Windows.' }
        $btnOpen.Text = 'Start Evora'; $btnOpen.Enabled = ($taskState -ne 'Running')
        $btnPower.Text = 'Stop Evora'; $btnPower.Enabled = ($taskState -eq 'Running')
    }
}

$copy.Add_Click({ try { [System.Windows.Forms.Clipboard]::SetText('http://localhost:9000'); $copy.Text = 'Copied' } catch { } })
$btnOpen.Add_Click({
    if ($btnOpen.Text -eq 'Start Evora') {
        Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-STA', '-WindowStyle', 'Hidden', '-File', ('"{0}"' -f $PSCommandPath), '-Start')
    } else { Start-Process 'http://localhost:9000' }
    Update-EvoraStatus
})
$btnPower.Add_Click({ Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-STA', '-WindowStyle', 'Hidden', '-File', ('"{0}"' -f $PSCommandPath), '-Stop'); Update-EvoraStatus })
$btnSettings.Add_Click({ Update-EvoraStatus; $viewMain.Visible = $false; $viewSettings.Visible = $true })
$btnBack.Add_Click({ $viewSettings.Visible = $false; $viewMain.Visible = $true })
$btnLog.Add_Click({ $log = Join-Path $Root 'whisper_service.log'; if (Test-Path -LiteralPath $log) { Start-Process notepad.exe -ArgumentList ('"{0}"' -f $log) } })
$btnFolder.Add_Click({ Start-Process explorer.exe -ArgumentList ('"{0}"' -f $Root) })

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 2500
$timer.Add_Tick({ Update-EvoraStatus; if ($copy.Text -eq 'Copied') { $copy.Text = 'Copy' } })
$timer.Start()
$form.Add_FormClosed({ $timer.Stop(); $timer.Dispose() })
Update-EvoraStatus
[void]$form.ShowDialog()
