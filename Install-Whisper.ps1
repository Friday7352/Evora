<#
  Whisper — one-click Windows installer

  Uses an isolated CPython 3.11 environment.  The former setup pointed at
  Python 3.14, for which the Windows GPU/speaker packages were not a stable
  combination.  Python 3.11 keeps faster-whisper, CTranslate2, PyTorch and
  SpeechBrain on a broadly supported set of wheels.
#>

[CmdletBinding()]
param(
    [switch] $Silent,
    [string] $InstallPath = (Join-Path $env:ProgramFiles 'Whisper')
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ($env:OS -ne 'Windows_NT' -or -not [Environment]::Is64BitOperatingSystem) {
    throw 'Whisper Setup supports 64-bit Windows 10 and Windows 11.'
}

# Installing under Program Files and opening the LAN firewall both require
# elevation. Relaunch once, keeping the wizard itself as the visible app.
$admin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $admin) {
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"{0}"' -f $PSCommandPath))
    if ($Silent) { $arguments += '-Silent' }
    if ($InstallPath) { $arguments += @('-InstallPath', ('"{0}"' -f $InstallPath)) }
    Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $arguments
    exit 0
}

$SourceDir = Split-Path -Parent $PSCommandPath
$LogPath = Join-Path ([IO.Path]::GetTempPath()) 'Whisper-Setup.log'

function Write-SetupLog([string] $Message) {
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -LiteralPath $LogPath -Value ("[{0}] {1}" -f $stamp, $Message) -Encoding utf8
}

function Invoke-Checked([string] $FilePath, [string[]] $Arguments, [string] $WorkingDirectory) {
    Write-SetupLog ('RUN {0} {1}' -f $FilePath, ($Arguments -join ' '))
    $process = Start-Process -FilePath $FilePath -ArgumentList $Arguments -WorkingDirectory $WorkingDirectory `
        -Wait -PassThru -NoNewWindow
    if ($process.ExitCode -ne 0) { throw ("{0} failed with exit code {1}." -f $FilePath, $process.ExitCode) }
}

function Find-Python311 {
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        try {
            & $py.Source -3.11 -c 'import sys; print(sys.executable)' 2>$null | Select-Object -First 1 | ForEach-Object {
                if ($_ -and (Test-Path -LiteralPath $_)) { return $_.Trim() }
            }
        } catch { }
    }
    $roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:LocalAppData) | Where-Object { $_ }
    foreach ($root in $roots) {
        $candidate = Get-ChildItem -LiteralPath $root -Filter python.exe -File -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -match 'Python311|Python3\.11' } | Select-Object -First 1
        if ($candidate) { return $candidate.FullName }
    }
    return $null
}

function Install-Python311 {
    $python = Find-Python311
    if ($python) { return $python }

    $installer = Join-Path ([IO.Path]::GetTempPath()) 'python-3.11.9-amd64.exe'
    Write-SetupLog 'Downloading Python 3.11.9 from python.org.'
    Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' `
        -OutFile $installer -UseBasicParsing
    try {
        Invoke-Checked -FilePath $installer -Arguments @(
            '/quiet', 'InstallAllUsers=1', 'PrependPath=1', 'Include_launcher=1', 'Include_test=0'
        ) -WorkingDirectory $env:TEMP
    } finally {
        Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
    }
    $python = Find-Python311
    if (-not $python) { throw 'Python 3.11 installed but could not be located. Restart Windows, then run setup again.' }
    return $python
}

function Copy-ProgramFiles([string] $Destination) {
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
    $keep = @(
        '.gitattributes', '.gitignore', 'LICENSE', 'README.md', 'requirements.txt',
        'whisper_server.py', 'StartWhisper.bat', 'install_whisper_task.ps1',
        'uninstall_whisper_task.ps1', 'Install-Whisper.ps1', 'Install-Whisper.cmd'
    )
    foreach ($name in $keep) {
        $from = Join-Path $SourceDir $name
        if (Test-Path -LiteralPath $from) {
            Copy-Item -LiteralPath $from -Destination (Join-Path $Destination $name) -Force
        }
    }
}

function Add-WhisperFirewallRule([string] $Target) {
    $python = Join-Path $Target '.venv\Scripts\python.exe'
    Get-NetFirewallRule -DisplayName 'Whisper transcription service' -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule -ErrorAction SilentlyContinue
    New-NetFirewallRule -DisplayName 'Whisper transcription service' -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort 9000 -Program $python -Profile Private | Out-Null
}

