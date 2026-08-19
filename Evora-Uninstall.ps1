<#
    Evora — uninstaller
    ------------------------------------------------------------------
    The same confirmation, live-removal log, and completion flow used by
    Frivo.  It removes only registrations and shortcuts that belong to this
    exact Evora installation.
#>

[CmdletBinding()]
param([switch] $Silent)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Continue'

$Root = Split-Path -Parent $PSCommandPath
$AppName = 'Evora'
$LogPath = Join-Path ([IO.Path]::GetTempPath()) 'Evora-Uninstall.log'
$MarkerPath = Join-Path $Root '.whisper-install.json'
$RuleName = 'Whisper transcription service'
$TaskName = 'WhisperTranscriptionService'
$RegistryKey = 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Whisper'
$script:Steps = New-Object System.Collections.ArrayList
$script:Problems = New-Object System.Collections.ArrayList
$script:StepSink = $null

function Write-UninstallLog([string] $Text) {
    Add-Content -LiteralPath $LogPath -Value ('[{0}] {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Text) -Encoding utf8
}

function Add-Step([string] $Text, [bool] $Ok = $true) {
    [void] $script:Steps.Add([pscustomobject]@{ Text = $Text; Ok = $Ok })
    Write-UninstallLog ('{0}{1}' -f $Text, $(if ($Ok) { '' } else { ' [FAILED]' }))
    if (-not $Ok) { [void] $script:Problems.Add($Text) }
    if ($script:StepSink) { & $script:StepSink $Text $Ok }
}

function Test-EvoraAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    return (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-EvoraInstallOwnership {
    try {
        if (-not (Test-Path -LiteralPath $MarkerPath -PathType Leaf)) { return $false }
        $marker = Get-Content -LiteralPath $MarkerPath -Raw | ConvertFrom-Json
        if ([string] $marker.Id -ne 'com.frivo.whisper') { return $false }
        return [IO.Path]::GetFullPath([string] $marker.InstallPath).TrimEnd('\', '/').Equals(
            [IO.Path]::GetFullPath($Root).TrimEnd('\', '/'), [StringComparison]::OrdinalIgnoreCase)
    } catch { return $false }
}

function Test-EvoraRegistryOwnership {
    try {
        $location = [string] (Get-ItemProperty -LiteralPath $RegistryKey -ErrorAction Stop).InstallLocation
        return $location -and [IO.Path]::GetFullPath($location).TrimEnd('\', '/').Equals(
            [IO.Path]::GetFullPath($Root).TrimEnd('\', '/'), [StringComparison]::OrdinalIgnoreCase)
    } catch { return $false }
}

function Test-EvoraLegacyOwnership {
    # Older Evora releases did not always leave the install marker behind.
    # Only recognize the two historical default folders and Evora's own
    # service files; never treat an arbitrary folder as safe to remove.
    try {
        $knownPaths = @(
            (Join-Path $env:ProgramFiles 'Evora'),
            (Join-Path $env:ProgramFiles 'Whisper')
        )
        $normalizedRoot = [IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
        $known = $knownPaths | Where-Object {
            [IO.Path]::GetFullPath($_).TrimEnd('\', '/').Equals($normalizedRoot, [StringComparison]::OrdinalIgnoreCase)
        }
        return $known -and (Test-Path -LiteralPath (Join-Path $Root 'whisper_server.py') -PathType Leaf) -and
            ((Test-Path -LiteralPath (Join-Path $Root 'EvoraHost.exe') -PathType Leaf) -or (Test-Path -LiteralPath (Join-Path $Root 'Install-Whisper.ps1') -PathType Leaf))
    } catch { return $false }
}

function Remove-EvoraHostsEntry {
    $hosts = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'
    if (-not (Test-Path -LiteralPath $hosts)) { return $false }
    $lines = @([IO.File]::ReadAllLines($hosts))
    $kept = @($lines | Where-Object { $_ -notmatch '(?i)^\s*127\.0\.0\.1\s+evora\.local\s+# Evora - added by setup, removed on uninstall\s*$' })
    if ($kept.Count -eq $lines.Count) { return $false }
    [IO.File]::WriteAllLines($hosts, [string[]] $kept)
    return $true
}

function Test-EvoraShortcut([string] $Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    try {
        $link = (New-Object -ComObject WScript.Shell).CreateShortcut($Path)
        return ([string] $link.TargetPath).IndexOf($Root, [StringComparison]::OrdinalIgnoreCase) -ge 0 -or
            ([string] $link.Arguments).IndexOf($Root, [StringComparison]::OrdinalIgnoreCase) -ge 0
    } catch { return $false }
}

function Preserve-EvoraReinstallCache {
    $cache = Join-Path $env:ProgramData 'Evora\reinstall-cache'
    New-Item -ItemType Directory -Path $cache -Force | Out-Null
    $preserved = 0
    foreach ($name in @('.venv', 'model_cache', 'ecapa_model')) {
        $source = Join-Path $Root $name
        $destination = Join-Path $cache $name
        if (-not (Test-Path -LiteralPath $source)) { continue }
        if (Test-Path -LiteralPath $destination) { Remove-Item -LiteralPath $destination -Recurse -Force -ErrorAction Stop }
        Move-Item -LiteralPath $source -Destination $destination -Force -ErrorAction Stop
        $preserved++
    }
    return $preserved
}

function Invoke-EvoraRemoval {
    param([bool] $KeepDownloaded = $false)
    if (-not ((Test-EvoraInstallOwnership) -or (Test-EvoraRegistryOwnership) -or (Test-EvoraLegacyOwnership))) {
        Add-Step 'Refused to remove an unverified Evora folder' $false
        return
    }

    # Close Evora's visible launcher before files are touched.  This includes
    # the native host used by current releases and the older PowerShell host.
    try {
        $closed = 0
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            ($_.Name -ieq 'EvoraHost.exe' -and $_.ExecutablePath -and $_.ExecutablePath.Equals((Join-Path $Root 'EvoraHost.exe'), [StringComparison]::OrdinalIgnoreCase)) -or
            ($_.Name -ieq 'powershell.exe' -and $_.CommandLine -and $_.CommandLine.IndexOf($Root, [StringComparison]::OrdinalIgnoreCase) -ge 0 -and $_.CommandLine -match '(?i)(Evora|Whisper)-Launcher\.ps1')
        } | ForEach-Object {
            try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop; $closed++ } catch { }
        }
        if ($closed) { Add-Step ('Closed {0} Evora window(s)' -f $closed); Start-Sleep -Milliseconds 800 }
    } catch { Add-Step 'Close the Evora launcher' $false }

    # Stop and remove Evora's machine service only when its task command
    # points at this installation.
    try {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        $ownedTask = $task -and (($task.Actions | Out-String) -match [regex]::Escape($Root))
        if ($ownedTask) {
            Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
            Add-Step 'Stopped and removed the Evora background service'
        }
    } catch { Add-Step 'Remove the Evora background service' $false }

    try {
        $stopped = 0
        Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'" -ErrorAction SilentlyContinue | Where-Object {
            $_.ExecutablePath -and $_.ExecutablePath.StartsWith((Join-Path $Root '.venv'), [StringComparison]::OrdinalIgnoreCase)
        } | ForEach-Object {
            try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop; $stopped++ } catch { }
        }
        if ($stopped) { Add-Step ('Stopped {0} Evora service process(es)' -f $stopped); Start-Sleep -Milliseconds 800 }
    } catch { Add-Step 'Stop running Evora service processes' $false }

    try {
        $python = Join-Path $Root '.venv\Scripts\python.exe'
        Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue | ForEach-Object {
            $rule = $_
            $application = Get-NetFirewallApplicationFilter -AssociatedNetFirewallRule $rule -ErrorAction SilentlyContinue
            if ($application -and $application.Program -and $application.Program.Equals($python, [StringComparison]::OrdinalIgnoreCase)) {
                $rule | Remove-NetFirewallRule -ErrorAction Stop
                Add-Step 'Removed Evora private-network access'
            }
        }
    } catch { Add-Step 'Remove Evora private-network access' $false }

    try { if (Remove-EvoraHostsEntry) { Add-Step 'Removed the evora.local address' } }
    catch { Add-Step 'Remove the evora.local address' $false }

    foreach ($desktop in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('CommonDesktopDirectory'))) {
        if (-not $desktop) { continue }
        $shortcut = Join-Path $desktop 'Evora.lnk'
        if (Test-EvoraShortcut $shortcut) {
            try { Remove-Item -LiteralPath $shortcut -Force -ErrorAction Stop; Add-Step 'Removed the Evora desktop shortcut' }
            catch { Add-Step 'Remove the Evora desktop shortcut' $false }
        }
    }

    try {
        if (Test-EvoraRegistryOwnership) {
            Remove-Item -LiteralPath $RegistryKey -Recurse -Force -ErrorAction Stop
            Add-Step 'Removed the Apps & features entry'
        }
    } catch { Add-Step 'Remove the Apps & features entry' $false }

    if ($KeepDownloaded) {
        try {
            $preserved = Preserve-EvoraReinstallCache
            if ($preserved) { Add-Step 'Kept downloaded models and GPU libraries for a faster reinstall' }
        } catch { Add-Step 'Keep downloaded models and GPU libraries' $false }
    }

    # The running uninstaller itself holds handles on this folder.  The small
    # helper waits for this exact native-host process to exit, then removes all
    # program files, including models and virtual environment.
    try {
        $escapedRoot = $Root.Replace("'", "''")
        $cleanup = "Wait-Process -Id $PID; Remove-Item -LiteralPath '$escapedRoot' -Recurse -Force -ErrorAction SilentlyContinue"
        Start-Process -FilePath 'powershell.exe' -WindowStyle Hidden -WorkingDirectory ([Environment]::GetFolderPath('Windows')) -ArgumentList @('-NoProfile', '-WindowStyle', 'Hidden', '-Command', $cleanup) | Out-Null
        Add-Step 'Finalizing Evora program files and downloaded models'
    } catch { Add-Step 'Finalize Evora program files' $false }
}

if (-not (Test-EvoraAdministrator)) {
    $setupHost = Join-Path $Root 'EvoraSetupHost.exe'
    try {
        Start-Process -FilePath $setupHost -Verb RunAs -WindowStyle Hidden -ArgumentList @('--script', ('"{0}"' -f $PSCommandPath)) | Out-Null
    } catch {
        Add-Type -AssemblyName System.Windows.Forms
        [System.Windows.Forms.MessageBox]::Show('Administrator privileges are required to uninstall Evora.', 'Uninstall Evora', [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Warning) | Out-Null
    }
    exit
}

Write-UninstallLog ('Evora uninstall started: ' + $Root)
if ($Silent) {
    Invoke-EvoraRemoval
    exit $(if ($script:Problems.Count) { 1 } else { 0 })
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
Import-Module (Join-Path $Root 'Whisper.Ui.psm1') -Force
$Theme = Get-FrivoTheme
$form = New-FrivoForm -Theme $Theme -Title 'Uninstall Evora' -Width 500 -Height 430 -IconPath (Join-Path $Root 'EvoraIcon.ico')
$header = New-FrivoHeader -Theme $Theme -Form $form -Title 'Uninstall Evora' -Subtitle $Root -LogoPngPath (Join-Path $Root 'Evora.png')

$confirm = New-Object System.Windows.Forms.Panel
$confirm.Location = [Drawing.Point]::new(0, 76); $confirm.Size = [Drawing.Size]::new(500, 354); $confirm.BackColor = $Theme.Bg
$form.Controls.Add($confirm)
[void](New-FrivoLabel -Theme $Theme -Parent $confirm -Text 'This removes Evora, its background service, private-network access, and shortcuts.' -X 28 -Y 10 -W 444 -H 48 -Font $Theme.FontUI -Color $Theme.Dim)
$removeCard = New-FrivoCard -Theme $Theme -Parent $confirm -X 24 -Y 72 -W 452 -H 94
[void](New-FrivoLabel -Theme $Theme -Parent $removeCard -Text 'Evora will be removed from this computer.' -X 18 -Y 16 -W 416 -H 20 -Font $Theme.FontMid -Color $Theme.Ink)
[void](New-FrivoLabel -Theme $Theme -Parent $removeCard -Text 'This does not change Frivo or your Windows Python installation.' -X 18 -Y 38 -W 416 -H 18 -Font $Theme.FontSmall -Color $Theme.Dim)
$keepCache = New-FrivoCheck -Theme $Theme -Parent $removeCard -Text 'Keep downloaded models and GPU libraries for a faster reinstall' -X 18 -Y 62 -W 416 -Checked $true
$removeButton = New-FrivoButton -Theme $Theme -Parent $confirm -Text 'Uninstall Evora' -X 24 -Y 250 -W 452 -H 44 -Primary $true
$cancelButton = New-FrivoButton -Theme $Theme -Parent $confirm -Text 'Cancel' -X 24 -Y 302 -W 452 -H 38

$run = New-Object System.Windows.Forms.Panel
$run.Location = [Drawing.Point]::new(0, 76); $run.Size = [Drawing.Size]::new(500, 354); $run.BackColor = $Theme.Bg; $run.Visible = $false
$form.Controls.Add($run)
$runLog = New-FrivoTextBox -Theme $Theme -Parent $run -X 24 -Y 8 -W 452 -H 230 -Multiline
$summary = New-FrivoLabel -Theme $Theme -Parent $run -Text '' -X 28 -Y 246 -W 444 -H 48 -Font $Theme.FontUI -Color $Theme.Ink
$closeButton = New-FrivoButton -Theme $Theme -Parent $run -Text 'Close' -X 24 -Y 302 -W 452 -H 38 -Primary $true
$closeButton.Enabled = $false

$cancelButton.Add_Click({ $form.Close() })
$removeButton.Add_Click({
    $confirm.Visible = $false; $run.Visible = $true; $header.Subtitle.Text = 'Removing...'
    [System.Windows.Forms.Application]::DoEvents()
    $script:StepSink = {
        param($Text, $Ok)
        $runLog.AppendText((if ($Ok) { '  ' } else { '! ' }) + $Text + "`r`n")
        $runLog.SelectionStart = $runLog.TextLength; $runLog.ScrollToCaret()
        [System.Windows.Forms.Application]::DoEvents()
    }
    Invoke-EvoraRemoval -KeepDownloaded $keepCache.Checked
    if ($runLog.TextLength -eq 0) {
        # If Windows delayed a live UI repaint, still show the completed
        # removal record before the summary rather than an empty panel.
        foreach ($step in $script:Steps) { $runLog.AppendText((if ($step.Ok) { '  ' } else { '! ' }) + $step.Text + "`r`n") }
    }
    if ($script:Problems.Count) {
        $header.Subtitle.Text = 'Finished, with problems'; $summary.ForeColor = $Theme.Warn
        $summary.Text = ('Some steps did not complete. Details were saved to:' + "`r`n" + $LogPath)
    } else {
        $header.Subtitle.Text = 'Finished'
        $summary.Text = if ($keepCache.Checked) { 'Evora has been uninstalled. Downloaded models and GPU libraries were kept for a faster reinstall.' } else { 'Evora has been uninstalled. You can now close this window.' }
    }
    $closeButton.Enabled = $true
})
$closeButton.Add_Click({ $form.Close() })
[void]$form.ShowDialog()
