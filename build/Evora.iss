#define AppName "Evora"
#define AppVersion Trim(FileRead(FileOpen("..\VERSION")))
#define AppPublisher "Friday"

[Setup]
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}.0
VersionInfoCompany={#AppPublisher}
VersionInfoDescription=Evora Setup
OutputDir=..\dist
OutputBaseFilename=EvoraSetup
Compression=lzma2/max
SolidCompression=yes
SetupIconFile=..\EvoraIcon.ico
PrivilegesRequired=admin
CreateAppDir=no
Uninstallable=no
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
DisableWelcomePage=yes
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
DisableReadyMemo=yes
DisableFinishedPage=yes
DisableStartupPrompt=yes
MinVersion=10.0

[Files]
Source: "..\Evora-Launcher.ps1";     DestDir: "{tmp}\EvoraSetupPayload"; Flags: ignoreversion deleteafterinstall
Source: "..\Evora-Setup.ps1";        DestDir: "{tmp}\EvoraSetupPayload"; Flags: ignoreversion deleteafterinstall
Source: "..\Evora-Setup.vbs";        DestDir: "{tmp}\EvoraSetupPayload"; Flags: ignoreversion deleteafterinstall
Source: "..\Evora-Uninstall.ps1";    DestDir: "{tmp}\EvoraSetupPayload"; Flags: ignoreversion deleteafterinstall
Source: "..\Evora.Ui.psm1";           DestDir: "{tmp}\EvoraSetupPayload"; Flags: ignoreversion deleteafterinstall
Source: "..\Evora.png";               DestDir: "{tmp}\EvoraSetupPayload"; Flags: ignoreversion deleteafterinstall
Source: "..\EvoraHost.exe";           DestDir: "{tmp}\EvoraSetupPayload"; Flags: ignoreversion deleteafterinstall
Source: "..\EvoraIcon.ico";           DestDir: "{tmp}\EvoraSetupPayload"; Flags: ignoreversion deleteafterinstall
Source: "..\EvoraSetupHost.exe";      DestDir: "{tmp}\EvoraSetupPayload"; Flags: ignoreversion deleteafterinstall
Source: "..\Install-Evora.ps1";       DestDir: "{tmp}\EvoraSetupPayload"; Flags: ignoreversion deleteafterinstall
Source: "..\LICENSE";                 DestDir: "{tmp}\EvoraSetupPayload"; Flags: ignoreversion deleteafterinstall
Source: "..\README.md";               DestDir: "{tmp}\EvoraSetupPayload"; Flags: ignoreversion deleteafterinstall
Source: "..\THIRD_PARTY_NOTICES.txt"; DestDir: "{tmp}\EvoraSetupPayload"; Flags: ignoreversion deleteafterinstall
Source: "..\Uninstall-Evora.vbs";     DestDir: "{tmp}\EvoraSetupPayload"; Flags: ignoreversion deleteafterinstall
Source: "..\evora_server.py";         DestDir: "{tmp}\EvoraSetupPayload"; Flags: ignoreversion deleteafterinstall
Source: "..\requirements.txt";        DestDir: "{tmp}\EvoraSetupPayload"; Flags: ignoreversion deleteafterinstall
Source: "..\VERSION";                 DestDir: "{tmp}\EvoraSetupPayload"; Flags: ignoreversion deleteafterinstall
Source: "..\evora_speakers.py";        DestDir: "{tmp}\EvoraSetupPayload"; Flags: ignoreversion deleteafterinstall
Source: "..\evora_api.py";             DestDir: "{tmp}\EvoraSetupPayload"; Flags: ignoreversion deleteafterinstall
Source: "..\evora_joint.py";           DestDir: "{tmp}\EvoraSetupPayload"; Flags: ignoreversion deleteafterinstall
Source: "..\vendor\*"; DestDir: "{tmp}\EvoraSetupPayload\vendor"; Excludes: "__pycache__\*,*.pyc"; Flags: ignoreversion deleteafterinstall recursesubdirs createallsubdirs
Source: "..\templates\speaker_settings.html"; DestDir: "{tmp}\EvoraSetupPayload\templates"; Flags: ignoreversion deleteafterinstall
Source: "..\docs\speaker-api.md"; DestDir: "{tmp}\EvoraSetupPayload\docs"; Flags: ignoreversion deleteafterinstall
Source: "..\docs\RELEASE-1.1.0.md"; DestDir: "{tmp}\EvoraSetupPayload\docs"; Flags: ignoreversion deleteafterinstall
Source: "..\docs\RELEASE-1.2.0.md"; DestDir: "{tmp}\EvoraSetupPayload\docs"; Flags: ignoreversion deleteafterinstall
Source: "..\docs\RELEASE-1.3.0.md"; DestDir: "{tmp}\EvoraSetupPayload\docs"; Flags: ignoreversion deleteafterinstall
Source: "..\docs\RELEASE-1.4.0.md"; DestDir: "{tmp}\EvoraSetupPayload\docs"; Flags: ignoreversion deleteafterinstall

[Run]
Filename: "{tmp}\EvoraSetupPayload\EvoraSetupHost.exe"; Parameters: "--script ""{tmp}\EvoraSetupPayload\Evora-Setup.ps1"""; Flags: waituntilterminated hidewizard 64bit

[Code]
function PostMessage(hWnd: HWND; Msg, wParam, lParam: Longint): Boolean;
  external 'PostMessageW@user32.dll stdcall';

procedure CurPageChanged(CurPageID: Integer);
begin
  if CurPageID = wpReady then
  begin
    WizardForm.Hide;
    PostMessage(WizardForm.NextButton.Handle, $00F5, 0, 0);
  end;
end;
