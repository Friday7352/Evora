[CmdletBinding()]
param(
    [switch] $Start,
    [switch] $Stop,
    [switch] $EnableStartup,
    [switch] $DisableStartup,
    [switch] $EnableLan,
    [switch] $DisableLan
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSCommandPath
$TaskName = 'WhisperTranscriptionService'

function Test-EvoraAdmin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-EvoraRequestedAction {
    if ($Start) { return 'Start' }
    if ($Stop) { return 'Stop' }
    if ($EnableStartup) { return 'EnableStartup' }
    if ($DisableStartup) { return 'DisableStartup' }
    if ($EnableLan) { return 'EnableLan' }
    if ($DisableLan) { return 'DisableLan' }
    return $null
}

$requestedAction = Get-EvoraRequestedAction
if ($requestedAction) {
    if (-not (Test-EvoraAdmin)) {
        $action = '-' + $requestedAction
        Start-Process -FilePath 'powershell.exe' -Verb RunAs -WindowStyle Hidden -ArgumentList @(
            '-NoProfile', '-ExecutionPolicy', 'Bypass', '-STA', '-WindowStyle', 'Hidden',
            '-File', ('"{0}"' -f $PSCommandPath), $action
        )
        exit 0
    }
    switch ($requestedAction) {
        'Start' { Start-ScheduledTask -TaskName $TaskName -ErrorAction Stop }
        'Stop' { Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue }
        'EnableStartup' { Enable-ScheduledTask -TaskName $TaskName -ErrorAction Stop }
        'DisableStartup' { Disable-ScheduledTask -TaskName $TaskName -ErrorAction Stop }
        'EnableLan' {
            $python = Join-Path $Root '.venv\Scripts\python.exe'
            if (-not (Test-Path -LiteralPath $python -PathType Leaf)) { throw 'Evora is not fully installed yet.' }
            Get-NetFirewallRule -DisplayName 'Whisper transcription service' -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
            New-NetFirewallRule -DisplayName 'Whisper transcription service' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 9000 -Program $python -Profile Private -RemoteAddress LocalSubnet | Out-Null
        }
        'DisableLan' { Get-NetFirewallRule -DisplayName 'Whisper transcription service' -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue }
    }
    exit 0
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
Import-Module (Join-Path $Root 'Whisper.Ui.psm1') -Force
$Theme = Get-FrivoTheme
$IconPath = Join-Path $Root 'Evora.ico'
$form = New-FrivoForm -Theme $Theme -Title 'Evora' -Width 470 -Height 570 -IconPath $IconPath -AppId 'Evora.Launcher'
$header = New-FrivoHeader -Theme $Theme -Form $form -Title 'Evora' -Subtitle 'Local transcription service for Frivo' -LogoPngPath (Join-Path $Root 'Evora.png')

$viewMain = New-Object System.Windows.Forms.Panel
$viewMain.Location = [System.Drawing.Point]::new(0, 84)
$viewMain.Size = [System.Drawing.Size]::new(470, 486)
$viewMain.BackColor = $Theme.Bg
$form.Controls.Add($viewMain)
$viewSettings = New-Object System.Windows.Forms.Panel
$viewSettings.Location = [System.Drawing.Point]::new(0, 84)
$viewSettings.Size = [System.Drawing.Size]::new(470, 486)
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

$lanCard = New-FrivoCard -Theme $Theme -Parent $viewMain -X 24 -Y 108 -W 422 -H 78
[void](New-FrivoLabel -Theme $Theme -Parent $lanCard -Text 'OTHER DEVICES ON YOUR NETWORK' -X 18 -Y 14 -W 300 -H 14 -Font $Theme.FontCaps -Color $Theme.Faint)
$lanUrl = New-FrivoLabel -Theme $Theme -Parent $lanCard -Text '' -X 18 -Y 34 -W 270 -H 24 -Font $Theme.FontMid -Color $Theme.Ink
$lanCopy = New-FrivoButton -Theme $Theme -Parent $lanCard -Text 'Copy' -X 336 -Y 26 -W 68 -H 32
$lanCard.Visible = $false

$modelCard = New-FrivoCard -Theme $Theme -Parent $viewMain -X 24 -Y 108 -W 200 -H 80
[void](New-FrivoLabel -Theme $Theme -Parent $modelCard -Text 'ACTIVE MODEL' -X 18 -Y 14 -W 164 -H 14 -Font $Theme.FontCaps -Color $Theme.Faint)
$modelSummary = New-FrivoLabel -Theme $Theme -Parent $modelCard -Text 'Waiting...' -X 18 -Y 38 -W 164 -H 24 -Font $Theme.FontMid -Color $Theme.Ink
$deviceCard = New-FrivoCard -Theme $Theme -Parent $viewMain -X 236 -Y 108 -W 210 -H 80
[void](New-FrivoLabel -Theme $Theme -Parent $deviceCard -Text 'PROCESSING' -X 18 -Y 14 -W 174 -H 14 -Font $Theme.FontCaps -Color $Theme.Faint)
$deviceSummary = New-FrivoLabel -Theme $Theme -Parent $deviceCard -Text 'Waiting...' -X 18 -Y 38 -W 174 -H 24 -Font $Theme.FontMid -Color $Theme.Ink
$note = New-FrivoLabel -Theme $Theme -Parent $viewMain -Text 'Evora keeps transcription private on this computer.' -X 28 -Y 202 -W 414 -H 34 -Font $Theme.FontSmall -Color $Theme.Dim
$btnOpen = New-FrivoButton -Theme $Theme -Parent $viewMain -Text 'Open service status' -X 24 -Y 246 -W 422 -H 42 -Primary $true
$btnSettings = New-FrivoButton -Theme $Theme -Parent $viewMain -Text 'Settings' -X 24 -Y 300 -W 205 -H 36
$btnPower = New-FrivoButton -Theme $Theme -Parent $viewMain -Text 'Stop Evora' -X 241 -Y 300 -W 205 -H 36

$btnBack = New-FrivoButton -Theme $Theme -Parent $viewSettings -Text 'Back' -X 24 -Y 4 -W 90 -H 32
[void](New-FrivoLabel -Theme $Theme -Parent $viewSettings -Text 'STARTUP' -X 30 -Y 48 -W 380 -H 16 -Font $Theme.FontCaps -Color $Theme.Faint)
$startupCard = New-FrivoCard -Theme $Theme -Parent $viewSettings -X 24 -Y 70 -W 422 -H 62
$startupCheck = New-FrivoCheck -Theme $Theme -Parent $startupCard -Text 'Start Evora automatically when Windows starts' -X 18 -Y 11 -W 386 -Checked $true
[void](New-FrivoLabel -Theme $Theme -Parent $startupCard -Text 'Keeps private transcription ready for Frivo.' -X 38 -Y 35 -W 350 -H 18 -Font $Theme.FontSmall -Color $Theme.Dim)
$networkHeading = New-FrivoLabel -Theme $Theme -Parent $viewSettings -Text 'NETWORK ACCESS' -X 30 -Y 146 -W 380 -H 16 -Font $Theme.FontCaps -Color $Theme.Faint
$networkCard = New-FrivoCard -Theme $Theme -Parent $viewSettings -X 24 -Y 168 -W 422 -H 62
$networkAction = New-FrivoButton -Theme $Theme -Parent $networkCard -Text 'Open port 9000' -X 18 -Y 13 -W 160 -H 36
[void](New-FrivoLabel -Theme $Theme -Parent $networkCard -Text 'Lets Frivo devices on your private network connect to Evora.' -X 192 -Y 14 -W 208 -H 32 -Font $Theme.FontSmall -Color $Theme.Dim)
$modelHeading = New-FrivoLabel -Theme $Theme -Parent $viewSettings -Text 'MODEL' -X 30 -Y 244 -W 380 -H 16 -Font $Theme.FontCaps -Color $Theme.Faint
$activeCard = New-FrivoCard -Theme $Theme -Parent $viewSettings -X 24 -Y 266 -W 200 -H 62
[void](New-FrivoLabel -Theme $Theme -Parent $activeCard -Text 'ACTIVE MODEL' -X 18 -Y 10 -W 164 -H 14 -Font $Theme.FontCaps -Color $Theme.Faint)
$activeModel = New-FrivoLabel -Theme $Theme -Parent $activeCard -Text 'Waiting...' -X 18 -Y 31 -W 164 -H 22 -Font $Theme.FontUI -Color $Theme.Ink
$activeDeviceCard = New-FrivoCard -Theme $Theme -Parent $viewSettings -X 236 -Y 266 -W 210 -H 62
[void](New-FrivoLabel -Theme $Theme -Parent $activeDeviceCard -Text 'PROCESSING' -X 18 -Y 10 -W 174 -H 14 -Font $Theme.FontCaps -Color $Theme.Faint)
$activeDevice = New-FrivoLabel -Theme $Theme -Parent $activeDeviceCard -Text 'Waiting...' -X 18 -Y 31 -W 174 -H 22 -Font $Theme.FontUI -Color $Theme.Ink
$downloadHeading = New-FrivoLabel -Theme $Theme -Parent $viewSettings -Text 'DOWNLOADED MODELS' -X 30 -Y 342 -W 380 -H 16 -Font $Theme.FontCaps -Color $Theme.Faint
$cachedCard = New-FrivoCard -Theme $Theme -Parent $viewSettings -X 24 -Y 364 -W 422 -H 54
$cachedModels = New-FrivoLabel -Theme $Theme -Parent $cachedCard -Text 'Checking model cache...' -X 18 -Y 16 -W 386 -H 22 -Font $Theme.FontSmall -Color $Theme.Dim
$btnLog = New-FrivoButton -Theme $Theme -Parent $viewSettings -Text 'View service log' -X 24 -Y 434 -W 205 -H 36
$btnFolder = New-FrivoButton -Theme $Theme -Parent $viewSettings -Text 'Open Evora folder' -X 241 -Y 434 -W 205 -H 36

function Get-CachedModelNames {
    $cache = Join-Path $Root 'model_cache'
    if (-not (Test-Path -LiteralPath $cache -PathType Container)) { return @() }
    $names = New-Object System.Collections.Generic.List[string]
    Get-ChildItem -LiteralPath $cache -Directory -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_.Name -match 'faster-whisper-(.+)$') { [void]$names.Add($Matches[1]) }
    }
    return @($names | Sort-Object -Unique)
}

