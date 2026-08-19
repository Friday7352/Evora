Option Explicit

' Windows Installed apps uses a hidden bootstrap so it can explicitly
' elevate the native Evora host instead of failing silently from WScript.
Dim shell, fso, folder, scriptPath, args
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
scriptPath = folder & "\Evora-Uninstall.ps1"
args = "-NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File """ & scriptPath & """"
shell.Run "powershell.exe " & args, 0, False
