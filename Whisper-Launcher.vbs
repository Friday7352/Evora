Option Explicit

' Opens the Evora status window without a console window.
Dim shell, fso, folder, host, args
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
host = folder & "\EvoraHost.exe"
If fso.FileExists(host) Then
    shell.Run """" & host & """ --script """" & folder & "\Evora-Launcher.ps1"""", 0, False
Else
    args = "-NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File """ & folder & "\Evora-Launcher.ps1"""
    shell.Run "powershell.exe " & args, 0, False
End If