function ConvertTo-EvoraTitle([string] $Text) {
    if ([string]::IsNullOrWhiteSpace($Text)) { return 'Unknown' }
    return $Text.Substring(0, 1).ToUpperInvariant() + $Text.Substring(1)
}

function Test-EvoraStartupEnabled {
    try { return [bool](Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop).Settings.Enabled } catch { return $false }
}

function Test-EvoraNetworkEnabled {
    return $null -ne (Get-NetFirewallRule -DisplayName 'Whisper transcription service' -ErrorAction SilentlyContinue | Select-Object -First 1)
}

function Get-EvoraLanIp {
    try {
        $socket = New-Object System.Net.Sockets.Socket([System.Net.Sockets.AddressFamily]::InterNetwork, [System.Net.Sockets.SocketType]::Dgram, [System.Net.Sockets.ProtocolType]::Udp)
        try { $socket.Connect('8.8.8.8', 80); return ([System.Net.IPEndPoint]$socket.LocalEndPoint).Address.ToString() } finally { $socket.Close() }
    } catch { return $null }
}

function Test-EvoraLocalName {
    try { return $null -ne ([System.Net.Dns]::GetHostAddresses('evora.local') | Where-Object { $_.ToString().StartsWith('127.') } | Select-Object -First 1) } catch { return $false }
}

