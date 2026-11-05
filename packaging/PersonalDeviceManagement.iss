; PersonalDeviceManagement Windows 安装包(Inno Setup 6)
; 按用户级安装(无 UAC、无需管理员),默认装到 %LOCALAPPDATA%\Programs
; 构建:ISCC /DAPP_VERSION=1.1.0 PersonalDeviceManagement.spec

#define AppName "个人器件库 PersonalDeviceManagement"
#define AppExe "PersonalDeviceManagement.exe"
#ifndef APP_VERSION
  #define APP_VERSION "1.1.0"
#endif

[Setup]
AppId={{7C1E2A64-6B7E-4F8A-9C3D-2E5A1B0F9D41}
AppName={#AppName}
AppVersion={#APP_VERSION}
AppPublisher=equintity
DefaultDirName={autopf}\PersonalDeviceManagement
PrivilegesRequired=lowest
DisableProgramGroupPage=yes
OutputDir=..\release
OutputBaseFilename=PersonalDeviceManagement-v{#APP_VERSION}-win64-setup
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
UninstallDisplayIcon={app}\{#AppExe}
ArchitecturesInstallIn64BitMode=x64compatible
; 不做全局文件关联/服务/驱动,卸载即删干净

[Files]
Source: "..\dist\PersonalDeviceManagement\*"; DestDir: "{app}"; \
  Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\卸载 {#AppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; \
  Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; \
  GroupDescription: "附加任务:"

[Run]
Filename: "{app}\{#AppExe}"; Description: "立即运行 {#AppName}"; \
  Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 卸载不触碰 %LOCALAPPDATA%\PersonalDeviceManagement(用户器件数据保留)
Type: filesandordirs; Name: "{app}"
