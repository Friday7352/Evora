Option Explicit

' Silent, double-click entry point for Evora Setup. PowerShell handles the
' UAC prompt and opens the installer window; WScript prevents a console
' window from appearing behind it.
Dim shell, fileSystem, folder, setupScript, host, arguments
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")
folder = fileSystem.GetParentFolderName(WScript.ScriptFullName)
setupScript = folder & "\Evora-Setup.ps1"
host = folder & "\EvoraSetupHost.exe"
If fileSystem.FileExists(host) Then
    shell.Run """" & host & """ --script """" & setupScript & """", 0, False
Else
    arguments = "-NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File """ & setupScript & """"
    shell.Run "powershell.exe " & arguments, 0, False
End If
