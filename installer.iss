; Script do Inno Setup 6.3+ para gerar o instalador do BPM Renamer.
; Use o build_windows.bat: ele gera o programa com o PyInstaller e depois chama este script.

#define AppName "BPM Renamer"
#define AppVersion "5.0"
#define AppExe "BPMRenamer.exe"

[Setup]
AppId={{6F1B7C52-3E0A-4D8B-9A64-2C5E8B71D3A9}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=BPM Renamer
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=BPMRenamer_Setup_{#AppVersion}
SetupIconFile=icone.ico
UninstallDisplayIcon={app}\{#AppExe}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; Somente Windows 10/11 de 64 bits
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Instala sem precisar de administrador (so para o usuario atual); o assistente permite escolher "para todos".
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
CloseApplications=yes

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Programa + todas as bibliotecas geradas pelo PyInstaller
Source: "dist\BPMRenamer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