$script:cachedModelText = 'Checking model cache...'
$script:nextModelCacheScan = [DateTime]::MinValue
$script:loadingSettings = $false

function Set-EvoraMainNetworkState([bool] $ShowLan) {
    $lanCard.Visible = $ShowLan
    $y = if ($ShowLan) { 196 } else { 108 }
    $modelCard.Location = [System.Drawing.Point]::new(24, $y)
    $deviceCard.Location = [System.Drawing.Point]::new(236, $y)
    $below = if ($ShowLan) { 88 } else { 0 }
    $note.Location = [System.Drawing.Point]::new(28, 202 + $below)
    $btnOpen.Location = [System.Drawing.Point]::new(24, 246 + $below)
    $btnSettings.Location = [System.Drawing.Point]::new(24, 300 + $below)
    $btnPower.Location = [System.Drawing.Point]::new(241, 300 + $below)
}

function Set-EvoraSettingsNetworkState([bool] $PortOpen) {
    $networkHeading.Visible = -not $PortOpen
    $networkCard.Visible = -not $PortOpen
    $offset = if ($PortOpen) { -98 } else { 0 }
    $modelHeading.Location = [System.Drawing.Point]::new(30, 244 + $offset)
    $activeCard.Location = [System.Drawing.Point]::new(24, 266 + $offset)
    $activeDeviceCard.Location = [System.Drawing.Point]::new(236, 266 + $offset)
    $downloadHeading.Location = [System.Drawing.Point]::new(30, 342 + $offset)
    $cachedCard.Location = [System.Drawing.Point]::new(24, 364 + $offset)
    $btnLog.Location = [System.Drawing.Point]::new(24, 434 + $offset)
    $btnFolder.Location = [System.Drawing.Point]::new(241, 434 + $offset)
}

