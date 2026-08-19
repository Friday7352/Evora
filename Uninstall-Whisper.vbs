Option Explicit

' Windows Installed apps opens Evora's branded uninstaller with no console
' window. The native host owns the visible process, just as Frivo does.
Dim shell, fso, folder, scriptPath, host, args
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
scriptPath = folder & "\Evora-Uninstall.ps1"
host = folder & "\EvoraSetupHost.exe"
If fso.FileExists(host) And fso.FileExists(scriptPath) Then
    shell.Run Chr(34) & host & Chr(34) & " --script " & Chr(34) & scriptPath & Chr(34), 0, False
Else
    args = "-NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File """ & scriptPath & """"
    shell.Run "powershell.exe " & args, 0, False
End If
