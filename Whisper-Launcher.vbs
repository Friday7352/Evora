Option Explicit

' Opens the Whisper status window without a console window.
Dim shell, fso, folder, args
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
args = "-NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File """ & folder & "\Whisper-Launcher.ps1"""
shell.Run "powershell.exe " & args, 0, False