function Update-EvoraStatus {
    $taskState = 'Not registered'
    try { $taskState = (Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop).State.ToString() } catch { }
    $health = $null
    if ($taskState -eq 'Running') {
        try { $health = Invoke-RestMethod -Uri 'http://127.0.0.1:9000/health' -TimeoutSec 1 -ErrorAction Stop } catch { }
    }
    if ([DateTime]::Now -ge $script:nextModelCacheScan) {
        $cached = @(Get-CachedModelNames)
        $script:cachedModelText = if ($cached.Count) { (($cached | ForEach-Object { ConvertTo-EvoraTitle $_ }) -join ', ') } else { 'No downloaded speech models found yet.' }
        $script:nextModelCacheScan = [DateTime]::Now.AddSeconds(30)
    }
    $cachedModels.Text = $script:cachedModelText
    $localAddress = if (Test-EvoraLocalName) { 'http://evora.local:9000' } else { 'http://localhost:9000' }
    $localUrl.Text = $localAddress
    $lanAddress = Get-EvoraLanIp
    $portOpen = Test-EvoraNetworkEnabled
    $showLan = $portOpen -and -not [string]::IsNullOrWhiteSpace($lanAddress)
    if ($showLan) { $lanUrl.Text = ('http://{0}:9000' -f $lanAddress) }
    Set-EvoraMainNetworkState $showLan
    Set-EvoraSettingsNetworkState $portOpen
    if ($health -and $health.ok) {
        $modelName = ConvertTo-EvoraTitle $health.model
        $processing = if ($health.device -eq 'cuda') { 'GPU (CUDA)' } elseif ($health.device -eq 'cpu') { 'CPU' } else { ConvertTo-EvoraTitle $health.device }
        $dot.BackColor = $Theme.Signal
        $status.Text = 'Running'
        $modelSummary.Text = $modelName
        $deviceSummary.Text = $processing
        $activeModel.Text = $modelName
        $activeDevice.Text = $processing
        $note.Text = if ($showLan) { 'evora.local works on this PC. Other devices use the address above.' } else { 'Frivo can connect at ' + $localAddress + '.' }
        $btnOpen.Text = 'Open service status'; $btnOpen.Enabled = $true
        $btnPower.Text = 'Stop Evora'; $btnPower.Enabled = $true
    } else {
        $dot.BackColor = if ($taskState -eq 'Running') { $Theme.Warn } else { $Theme.Faint }
        $status.Text = if ($taskState -eq 'Running') { 'Starting...' } else { 'Stopped' }
        $modelSummary.Text = 'Not running'
        $deviceSummary.Text = 'Not running'
        $activeModel.Text = 'Not running'
        $activeDevice.Text = 'Not running'
        $note.Text = if ($taskState -eq 'Running') { 'The first model download can take several minutes.' } else { 'Evora can start automatically with Windows.' }
        $btnOpen.Text = 'Start Evora'; $btnOpen.Enabled = ($taskState -ne 'Running')
        $btnPower.Text = 'Stop Evora'; $btnPower.Enabled = ($taskState -eq 'Running')
    }
}

