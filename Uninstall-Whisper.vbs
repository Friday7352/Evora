Option Explicit

' Windows Installed apps uses this silent shim. The PowerShell file is copied
' out of the install directory first, so it can safely remove that directory.
Dim shell, fso, folder, tempFolder, setupCopy, args
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
folder = fso.GetParentFolderName(WScript.ScriptFullName)
tempFolder = shell.ExpandEnvironmentStrings("%TEMP%") & "\Whisper-Uninstall"
If Not fso.FolderExists(tempFolder) Then fso.CreateFolder(tempFolder)
setupCopy = tempFolder & "\Install-Whisper.ps1"
fso.CopyFile folder & "\Install-Whisper.ps1", setupCopy, True
args = "-NoProfile -ExecutionPolicy Bypass -STA -WindowStyle Hidden -File """ & setupCopy & """ -Uninstall -InstallPath """ & folder & """"
shell.Run "powershell.exe " & args, 0, False
