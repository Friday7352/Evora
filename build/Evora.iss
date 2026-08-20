#define AppName "Evora"
#define AppVersion "1.0.0"
#define AppPublisher "Friday"

[Setup]
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion=1.0.0.0
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

[Run]
Filename: "{tmp}\EvoraSetupPayload\EvoraSetupHost.exe"; Parameters: "--script ""{tmp}\EvoraSetupPayload\Evora-Setup.ps1"""; Flags: waituntilterminated hidewizard 64bit
