<#
  Whisper - one-click Windows installer

  Uses an isolated CPython 3.11 environment.  The former setup pointed at
  Python 3.14, for which the Windows GPU/speaker packages were not a stable
  combination.  Python 3.11 keeps faster-whisper, CTranslate2, PyTorch and
  SpeechBrain on a broadly supported set of wheels.
#>

[CmdletBinding()]
param(
    [switch] $Silent,
    [switch] $Uninstall,
    [switch] $NoUi,
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
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-STA', '-WindowStyle', 'Hidden', '-File', ('"{0}"' -f $PSCommandPath))
    if ($Silent) { $arguments += '-Silent' }
    if ($Uninstall) { $arguments += '-Uninstall' }
    if ($NoUi) { $arguments += '-NoUi' }
    if ($InstallPath) { $arguments += @('-InstallPath', ('"{0}"' -f $InstallPath)) }
    Start-Process -FilePath 'powershell.exe' -Verb RunAs -WindowStyle Hidden -ArgumentList $arguments
    exit 0
}

$SourceDir = Split-Path -Parent $PSCommandPath
$LogPath = Join-Path ([IO.Path]::GetTempPath()) 'Whisper-Setup.log'
$script:InstallMarkerName = '.whisper-install.json'
$script:InstallMarkerId = 'com.frivo.whisper'

function Write-SetupLog([string] $Message) {
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -LiteralPath $LogPath -Value ("[{0}] {1}" -f $stamp, $Message) -Encoding utf8
}

function Get-WhisperInstallMarkerPath([string] $Path) {
    return Join-Path $Path $script:InstallMarkerName
}