$copy.Add_Click({ try { [System.Windows.Forms.Clipboard]::SetText($localUrl.Text); $copy.Text = 'Copied' } catch { } })
$lanCopy.Add_Click({ try { [System.Windows.Forms.Clipboard]::SetText($lanUrl.Text); $lanCopy.Text = 'Copied' } catch { } })
$btnOpen.Add_Click({
    if ($btnOpen.Text -eq 'Start Evora') {
        Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-STA', '-WindowStyle', 'Hidden', '-File', ('"{0}"' -f $PSCommandPath), '-Start')
    } else { Start-Process $localUrl.Text }
    Update-EvoraStatus
})
$btnPower.Add_Click({ Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-STA', '-WindowStyle', 'Hidden', '-File', ('"{0}"' -f $PSCommandPath), '-Stop'); Update-EvoraStatus })
$btnSettings.Add_Click({
    $script:loadingSettings = $true
    $startupCheck.Checked = Test-EvoraStartupEnabled
    $script:loadingSettings = $false
    $viewMain.Visible = $false; $viewSettings.Visible = $true
})
$btnBack.Add_Click({ $viewSettings.Visible = $false; $viewMain.Visible = $true })
$startupCheck.Add_CheckedChanged({
    if ($script:loadingSettings) { return }
    $action = if ($startupCheck.Checked) { '-EnableStartup' } else { '-DisableStartup' }
    Start-Process -FilePath 'powershell.exe' -Verb RunAs -WindowStyle Hidden -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-STA', '-WindowStyle', 'Hidden', '-File', ('"{0}"' -f $PSCommandPath), $action)
})
$networkAction.Add_Click({
    $networkAction.Enabled = $false
    $networkAction.Text = 'Opening...'
    [System.Windows.Forms.Application]::DoEvents()
    Start-Process -FilePath 'powershell.exe' -Verb RunAs -Wait -WindowStyle Hidden -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-STA', '-WindowStyle', 'Hidden', '-File', ('"{0}"' -f $PSCommandPath), '-EnableLan') | Out-Null
    $networkAction.Text = 'Open port 9000'
    Update-EvoraStatus
})
$btnLog.Add_Click({ $log = Join-Path $Root 'whisper_service.log'; if (Test-Path -LiteralPath $log) { Start-Process notepad.exe -ArgumentList ('"{0}"' -f $log) } })
$btnFolder.Add_Click({ Start-Process explorer.exe -ArgumentList ('"{0}"' -f $Root) })

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 5000
$timer.Add_Tick({ Update-EvoraStatus; if ($copy.Text -eq 'Copied') { $copy.Text = 'Copy' }; if ($lanCopy.Text -eq 'Copied') { $lanCopy.Text = 'Copy' } })
$timer.Start()
$form.Add_FormClosed({ $timer.Stop(); $timer.Dispose() })
Update-EvoraStatus
[void]$form.ShowDialog()
