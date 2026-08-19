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
$SettingsDirectory = Join-Path $env:APPDATA 'Evora'
$SettingsPath = Join-Path $SettingsDirectory 'launcher.json'
$script:launcherSettings = @{ CloseAction = 'keep' }
$script:loadingSettings = $false
$script:serviceRunning = $false

function Read-EvoraLauncherSettings {
    if (-not (Test-Path -LiteralPath $SettingsPath)) { return }
    try {
        $saved = Get-Content -LiteralPath $SettingsPath -Raw | ConvertFrom-Json
        if ($saved.PSObject.Properties['CloseAction'] -and $saved.CloseAction -in @('keep', 'stop')) {
            $script:launcherSettings.CloseAction = [string] $saved.CloseAction
        }
    } catch { }
}

function Save-EvoraLauncherSettings {
    try {
        New-Item -ItemType Directory -Path $SettingsDirectory -Force | Out-Null
        [IO.File]::WriteAllText($SettingsPath, ($script:launcherSettings | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
    } catch { }
}

Read-EvoraLauncherSettings

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

function Get-EvoraFirewallRules {
    $python = Join-Path $Root '.venv\Scripts\python.exe'
    return @(Get-NetFirewallRule -DisplayName 'Whisper transcription service' -ErrorAction SilentlyContinue | Where-Object {
        $application = Get-NetFirewallApplicationFilter -AssociatedNetFirewallRule $_ -ErrorAction SilentlyContinue
        $application -and $application.Program -and $application.Program.Equals($python, [StringComparison]::OrdinalIgnoreCase)
    })
}

function Remove-EvoraFirewallRule {
    Get-EvoraFirewallRules | Remove-NetFirewallRule -ErrorAction SilentlyContinue
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
            Remove-EvoraFirewallRule
            New-NetFirewallRule -DisplayName 'Whisper transcription service' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 9000 -Program $python -Profile Private -RemoteAddress LocalSubnet | Out-Null
        }
        'DisableLan' { Remove-EvoraFirewallRule }
    }
    exit 0
}

# Match Frivo's single-launcher behavior.  A second launch signals the first
# window (including one hidden in the notification area) and exits.
$createdMutex = $false
$instanceLock = New-Object System.Threading.Mutex($true, 'Local\EvoraLauncher', [ref] $createdMutex)
$showSignal = New-Object System.Threading.EventWaitHandle($false,
    [System.Threading.EventResetMode]::ManualReset, 'Local\EvoraLauncherShow')
