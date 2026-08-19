Option Explicit

' Legacy entry point for Evora Setup. Delegate to the Evora-named entry
' point so every supported shortcut uses the branded Windows setup host.
Dim shell, fileSystem, folder, setupScript
Set shell = CreateObject("WScript.Shell")
Set fileSystem = CreateObject("Scripting.FileSystemObject")
folder = fileSystem.GetParentFolderName(WScript.ScriptFullName)
setupScript = folder & "\Evora-Setup.vbs"
shell.Run "wscript.exe """ & setupScript & """", 0, False
