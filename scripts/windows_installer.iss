; Build with scripts/build_installer.py after scripts/build_windows.py.
#ifndef AppVersion
  #error AppVersion must be supplied by the build script
#endif
#ifndef RepoRoot
  #error RepoRoot must be supplied by the build script
#endif

[Setup]
AppId={{6FF2F847-1F50-4B71-B7F9-DA661585D641}
AppName=Proxmox ZFS Assistant
AppVersion={#AppVersion}
AppPublisher=Rudi Gomirato
AppPublisherURL=https://github.com/Rudi-Gomirato/proxmox-zfs-assistant
AppSupportURL=https://github.com/Rudi-Gomirato/proxmox-zfs-assistant/issues
DefaultDirName={localappdata}\Programs\Proxmox ZFS Assistant
DefaultGroupName=Proxmox ZFS Assistant
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile={#RepoRoot}\LICENSE
OutputDir={#RepoRoot}\dist
OutputBaseFilename=ProxmoxZfsAssistant-{#AppVersion}-Windows-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\ProxmoxZfsAssistant.exe
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "italian"; MessagesFile: "compiler:Languages\Italian.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Crea un collegamento sul desktop"; Flags: unchecked

[Files]
Source: "{#RepoRoot}\dist\ProxmoxZfsAssistant\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Proxmox ZFS Assistant"; Filename: "{app}\ProxmoxZfsAssistant.exe"
Name: "{autodesktop}\Proxmox ZFS Assistant"; Filename: "{app}\ProxmoxZfsAssistant.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\ProxmoxZfsAssistant.exe"; Description: "Avvia Proxmox ZFS Assistant"; Flags: nowait postinstall skipifsilent

; No uninstall-delete rules: the user's database and SSH known_hosts are kept.