if (-not $createdMutex) {
    [void] $showSignal.Set()
    exit 0
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
Import-Module (Join-Path $Root 'Whisper.Ui.psm1') -Force
$Theme = Get-FrivoTheme
$IconPath = Join-Path $Root 'EvoraIcon.ico'
$form = New-FrivoForm -Theme $Theme -Title 'Evora' -Width 470 -Height 570 -IconPath $IconPath
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
[void](New-FrivoLabel -Theme $Theme -Parent $viewSettings -Text 'WHEN I CLOSE THIS WINDOW' -X 30 -Y 42 -W 380 -H 16 -Font $Theme.FontCaps -Color $Theme.Faint)
$closeCard = New-FrivoCard -Theme $Theme -Parent $viewSettings -X 24 -Y 62 -W 422 -H 84
$keepInTray = New-FrivoRadio -Theme $Theme -Parent $closeCard -Text 'Keep Evora running in the background' -X 18 -Y 7 -W 386 -Checked $true
[void](New-FrivoLabel -Theme $Theme -Parent $closeCard -Text 'Minimizes Evora to the notification area.' -X 38 -Y 28 -W 350 -H 16 -Font $Theme.FontSmall -Color $Theme.Dim)
$stopOnClose = New-FrivoRadio -Theme $Theme -Parent $closeCard -Text 'Stop Evora' -X 18 -Y 47 -W 386
[void](New-FrivoLabel -Theme $Theme -Parent $closeCard -Text 'Stops the local transcription service and closes Evora.' -X 38 -Y 66 -W 360 -H 14 -Font $Theme.FontSmall -Color $Theme.Dim)
[void](New-FrivoLabel -Theme $Theme -Parent $viewSettings -Text 'STARTUP' -X 30 -Y 156 -W 380 -H 16 -Font $Theme.FontCaps -Color $Theme.Faint)
$startupCard = New-FrivoCard -Theme $Theme -Parent $viewSettings -X 24 -Y 174 -W 422 -H 62
$startupCheck = New-FrivoCheck -Theme $Theme -Parent $startupCard -Text 'Start Evora automatically when Windows starts' -X 18 -Y 11 -W 386 -Checked $true
[void](New-FrivoLabel -Theme $Theme -Parent $startupCard -Text 'Keeps private transcription ready for Frivo.' -X 38 -Y 35 -W 350 -H 18 -Font $Theme.FontSmall -Color $Theme.Dim)
$networkHeading = New-FrivoLabel -Theme $Theme -Parent $viewSettings -Text 'NETWORK ACCESS' -X 30 -Y 246 -W 380 -H 16 -Font $Theme.FontCaps -Color $Theme.Faint
$networkCard = New-FrivoCard -Theme $Theme -Parent $viewSettings -X 24 -Y 266 -W 422 -H 62
$networkAction = New-FrivoButton -Theme $Theme -Parent $networkCard -Text 'Open port 9000' -X 18 -Y 13 -W 160 -H 36
[void](New-FrivoLabel -Theme $Theme -Parent $networkCard -Text 'Lets Frivo devices on your private network connect to Evora.' -X 192 -Y 14 -W 208 -H 32 -Font $Theme.FontSmall -Color $Theme.Dim)
$modelHeading = New-FrivoLabel -Theme $Theme -Parent $viewSettings -Text 'MODEL' -X 30 -Y 338 -W 380 -H 16 -Font $Theme.FontCaps -Color $Theme.Faint
$activeCard = New-FrivoCard -Theme $Theme -Parent $viewSettings -X 24 -Y 356 -W 200 -H 62
[void](New-FrivoLabel -Theme $Theme -Parent $activeCard -Text 'ACTIVE MODEL' -X 18 -Y 10 -W 164 -H 14 -Font $Theme.FontCaps -Color $Theme.Faint)
$activeModel = New-FrivoLabel -Theme $Theme -Parent $activeCard -Text 'Waiting...' -X 18 -Y 31 -W 164 -H 22 -Font $Theme.FontUI -Color $Theme.Ink
$activeDeviceCard = New-FrivoCard -Theme $Theme -Parent $viewSettings -X 236 -Y 356 -W 210 -H 62
[void](New-FrivoLabel -Theme $Theme -Parent $activeDeviceCard -Text 'PROCESSING' -X 18 -Y 10 -W 174 -H 14 -Font $Theme.FontCaps -Color $Theme.Faint)
$activeDevice = New-FrivoLabel -Theme $Theme -Parent $activeDeviceCard -Text 'Waiting...' -X 18 -Y 31 -W 174 -H 22 -Font $Theme.FontUI -Color $Theme.Ink
$btnLog = New-FrivoButton -Theme $Theme -Parent $viewSettings -Text 'View service log' -X 24 -Y 434 -W 205 -H 36
$btnFolder = New-FrivoButton -Theme $Theme -Parent $viewSettings -Text 'Open Evora folder' -X 241 -Y 434 -W 205 -H 36

function ConvertTo-EvoraTitle([string] $Text) {
    if ([string]::IsNullOrWhiteSpace($Text)) { return 'Unknown' }
    return $Text.Substring(0, 1).ToUpperInvariant() + $Text.Substring(1)
}

function Test-EvoraStartupEnabled {
    try { return [bool](Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop).Settings.Enabled } catch { return $false }
}

function Test-EvoraNetworkEnabled {
    # Frivo treats its named private-network rule as the switch for showing
    # the LAN card.  Mirror that behavior here so an already-open port is
    # immediately visible in the main launcher.
    return $null -ne (Get-NetFirewallRule -DisplayName 'Whisper transcription service' -ErrorAction SilentlyContinue | Select-Object -First 1)
}

function Test-EvoraServerUp {
    # This is the same lightweight TCP probe Frivo uses.  It keeps the
    # window responsive instead of waiting for an HTTP request every timer tick.
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect('127.0.0.1', 9000, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(400)) { return $false }
        $client.EndConnect($async)
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
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

$script:health = $null
$script:nextHealthCheck = [DateTime]::MinValue

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
    $offset = if ($PortOpen) { -92 } else { 0 }
    $modelHeading.Location = [System.Drawing.Point]::new(30, 338 + $offset)
    $activeCard.Location = [System.Drawing.Point]::new(24, 356 + $offset)
    $activeDeviceCard.Location = [System.Drawing.Point]::new(236, 356 + $offset)
    $btnLog.Location = [System.Drawing.Point]::new(24, 434 + $offset)
    $btnFolder.Location = [System.Drawing.Point]::new(241, 434 + $offset)
}

function Update-EvoraStatus {
    $taskState = 'Not registered'
    try { $taskState = (Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop).State.ToString() } catch { }
    $serverUp = ($taskState -eq 'Running') -and (Test-EvoraServerUp)
    $script:serviceRunning = ($taskState -eq 'Running')
    if ($serverUp -and [DateTime]::Now -ge $script:nextHealthCheck) {
        # Model information changes only during startup or a model change, so
        # refresh it occasionally rather than blocking every five seconds.
        try { $script:health = Invoke-RestMethod -Uri 'http://127.0.0.1:9000/health' -TimeoutSec 1 -ErrorAction Stop } catch { }
        $script:nextHealthCheck = [DateTime]::Now.AddSeconds(30)
    }
    if (-not $serverUp) { $script:health = $null; $script:nextHealthCheck = [DateTime]::MinValue }
    $health = $script:health
    $localAddress = if (Test-EvoraLocalName) { 'http://evora.local:9000' } else { 'http://localhost:9000' }
    $localUrl.Text = $localAddress
    $lanAddress = Get-EvoraLanIp
    $portOpen = Test-EvoraNetworkEnabled
    $showLan = $portOpen -and -not [string]::IsNullOrWhiteSpace($lanAddress)
    if ($showLan) { $lanUrl.Text = ('http://{0}:9000' -f $lanAddress) }
    Set-EvoraMainNetworkState $showLan
    Set-EvoraSettingsNetworkState $portOpen
    if ($serverUp) {
        $reportedHealth = $health -and $health.ok
        $modelName = if ($reportedHealth) { ConvertTo-EvoraTitle $health.model } else { 'Starting...' }
        $processing = if ($reportedHealth -and $health.device -eq 'cuda') { 'GPU (CUDA)' } elseif ($reportedHealth -and $health.device -eq 'cpu') { 'CPU' } elseif ($reportedHealth) { ConvertTo-EvoraTitle $health.device } else { 'Checking...' }
        $dot.BackColor = if ($reportedHealth) { $Theme.Signal } else { $Theme.Warn }
        $status.Text = if ($reportedHealth) { 'Running' } else { 'Starting...' }
        $modelSummary.Text = $modelName
        $deviceSummary.Text = $processing
        $activeModel.Text = $modelName
        $activeDevice.Text = $processing
        $note.Text = if ($showLan) { 'evora.local works on this PC. Other devices use the address above.' } elseif ($reportedHealth) { 'Frivo can connect at ' + $localAddress + '.' } else { 'The first model download can take several minutes.' }
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
    $keepInTray.Checked = ($script:launcherSettings.CloseAction -eq 'keep')
    $stopOnClose.Checked = ($script:launcherSettings.CloseAction -eq 'stop')
    $startupCheck.Checked = Test-EvoraStartupEnabled
    $script:loadingSettings = $false
    $viewMain.Visible = $false; $viewSettings.Visible = $true
})
$btnBack.Add_Click({ $viewSettings.Visible = $false; $viewMain.Visible = $true })
$keepInTray.Add_CheckedChanged({
    if (-not $script:loadingSettings -and $keepInTray.Checked) {
        $script:launcherSettings.CloseAction = 'keep'; Save-EvoraLauncherSettings
    }
})
$stopOnClose.Add_CheckedChanged({
    if (-not $script:loadingSettings -and $stopOnClose.Checked) {
        $script:launcherSettings.CloseAction = 'stop'; Save-EvoraLauncherSettings
    }
})
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

# The tray behavior follows Frivo: closing can hide the window while the
# private transcription service stays available, and the tray menu restores
# or stops it explicitly.
$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Text = 'Evora'
# Match Frivo's proven tray setup: use the packaged .ico directly.  The icon
# extracted from the native host can be null in this embedded PowerShell host,
# which leaves Windows with a notification but no visible tray entry.
if (Test-Path -LiteralPath $IconPath) {
    try { $notify.Icon = New-Object System.Drawing.Icon($IconPath) } catch { }
}
$trayMenu = New-Object System.Windows.Forms.ContextMenuStrip
$trayShow = $trayMenu.Items.Add('Show Evora')
[void] $trayMenu.Items.Add('-')
$trayQuit = $trayMenu.Items.Add('Stop Evora and quit')
$notify.ContextMenuStrip = $trayMenu
$notify.Visible = $true
$script:quitting = $false
$script:trayNoticeShown = $false

function Show-EvoraLauncher {
    $form.ShowInTaskbar = $true
    $form.Show()
    if ($form.WindowState -eq [System.Windows.Forms.FormWindowState]::Minimized) { $form.WindowState = [System.Windows.Forms.FormWindowState]::Normal }
    $form.BringToFront(); $form.Activate()
}

function Stop-EvoraAndQuit {
    $script:quitting = $true
    Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-STA', '-WindowStyle', 'Hidden', '-File', ('"{0}"' -f $PSCommandPath), '-Stop')
    $form.Close()
}

$trayShow.Add_Click({ Show-EvoraLauncher })
$trayQuit.Add_Click({ Stop-EvoraAndQuit })
$notify.Add_DoubleClick({ Show-EvoraLauncher })

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 1200
$timer.Add_Tick({
    Update-EvoraStatus
    if ($copy.Text -eq 'Copied') { $copy.Text = 'Copy' }
    if ($lanCopy.Text -eq 'Copied') { $lanCopy.Text = 'Copy' }
    if ($showSignal.WaitOne(0)) {
        [void] $showSignal.Reset()
        Show-EvoraLauncher
    }
})
$timer.Start()
$form.Add_FormClosing({
    param($sender, $eventArgs)
    if ($script:quitting -or $eventArgs.CloseReason -ne [System.Windows.Forms.CloseReason]::UserClosing) { return }
    # The user's close preference controls the launcher itself.  Do not make
    # it depend on a momentary service-status probe: Evora can be starting,
    # stopped, or temporarily unreachable and should still remain available
    # from the tray when "Keep Evora running" is selected.
    if ($script:launcherSettings.CloseAction -eq 'keep') {
        $eventArgs.Cancel = $true
        # Keep the same form/message loop alive, just as Frivo does.  This is
        # what keeps the notification-area icon and its menu registered.
        $form.Hide()
        if (-not $script:trayNoticeShown) {
            $script:trayNoticeShown = $true
            try { $notify.ShowBalloonTip(2500, 'Evora', 'Evora is still running. Right-click the tray icon to stop it.', [System.Windows.Forms.ToolTipIcon]::Info) } catch { }
        }
    } else {
        $script:quitting = $true
        Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-STA', '-WindowStyle', 'Hidden', '-File', ('"{0}"' -f $PSCommandPath), '-Stop')
    }
})
$form.Add_FormClosed({
    $timer.Stop(); $timer.Dispose(); $notify.Visible = $false; $notify.Dispose()
})
Update-EvoraStatus
# Frivo uses Application.Run, not ShowDialog.  Hiding a modal dialog ends its
# modal loop, which made Evora's native host exit and take the tray icon with
# it.  A normal application loop continues after the form is hidden.
[System.Windows.Forms.Application]::Run($form)
try { $instanceLock.ReleaseMutex() } catch { }
try { $instanceLock.Dispose(); $showSignal.Dispose() } catch { }