function Register-WhisperTask([string] $Target) {
    $taskName = 'WhisperTranscriptionService'
    $python = Join-Path $Target '.venv\Scripts\python.exe'
    $log = Join-Path $Target 'whisper_service.log'
    $cache = Join-Path $Target 'model_cache'
    $ecapa = Join-Path $Target 'ecapa_model'
    $prefix = "set HF_HOME=$cache&& set HUGGINGFACE_HUB_CACHE=$cache&& set TORCH_HOME=$cache&& set SPEECHBRAIN_CACHE=$ecapa&& "
    $command = "$prefix`"$python`" -u `"$Target\whisper_server.py`" >> `"$log`" 2>&1"
    $action = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c "{0}"' -f $command) -WorkingDirectory $Target
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -StartWhenAvailable -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal `
        -Settings $settings -Description 'Local Whisper transcription service for Frivo' | Out-Null
    Start-ScheduledTask -TaskName $taskName
}

function New-Shortcut([string] $Target, [string] $Link, [string] $WorkingDirectory) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Link)
    $shortcut.TargetPath = $Target
    $shortcut.WorkingDirectory = $WorkingDirectory
    $shortcut.Description = 'Start local Whisper transcription service'
    $shortcut.Save()
}

function Install-Whisper([scriptblock] $OnProgress, [bool] $AllowLan = $true) {
    & $OnProgress 5 'Preparing Whisper…'
    Copy-ProgramFiles $InstallPath
    & $OnProgress 18 'Installing the stable Python runtime…'
    $python = Install-Python311
    & $OnProgress 34 'Creating Whisper’s private Python environment…'
    $venv = Join-Path $InstallPath '.venv'
    if (Test-Path -LiteralPath $venv) { Remove-Item -LiteralPath $venv -Recurse -Force }
    Invoke-Checked -FilePath $python -Arguments @('-m', 'venv', $venv) -WorkingDirectory $InstallPath
    $venvPython = Join-Path $venv 'Scripts\python.exe'
    & $OnProgress 48 'Installing Whisper and GPU-compatible libraries…'
    Invoke-Checked -FilePath $venvPython -Arguments @('-m', 'pip', 'install', '--upgrade', 'pip') -WorkingDirectory $InstallPath
    Invoke-Checked -FilePath $venvPython -Arguments @('-m', 'pip', 'install', '--no-input', '--disable-pip-version-check', '-r', 'requirements.txt') -WorkingDirectory $InstallPath
    & $OnProgress 78 'Configuring network access…'
    if ($AllowLan) { Add-WhisperFirewallRule $InstallPath }
    & $OnProgress 88 'Registering Whisper to start automatically…'
    Register-WhisperTask $InstallPath
    $desktop = [Environment]::GetFolderPath('CommonDesktopDirectory')
    New-Shortcut -Target (Join-Path $InstallPath 'StartWhisper.bat') -Link (Join-Path $desktop 'Whisper.lnk') -WorkingDirectory $InstallPath
    & $OnProgress 100 'Whisper is ready.'
}

if ($Silent) {
    try { Install-Whisper -OnProgress { param($percent, $message) Write-Host ("{0}%  {1}" -f $percent, $message) }; exit 0 }
    catch { Write-Error $_; exit 1 }
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

# These are the same Win32 touches used by Frivo Setup: native dark window
# chrome and rounded surfaces instead of the stock Windows controls.
$script:WhisperGdiReady = $false
try {
    $gdiDefinition = @'
[DllImport("gdi32.dll")]
public static extern IntPtr CreateRoundRectRgn(int l, int t, int r, int b, int w, int h);
'@
    Add-Type -Namespace WhisperSetupNative -Name Gdi -MemberDefinition $gdiDefinition -ErrorAction Stop
    $script:WhisperGdiReady = $true
} catch { $script:WhisperGdiReady = ($null -ne ('WhisperSetupNative.Gdi' -as [type])) }

$script:WhisperDwmReady = $false
try {
    $dwmDefinition = @'
[DllImport("dwmapi.dll")]
public static extern int DwmSetWindowAttribute(IntPtr hwnd, int attribute, ref int value, int valueSize);
'@
    Add-Type -Namespace WhisperSetupNative -Name Dwm -MemberDefinition $dwmDefinition -ErrorAction Stop
    $script:WhisperDwmReady = $true
} catch { $script:WhisperDwmReady = ($null -ne ('WhisperSetupNative.Dwm' -as [type])) }

function Set-WhisperRounded($Control, [int] $Radius = 12) {
    if (-not $script:WhisperGdiReady) { return }
    $handler = {
        param($sender, $eventArgs)
        $region = [WhisperSetupNative.Gdi]::CreateRoundRectRgn(0, 0, $sender.Width + 1, $sender.Height + 1, $Radius, $Radius)
        $sender.Region = [System.Drawing.Region]::FromHrgn($region)
    }.GetNewClosure()
    $Control.Add_Resize($handler)
    & $handler $Control $null
}

function Set-WhisperWindowChrome($Window, $Theme) {
    if (-not $script:WhisperDwmReady) { return }
    $apply = {
        try {
            $dark = 1; $caption = $Theme.Bg.ToArgb(); $text = $Theme.Ink.ToArgb(); $border = $Theme.Hair.ToArgb()
            [void][WhisperSetupNative.Dwm]::DwmSetWindowAttribute($Window.Handle, 20, [ref]$dark, 4)
            [void][WhisperSetupNative.Dwm]::DwmSetWindowAttribute($Window.Handle, 35, [ref]$caption, 4)
            [void][WhisperSetupNative.Dwm]::DwmSetWindowAttribute($Window.Handle, 36, [ref]$text, 4)
            [void][WhisperSetupNative.Dwm]::DwmSetWindowAttribute($Window.Handle, 34, [ref]$border, 4)
        } catch { }
    }.GetNewClosure()
    $Window.Add_HandleCreated(({ & $apply }).GetNewClosure())
}

$theme = [pscustomobject]@{
    Bg = [Drawing.Color]::FromArgb(13,17,23); Surface = [Drawing.Color]::FromArgb(22,27,34)
    Card = [Drawing.Color]::FromArgb(28,35,45); CardHi = [Drawing.Color]::FromArgb(50,61,76)
    Hair = [Drawing.Color]::FromArgb(42,50,62); Ink = [Drawing.Color]::FromArgb(232,238,247)
    Dim = [Drawing.Color]::FromArgb(154,163,178); Accent = [Drawing.Color]::FromArgb(250,47,47)
    AccentH = [Drawing.Color]::FromArgb(255,82,82); AccentP = [Drawing.Color]::FromArgb(216,31,31)
}
$bg = $theme.Bg; $surface = $theme.Surface; $card = $theme.Card; $ink = $theme.Ink; $dim = $theme.Dim; $accent = $theme.Accent

$form = New-Object Windows.Forms.Form
$form.Text = 'Whisper Setup'
$form.ClientSize = [Drawing.Size]::new(620,440)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.BackColor = $bg
$form.Font = [Drawing.Font]::new('Segoe UI',10)
Set-WhisperWindowChrome $form $theme

function Add-Label($text,$x,$y,$w,$h,$font,$color) {
    $label = New-Object Windows.Forms.Label
    $label.Text=$text; $label.Location=[Drawing.Point]::new($x,$y); $label.Size=[Drawing.Size]::new($w,$h)
    $label.Font=$font; $label.ForeColor=$color; $label.BackColor=[Drawing.Color]::Transparent
    $form.Controls.Add($label); return $label
}
function Add-Button($text,$x,$y,$w,$primary) {
    $button = New-Object Windows.Forms.Button
    $button.Text=$text; $button.Location=[Drawing.Point]::new($x,$y); $button.Size=[Drawing.Size]::new($w,38)
    $button.FlatStyle='Flat'; $button.Font=[Drawing.Font]::new('Segoe UI Semibold',10); $button.Cursor=[Windows.Forms.Cursors]::Hand
    if($primary){
        $button.FlatAppearance.BorderSize=0; $button.BackColor=$accent; $button.ForeColor=[Drawing.Color]::White
        $button.FlatAppearance.MouseOverBackColor=$theme.AccentH; $button.FlatAppearance.MouseDownBackColor=$theme.AccentP
    } else {
        $button.FlatAppearance.BorderSize=1; $button.FlatAppearance.BorderColor=$theme.Hair; $button.BackColor=$card; $button.ForeColor=$ink
        $button.FlatAppearance.MouseOverBackColor=$theme.CardHi
    }
    Set-WhisperRounded $button 14
    $form.Controls.Add($button); return $button
}

$mark = New-Object Windows.Forms.Panel
$mark.Location=[Drawing.Point]::new(28,22); $mark.Size=[Drawing.Size]::new(36,36); $mark.BackColor=$accent
Set-WhisperRounded $mark 12
$markText = New-Object Windows.Forms.Label
$markText.Text='W'; $markText.Location=[Drawing.Point]::new(0,4); $markText.Size=[Drawing.Size]::new(36,28)
$markText.Font=[Drawing.Font]::new('Segoe UI Semibold',12); $markText.ForeColor=[Drawing.Color]::White
$markText.BackColor=[Drawing.Color]::Transparent; $markText.TextAlign='MiddleCenter'
$mark.Controls.Add($markText); $form.Controls.Add($mark)
[void](Add-Label 'Whisper Setup' 78 18 500 36 ([Drawing.Font]::new('Segoe UI Semibold',19)) $ink)
[void](Add-Label 'Private, local transcription for Frivo' 80 57 500 22 ([Drawing.Font]::new('Segoe UI',9)) $dim)
$panel = New-Object Windows.Forms.Panel
$panel.Location=[Drawing.Point]::new(24,96); $panel.Size=[Drawing.Size]::new(572,210); $panel.BackColor=$surface
Set-WhisperRounded $panel 18
$form.Controls.Add($panel)
$summary = New-Object Windows.Forms.Label
$summary.Location=[Drawing.Point]::new(20,18); $summary.Size=[Drawing.Size]::new(532,160); $summary.BackColor=$surface; $summary.ForeColor=$ink
$summary.Font=[Drawing.Font]::new('Segoe UI',10); $summary.Text = @"
This installs Whisper with its own Python 3.11 environment, so it does not
depend on the incompatible Python 3.14 setup that caused the crash.

It installs the local transcription service, prepares the optional speaker
labelling tools, opens private-network access for Frivo, and starts Whisper
automatically after Windows restarts. The first launch downloads the model.
"@
$panel.Controls.Add($summary)
$track = New-Object Windows.Forms.Panel
$track.Location=[Drawing.Point]::new(28,324); $track.Size=[Drawing.Size]::new(564,10); $track.BackColor=$card
Set-WhisperRounded $track 10; $form.Controls.Add($track)
$fill = New-Object Windows.Forms.Panel
$fill.Location=[Drawing.Point]::new(0,0); $fill.Size=[Drawing.Size]::new(0,10); $fill.BackColor=$accent
Set-WhisperRounded $fill 10; $track.Controls.Add($fill)
$script:progressBar = [pscustomobject]@{ Fill=$fill; Width=564; Value=0 }
$script:progressBar | Add-Member -MemberType ScriptMethod -Name SetValue -Value {
    param([int]$Percent)
    $this.Value=[Math]::Min(100,[Math]::Max(0,$Percent))
    $this.Fill.Size=[Drawing.Size]::new([int]($this.Width*$this.Value/100),$this.Fill.Size.Height)
}
$status = Add-Label 'Ready to install.' 28 344 564 24 ([Drawing.Font]::new('Segoe UI',9)) $dim
$install = Add-Button 'Install Whisper' 354 390 150 $true
$cancel = Add-Button 'Cancel' 516 390 80 $false
$script:completed = $false

$cancel.Add_Click({ $form.Close() })
$install.Add_Click({
    if ($script:completed) { $form.Close(); return }
    $install.Enabled=$false; $cancel.Enabled=$false
    try {
        Install-Whisper -OnProgress {
            param($percent,$message)
            # $OnProgress is the callback parameter inside Install-Whisper.
            # This separate name avoids the old self-reference crash here.
            $script:progressBar.SetValue($percent); $status.Text=$message
            [Windows.Forms.Application]::DoEvents()
        }
        $summary.Text = "Whisper is installed and starting now.`r`n`r`nIn Frivo, open Settings → Providers → Local Whisper and use:`r`nhttp://localhost:9000 (same PC) or this computer's LAN address.`r`n`r`nThe first model download may take a few minutes."
        $status.Text='Installation complete.'; $install.Text='Finish'; $script:completed=$true; $install.Enabled=$true
    } catch {
        Write-SetupLog ('FAILED: ' + $_.Exception.Message)
        $status.Text='Setup failed — see ' + $LogPath
        [Windows.Forms.MessageBox]::Show($_.Exception.Message + "`r`n`r`nDetails: $LogPath", 'Whisper Setup', [Windows.Forms.MessageBoxButtons]::OK, [Windows.Forms.MessageBoxIcon]::Error) | Out-Null
        $install.Enabled=$true; $cancel.Enabled=$true
    }
})
[void]$form.ShowDialog()
