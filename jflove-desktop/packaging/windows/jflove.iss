; JFLove Windows 安装包脚本（Inno Setup 6 / ISCC）
;
; 由构建脚本调用（推荐，不要手写版本号）：
;     python jflove-desktop/build.py --mode onedir --installer
;     # 或仓库根统一入口：python build.py -m desktop
;
; 手动编译也可以，但**必须**传版本号（版本号唯一真相 = 仓库根 version.json）：
;     iscc /DAppVersion=1.5.0 jflove.iss
;
; 设计要求（见 plans/desktop-packaging-onedir-installer-rpm-v1.5.0.md §3.2）：
;   - 版本号绝不硬编码：未传 /DAppVersion 直接编译报错
;   - 安装目录 {autopf}\JFLove；无管理员权限时向导可退化为「仅当前用户」安装
;     （Inno 6 语义：非管理员模式下 {autopf} 自动解析为用户目录）
;   - 开始菜单组 + 可选桌面快捷方式 + UninstallDisplayIcon
;   - 卸载时询问是否删除用户数据，**默认保留**
;   - 简体中文界面（需 ChineseSimplified.isl，缺失时退英文并给出编译期提示）
;   - **不打包任何用户数据 / 配置**：配置在 %APPDATA%\JFLove（见 src/config/settings.py）

#define AppName "JFLove"

; ── 版本号：必须由构建脚本传入，禁止硬编码 ──────────────────────
#ifndef AppVersion
  #error 必须传入版本号：iscc /DAppVersion=<version> jflove.iss（由 build.py --installer 自动传入）
#endif

; ── 可被构建脚本覆盖的定义（手动编译时的相对默认值）────────────
#ifndef AppPublisher
  #define AppPublisher "js1688"
#endif
#ifndef AppURL
  #define AppURL "https://github.com/js1688/jflove"
#endif
#ifndef AppExeName
  #define AppExeName "JFLove.exe"
#endif
#ifndef SourceDir
  #define SourceDir "..\..\build\dist\JFLove"
#endif
#ifndef OutputDir
  #define OutputDir "..\..\build\dist"
#endif
#ifndef OutputBaseFilename
  #define OutputBaseFilename "JFLove-" + AppVersion + "-win64-setup"
#endif
#ifndef IconFile
  #define IconFile "..\..\images\icon.ico"
#endif
#ifndef LicenseFile
  #define LicenseFile "..\..\..\LICENSE"
#endif
; 简体中文语言文件是否存在（由 build.py 探测后传入 0/1）
#ifndef HasChineseIsl
  #define HasChineseIsl 0
#endif

[Setup]
; AppId 固定不变：升级安装时据此识别同一应用（改这个值会导致装出两份）
AppId={{B7E4B0C2-5A6B-4E1F-9C3D-2F8A1D4E7C55}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
; {autopf} = 管理员安装时的 Program Files；非管理员模式自动退化为用户目录
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
AllowNoIcons=yes
LicenseFile={#LicenseFile}
OutputDir={#OutputDir}
OutputBaseFilename={#OutputBaseFilename}
SetupIconFile={#IconFile}
UninstallDisplayName={#AppName} {#AppVersion}
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
; 默认管理员安装到 Program Files；用户无管理员权限时，向导会提供
; 「仅为当前用户安装」的选项，此时 {autopf} 自动解析为用户目录
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
; PySide6 6.x / Qt 6 需要 64 位 Windows 10 1809+
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
; 升级安装时若旧版本仍在运行，交给 Restart Manager 提示关闭
CloseApplications=yes
RestartApplications=no

[Languages]
#if HasChineseIsl
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
#else
; Inno Setup 官方发行包不含简体中文语言文件（ChineseSimplified.isl 属社区翻译），
; 缺失时退回内置英文界面并在编译日志里提示——安装包本身照常产出。
#pragma message "未找到 Languages\ChineseSimplified.isl：本次安装向导使用英文界面（放入该文件后自动切中文）"
Name: "english"; MessagesFile: "compiler:Default.isl"
#endif

[Tasks]
; 桌面快捷方式：可选，默认不勾选
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; onedir 产物整目录安装（JFLove.exe + _internal/）。
; SourceDir 由 PyInstaller 生成，只含程序自身文件；用户数据/配置一律在
; %APPDATA%\JFLove，绝不随安装包分发。
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; Excludes: "*.log,*.tmp,*.bak,.DS_Store"

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(AppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

[Code]
const
  UserDataDirName = 'JFLove';

{ 用户数据目录（配置 / 登录会话 / 日志 / 本地存储），与安装目录完全分离 }
function GetUserDataDir(): String;
begin
  Result := ExpandConstant('{userappdata}\') + UserDataDirName;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  { 卸载完成后才询问；默认按钮为「否」→ 默认保留用户数据 }
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := GetUserDataDir();
    if DirExists(DataDir) then
    begin
      { ⚠ 必须用 SuppressibleMsgBox 而不是 MsgBox：
        Inno 文档明确 /SUPPRESSMSGBOXES 只抑制"可抑制的"消息框；
        脚本段里用普通 MsgBox 时，无人值守卸载会卡在对话框上等响应
        （本机实测：静默卸载耗时 163s 才结束，正常应远小于此）。
        SuppressibleMsgBox 在 /SILENT|/VERYSILENT 下自动采用默认按钮 ⇒ 默认保留用户数据，
        CI / 企业批量卸载不会挂住；交互式卸载仍照常弹窗。
        ⚠ 注释里不要出现以方括号开头的行（会被解析器当成段标签，实测编译报 Invalid section tag）。 }
      if SuppressibleMsgBox('是否同时删除 JFLove 的用户数据？' + #13#10 + #13#10 +
                            '包含配置、登录会话、日志、本地存储：' + #13#10 + DataDir + #13#10 + #13#10 +
                            '选择「否」将保留这些数据，重新安装后可继续使用。',
                            mbConfirmation, MB_YESNO or MB_DEFBUTTON2, IDNO) = IDYES then
        DelTree(DataDir, True, True, True);
    end;
  end;
end;