function Test-WhisperInstallOwnership([string] $Path) {
    try {
        $markerPath = Get-WhisperInstallMarkerPath $Path
        if (-not (Test-Path -LiteralPath $markerPath -PathType Leaf)) { return $false }
        $marker = Get-Content -LiteralPath $markerPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        if ([string]$marker.Id -ne $script:InstallMarkerId) { return $false }
        $actual = [IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
        $recorded = [IO.Path]::GetFullPath([string]$marker.InstallPath).TrimEnd('\', '/')
        return $actual.Equals($recorded, [StringComparison]::OrdinalIgnoreCase)
    } catch { return $false }
}

function Write-WhisperInstallMarker([string] $Path) {
    $marker = [ordered]@{
        Id = $script:InstallMarkerId
        InstallPath = [IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
        CreatedUtc = [DateTime]::UtcNow.ToString('o')
    }
    [IO.File]::WriteAllText((Get-WhisperInstallMarkerPath $Path), ($marker | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
}

function Test-WhisperRecoverableResidue([string] $Path) {
    # Older setup versions did not write a marker. Only recognize their known
    # default folder and their own program files; never claim arbitrary files.
    try {
        $default = [IO.Path]::GetFullPath((Join-Path $env:ProgramFiles 'Whisper')).TrimEnd('\', '/')
        $actual = [IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
        if (-not $actual.Equals($default, [StringComparison]::OrdinalIgnoreCase)) { return $false }
        return (Test-Path -LiteralPath (Join-Path $actual 'whisper_server.py') -PathType Leaf) -or
            (Test-Path -LiteralPath (Join-Path $actual 'Install-Whisper.ps1') -PathType Leaf)
    } catch { return $false }
}

function Get-ExistingWhisperInstall([string] $Path) {
    if (Test-WhisperInstallOwnership $Path) {
        $state = if (Test-Path -LiteralPath (Join-Path $Path '.venv\Scripts\python.exe') -PathType Leaf) { 'Installed' } else { 'Partial' }
        return [pscustomobject]@{ State = $state; Path = $Path; Owned = $true }
    }
    if (Test-WhisperRecoverableResidue $Path) {
        return [pscustomobject]@{ State = 'Partial'; Path = $Path; Owned = $false }
    }
    return [pscustomobject]@{ State = 'None'; Path = $Path; Owned = $false }
}

${script:WhisperSetupPulse} = {}
function Set-WhisperSetupPulse([scriptblock] $Pulse) { $script:WhisperSetupPulse = if ($Pulse) { $Pulse } else { {} } }

function Invoke-Checked([string] $FilePath, [string[]] $Arguments, [string] $WorkingDirectory) {
    if ([string]::IsNullOrWhiteSpace($FilePath) -or -not (Test-Path -LiteralPath $FilePath -PathType Leaf)) {
        throw ('Setup could not find the required program: {0}' -f $FilePath)
    }
    if (-not (Test-Path -LiteralPath $WorkingDirectory -PathType Container)) {
        throw ('Setup could not find its working folder: {0}' -f $WorkingDirectory)
    }
    Write-SetupLog ('RUN {0} {1}' -f $FilePath, ($Arguments -join ' '))
    $quotedArguments = @($Arguments | ForEach-Object { '"' + $_.Replace('"', '\"') + '"' }) -join ' '
    $info = New-Object System.Diagnostics.ProcessStartInfo
    $info.FileName = $FilePath
    $info.Arguments = $quotedArguments
    $info.WorkingDirectory = $WorkingDirectory
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $process = [System.Diagnostics.Process]::Start($info)
    while (-not $process.HasExited) {
        & $script:WhisperSetupPulse
        Start-Sleep -Milliseconds 300
    }
    $exitCode = $process.ExitCode
    if ($exitCode -ne 0) { throw ("{0} failed with exit code {1}." -f $FilePath, $exitCode) }
}

function Find-Python311 {
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        try {
            $reported = @(& $py.Source -3.11 -c 'import sys; print(sys.executable)' 2>$null | Select-Object -First 1)
            if ($reported.Count -eq 1) {
                $candidate = $reported[0].ToString().Trim()
                if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) { return $candidate }
            }
        } catch { }
    }
    $locations = @(
        (Join-Path $env:ProgramFiles 'Python311\python.exe'),
        (Join-Path $env:LocalAppData 'Programs\Python\Python311\python.exe')
    )
    if (${env:ProgramFiles(x86)}) { $locations += Join-Path ${env:ProgramFiles(x86)} 'Python311\python.exe' }
    foreach ($candidate in $locations) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
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
        '.gitattributes', '.gitignore', 'LICENSE', 'README.md', 'THIRD_PARTY_NOTICES.txt', 'requirements.txt',
        'whisper_server.py', 'StartWhisper.bat', 'install_whisper_task.ps1',
        'uninstall_whisper_task.ps1', 'Install-Whisper.ps1', 'Install-Whisper.cmd', 'Install-Whisper.vbs',
        'Uninstall-Whisper.vbs', 'Whisper-Launcher.ps1', 'Whisper-Launcher.vbs', 'Evora-Launcher.ps1', 'EvoraHost.exe', 'EvoraSetupHost.exe', 'Whisper.Ui.psm1', 'Evora-Setup.ps1', 'Evora-Setup.vbs', 'Evora.png', 'Evora.ico'
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
        -Protocol TCP -LocalPort 9000 -Program $python -Profile Private -RemoteAddress LocalSubnet | Out-Null
}

function Get-EvoraHostsPath {
    return (Join-Path $env:SystemRoot 'System32\drivers\etc\hosts')
}

function Add-EvoraHostsEntry {
    $hosts = Get-EvoraHostsPath
    $lines = if (Test-Path -LiteralPath $hosts) { @([IO.File]::ReadAllLines($hosts)) } else { @() }
    if ($lines | Where-Object { $_ -match '(?i)^\s*[0-9a-fA-F:\.]+\s+.*\bevora\.local\b' }) { return }
    $entry = "127.0.0.1`tevora.local`t# Evora - added by setup, removed on uninstall"
    [IO.File]::WriteAllLines($hosts, [string[]]($lines + $entry))
}

function Remove-EvoraHostsEntry {
    $hosts = Get-EvoraHostsPath
    if (-not (Test-Path -LiteralPath $hosts)) { return }
    $lines = @([IO.File]::ReadAllLines($hosts))
    $kept = @($lines | Where-Object { $_ -notmatch '(?i)^\s*127\.0\.0\.1\s+evora\.local\s+# Evora - added by setup, removed on uninstall\s*$' })
    if ($kept.Count -ne $lines.Count) { [IO.File]::WriteAllLines($hosts, [string[]]$kept) }
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

function Register-WhisperInstalledApp([string] $Target) {
    $key = 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Whisper'
    $uninstaller = Join-Path $Target 'Uninstall-Whisper.vbs'
    New-Item -Path $key -Force | Out-Null
    New-ItemProperty -Path $key -Name 'DisplayName' -Value 'Evora' -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $key -Name 'DisplayVersion' -Value '1.0' -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $key -Name 'Publisher' -Value 'Friday' -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $key -Name 'InstallLocation' -Value $Target -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $key -Name 'UninstallString' -Value ('wscript.exe "{0}"' -f $uninstaller) -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $key -Name 'DisplayIcon' -Value ((Join-Path $Target 'Evora.ico') + ',0') -PropertyType String -Force | Out-Null
    New-ItemProperty -Path $key -Name 'NoModify' -Value 1 -PropertyType DWord -Force | Out-Null
}

function Remove-WhisperInstalledApp {
    Remove-Item -LiteralPath 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Whisper' -Recurse -Force -ErrorAction SilentlyContinue
}

function Remove-WhisperRuntime([string] $Target) {
    # Used by repair/reinstall. Models are deliberately retained, while the
    # Python environment and machine-wide service registration are rebuilt.
    Stop-ScheduledTask -TaskName 'WhisperTranscriptionService' -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName 'WhisperTranscriptionService' -Confirm:$false -ErrorAction SilentlyContinue
    Get-NetFirewallRule -DisplayName 'Whisper transcription service' -ErrorAction SilentlyContinue |
        Remove-NetFirewallRule -ErrorAction SilentlyContinue
    $venv = Join-Path $Target '.venv'
    if (Test-Path -LiteralPath $venv -PathType Container) {
        Remove-Item -LiteralPath $venv -Recurse -Force -ErrorAction Stop
    }
}

function Uninstall-Whisper([string] $Target) {
    $existing = Get-ExistingWhisperInstall $Target
    if ($existing.State -eq 'None') {
        throw 'Setup will only uninstall a Whisper folder that it previously created.'
    }
    $source = [IO.Path]::GetFullPath($SourceDir).TrimEnd('\', '/')
    $installed = [IO.Path]::GetFullPath($Target).TrimEnd('\', '/')
    if ($source.Equals($installed, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Setup is running from the folder being uninstalled. Run the downloaded installer from a different folder, then try again.'
    }
    Write-SetupLog ('Uninstalling Whisper from ' + $installed)
    # Close Evora's visible launcher before removing its files. Match the
    # installed path as well as the launcher name, so unrelated PowerShell
    # sessions are never touched.
    Get-CimInstance Win32_Process -Filter "Name = 'powershell.exe'" -ErrorAction SilentlyContinue |
        Where-Object {
            $_.CommandLine -and $_.CommandLine -like ('*' + $installed + '*') -and
            ($_.CommandLine -like '*Evora-Launcher.ps1*' -or $_.CommandLine -like '*Whisper-Launcher.ps1*')
        } |
        ForEach-Object { Invoke-CimMethod -InputObject $_ -MethodName Terminate -ErrorAction SilentlyContinue | Out-Null }
    Start-Sleep -Milliseconds 400
    # The branded host waits for the launcher process. End that short-lived
    # parent too so Windows releases EvoraHost.exe before the folder is
    # removed.
    Get-CimInstance Win32_Process -Filter "Name = 'EvoraHost.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -and $_.ExecutablePath.Equals((Join-Path $installed 'EvoraHost.exe'), [StringComparison]::OrdinalIgnoreCase) } |
        ForEach-Object { Invoke-CimMethod -InputObject $_ -MethodName Terminate -ErrorAction SilentlyContinue | Out-Null }
    Remove-WhisperRuntime $Target
    Remove-EvoraHostsEntry
    Remove-WhisperInstalledApp
    Remove-Item -LiteralPath (Join-Path ([Environment]::GetFolderPath('CommonDesktopDirectory')) 'Evora.lnk') -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $installed -Recurse -Force -ErrorAction Stop
    Write-SetupLog 'Whisper uninstall completed.'
}

function New-Shortcut([string] $Target, [string] $Link, [string] $WorkingDirectory, [string] $IconPath, [string] $Arguments) {
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($Link)
    $shortcut.TargetPath = $Target
    $shortcut.WorkingDirectory = $WorkingDirectory
    $shortcut.Description = 'Open Whisper service status and controls'
    if ($Arguments) { $shortcut.Arguments = $Arguments }
    if ($IconPath -and (Test-Path -LiteralPath $IconPath -PathType Leaf)) { $shortcut.IconLocation = ('{0},0' -f $IconPath) }
    $shortcut.Save()
}

function Install-Whisper([string] $Target, [scriptblock] $OnProgress, [bool] $AllowLan = $true, [bool] $CreateDesktopShortcut = $true, [bool] $StartWithWindows = $true, [switch] $Repair) {
    & $OnProgress 5 'Preparing Evora...'
    if ($Repair) {
        & $OnProgress 10 'Preparing the previous Evora installation...'
        Remove-WhisperRuntime $Target
    }
    Copy-ProgramFiles $Target
    Write-WhisperInstallMarker $Target
    & $OnProgress 12 'Setting up the evora.local address...'
    try { Add-EvoraHostsEntry } catch { Write-SetupLog ('Could not add evora.local: ' + $_.Exception.Message) }
    & $OnProgress 18 'Installing the stable Python runtime...'
    $python = Install-Python311
    & $OnProgress 34 'Creating Evora private Python environment...'
    $venv = Join-Path $Target '.venv'
    if (Test-Path -LiteralPath $venv) { Remove-Item -LiteralPath $venv -Recurse -Force }
    Invoke-Checked -FilePath $python -Arguments @('-m', 'venv', $venv) -WorkingDirectory $Target
    $venvPython = Join-Path $venv 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        throw ('Python completed but did not create the expected private environment at: {0}' -f $venvPython)
    }
    & $OnProgress 48 'Installing Evora and GPU-compatible libraries. This can take several minutes...'
    Invoke-Checked -FilePath $venvPython -Arguments @('-m', 'pip', 'install', '--upgrade', 'pip') -WorkingDirectory $Target
    Invoke-Checked -FilePath $venvPython -Arguments @('-m', 'pip', 'install', '--no-input', '--disable-pip-version-check', '-r', 'requirements.txt') -WorkingDirectory $Target
    & $OnProgress 78 'Configuring network access...'
    if ($AllowLan) { Add-WhisperFirewallRule $Target }
    & $OnProgress 88 'Finishing Evora setup...'
    if ($StartWithWindows) { Register-WhisperTask $Target }
    $desktop = [Environment]::GetFolderPath('CommonDesktopDirectory')
    Register-WhisperInstalledApp $Target
    if ($CreateDesktopShortcut) {
        New-Shortcut -Target (Join-Path $Target 'EvoraHost.exe') -Link (Join-Path $desktop 'Evora.lnk') -WorkingDirectory $Target -IconPath (Join-Path $Target 'Evora.ico') -Arguments ('--script "{0}"' -f (Join-Path $Target 'Evora-Launcher.ps1'))
    }
    & $OnProgress 100 'Evora is ready.'
}

if ($Uninstall) {
    try { Uninstall-Whisper $InstallPath; exit 0 }
    catch { Write-Error $_; exit 1 }
}

if ($Silent) {
    try { Install-Whisper -Target $InstallPath -OnProgress { param($percent, $message) Write-Host ("{0}%  {1}" -f $percent, $message) }; exit 0 }
    catch { Write-Error $_; exit 1 }
}

if ($NoUi) { return }

$script:ExistingInstall = Get-ExistingWhisperInstall $InstallPath

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
    Dim = [Drawing.Color]::FromArgb(154,163,178); Accent = [Drawing.Color]::FromArgb(169,78,255)
    AccentH = [Drawing.Color]::FromArgb(198,126,255); AccentP = [Drawing.Color]::FromArgb(124,48,201)
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
$setupTitle = Add-Label 'Welcome to Whisper Setup' 78 18 500 36 ([Drawing.Font]::new('Segoe UI Semibold',19)) $ink
$setupSubtitle = Add-Label 'Private, local transcription for Frivo' 80 57 500 22 ([Drawing.Font]::new('Segoe UI',9)) $dim
$welcomePanel = New-Object Windows.Forms.Panel
$welcomePanel.Location=[Drawing.Point]::new(24,96); $welcomePanel.Size=[Drawing.Size]::new(572,210); $welcomePanel.BackColor=$surface
Set-WhisperRounded $welcomePanel 18; $form.Controls.Add($welcomePanel)
$welcomeText = New-Object Windows.Forms.Label
$welcomeText.Location=[Drawing.Point]::new(20,18); $welcomeText.Size=[Drawing.Size]::new(532,160)
$welcomeText.BackColor=$surface; $welcomeText.ForeColor=$ink; $welcomeText.Font=[Drawing.Font]::new('Segoe UI',10)
$welcomeText.Text = @"
This wizard installs Whisper, a private transcription service that lets Frivo
turn speech into text on your own computer.

Setup creates a separate, stable Python environment, starts the local service
automatically after Windows restarts, and can optionally allow Frivo on your
private network to connect. The first startup downloads the Whisper model.

Click Next to choose an installation action.
"@
$welcomePanel.Controls.Add($welcomeText)
$panel = New-Object Windows.Forms.Panel
$panel.Location=[Drawing.Point]::new(24,96); $panel.Size=[Drawing.Size]::new(572,210); $panel.BackColor=$surface
Set-WhisperRounded $panel 18
$panel.Visible=$false
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
$repairOption = $null
$uninstallOption = $null
if ($script:ExistingInstall.State -ne 'None') {
    $summary.Font=[Drawing.Font]::new('Segoe UI',9)
    if ($script:ExistingInstall.State -eq 'Installed') {
        $summary.Text = "Whisper is already installed at:`r`n$($script:ExistingInstall.Path)`r`n`r`nChoose what you would like to do."
    } else {
        $summary.Text = "A previous Whisper installation did not complete at:`r`n$($script:ExistingInstall.Path)`r`n`r`nSetup can repair it or remove the incomplete files."
    }
    $repairOption = New-Object Windows.Forms.RadioButton
    $repairOption.Location=[Drawing.Point]::new(20,82); $repairOption.Size=[Drawing.Size]::new(532,22)
    $repairOption.Text=if($script:ExistingInstall.State -eq 'Installed'){'Repair or reinstall'}else{'Clean up and install'}
    $repairOption.Checked=$true; $repairOption.ForeColor=$ink; $repairOption.BackColor=$surface
    $repairOption.Font=[Drawing.Font]::new('Segoe UI',9)
    $panel.Controls.Add($repairOption)
    $repairNote = New-Object Windows.Forms.Label
    $repairNote.Location=[Drawing.Point]::new(40,105); $repairNote.Size=[Drawing.Size]::new(505,25)
    $repairNote.Text='Rebuilds the Python environment and service. Downloaded models are kept.'
    $repairNote.ForeColor=$dim; $repairNote.BackColor=$surface; $repairNote.Font=[Drawing.Font]::new('Segoe UI',8.5)
    $panel.Controls.Add($repairNote)
    $uninstallOption = New-Object Windows.Forms.RadioButton
    $uninstallOption.Location=[Drawing.Point]::new(20,140); $uninstallOption.Size=[Drawing.Size]::new(532,22)
    $uninstallOption.Text='Uninstall Whisper'; $uninstallOption.ForeColor=$ink; $uninstallOption.BackColor=$surface
    $uninstallOption.Font=[Drawing.Font]::new('Segoe UI',9)
    $panel.Controls.Add($uninstallOption)
    $uninstallNote = New-Object Windows.Forms.Label
    $uninstallNote.Location=[Drawing.Point]::new(40,163); $uninstallNote.Size=[Drawing.Size]::new(505,25)
    $uninstallNote.Text='Removes the Whisper program, virtual environment, downloaded models, and service.'
    $uninstallNote.ForeColor=$dim; $uninstallNote.BackColor=$surface; $uninstallNote.Font=[Drawing.Font]::new('Segoe UI',8.5)
    $panel.Controls.Add($uninstallNote)
}
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
$back = Add-Button 'Back' 246 390 96 $false
$back.Visible=$false
$install = Add-Button 'Next' 354 390 150 $true
$cancel = Add-Button 'Cancel' 516 390 80 $false
$script:completed = $false
$script:showingWelcome = $true

$cancel.Add_Click({ $form.Close() })
$back.Add_Click({
    $panel.Visible=$false; $welcomePanel.Visible=$true
    $setupTitle.Text='Welcome to Whisper Setup'
    $setupSubtitle.Text='Private, local transcription for Frivo'
    $back.Visible=$false; $install.Text='Next'; $script:showingWelcome=$true
})
$install.Add_Click({
    if ($script:completed) { $form.Close(); return }
    if ($script:showingWelcome) {
        $welcomePanel.Visible=$false; $panel.Visible=$true
        $setupTitle.Text = if($script:ExistingInstall.State -eq 'None'){'Install Whisper'}else{'Existing Whisper installation'}
        $setupSubtitle.Text = if($script:ExistingInstall.State -eq 'None'){'Ready to install the local transcription service'}else{'Repair, reinstall, or remove Whisper'}
        $back.Visible=$true; $install.Text=if($script:ExistingInstall.State -eq 'None'){'Install Whisper'}else{'Continue'}
        $script:showingWelcome=$false
        return
    }
    $install.Enabled=$false; $cancel.Enabled=$false
    try {
        if ($script:ExistingInstall.State -ne 'None' -and $uninstallOption.Checked) {
            $status.Text = 'Removing Whisper...'
            [System.Windows.Forms.Application]::DoEvents()
            Uninstall-Whisper $script:ExistingInstall.Path
            $summary.Text = 'Whisper has been removed from this computer.'
            if ($repairOption) { $repairOption.Visible=$false; $uninstallOption.Visible=$false; $repairNote.Visible=$false; $uninstallNote.Visible=$false }
            $status.Text='Uninstall complete.'; $install.Text='Close'; $script:completed=$true; $install.Enabled=$true
            return
        }
        $repair = ($script:ExistingInstall.State -ne 'None' -and $repairOption.Checked)
        Install-Whisper -Target $InstallPath -Repair:$repair -OnProgress {
            param($percent,$message)
            # $OnProgress is the callback parameter inside Install-Whisper.
            # This separate name avoids the old self-reference crash here.
            $script:progressBar.SetValue($percent); $status.Text=$message
            [Windows.Forms.Application]::DoEvents()
        }
        $summary.Text = "Whisper is installed and starting now.`r`n`r`nIn Frivo, open Settings > Providers > Local Whisper and use:`r`nhttp://localhost:9000 (same PC) or this computer's LAN address.`r`n`r`nThe first model download may take a few minutes."
        $status.Text='Installation complete.'; $install.Text='Finish'; $script:completed=$true; $install.Enabled=$true
    } catch {
        Write-SetupLog ('FAILED: ' + $_.Exception.Message)
        $status.Text='Setup failed - see ' + $LogPath
        [Windows.Forms.MessageBox]::Show($_.Exception.Message + "`r`n`r`nDetails: $LogPath", 'Whisper Setup', [Windows.Forms.MessageBoxButtons]::OK, [Windows.Forms.MessageBoxIcon]::Error) | Out-Null
        $install.Enabled=$true; $cancel.Enabled=$true
    }
})
[void]$form.ShowDialog()
