; ---------------------------------------------------------------------------
; JFLove 文件关联注册片段（生成物，请勿手改）
;
; 生成命令：python scripts/gen_file_assoc_assets.py
; 一致性判据：python scripts/gen_file_assoc_assets.py --check
; 唯一真相：ui/src/config/scratch-types.ts 的 TYPES
;           → ui/public/file-assoc.manifest.json（随 dist/ 一起进包，见设计 §6.7.1）
;
; 本文件是【段内片段】：由 packaging/windows/jflove.iss 在 [Registry] 段里 #include。
; 开头的 #define 会被 ISPP 预处理掉，供 [Code] 的卸载清理使用（§6.7.2）。
;
; 注册位置（HKCU；管理员安装也写 HKCU，理由见设计 §6.7.2 的裁决表）：
;   <应用键>                          默认值 = 应用可读名称
;   <应用键>\DefaultIcon              默认值 = "{app}\JFLove.exe",0
;   <应用键>\shell\open\command     默认值 = "{app}\JFLove.exe" "%1"
;   <应用键>\SupportedTypes           每个扩展名一个空值
; 其中 <应用键> = HKCU\Software\Classes\Applications\JFLove.exe
;
; ⚠ 严格不写（D18 / AC-43 ②）：
;   ❌ HKCU\Software\Classes\.<ext>            （会抢占默认程序）
;   ❌ ...\FileExts\.<ext>\UserChoice           （同样会抢占默认程序）
;   ❌ ChangesAssociations=yes / 任何「设为默认」的 API
;   SupportedTypes 的【值名】带前导点（".json"）是 Windows 的规定（值名 = 文件扩展名），
;   与上面禁止的 Software\Classes\.<ext>【键】不是一回事。
; ---------------------------------------------------------------------------

; [Code] 卸载清理用的清单（逗号分隔，顺序 = manifest 顺序）
#define FILE_ASSOC_EXTS "json,jsonc,md,markdown,csv,tsv,sql,xml,html,svg,yaml,yml,toml,ini,conf,properties,css,py,js,ts,tsx,jsx,sh,bat,ps1,java,go,rs,c,cpp,log,txt,diff,patch"
; 扩展名条数（装配期自检用；与 ui/public 里的清单必然相等）
#define FILE_ASSOC_COUNT 34
; 注册表里的应用可执行文件名 + 应用键全路径（[Code] 删整键时用）
#define FILE_ASSOC_EXE "JFLove.exe"
#define FILE_ASSOC_APP_KEY "Software\Classes\Applications\JFLove.exe"

; 构建期护栏：Applications 子键名必须与真实可执行文件名一致，否则「打开方式」列不出本应用
#if AppExeName != FILE_ASSOC_EXE
  #error jflove.iss 的 AppExeName 与文件关联注册片段不一致（重跑 gen_file_assoc_assets.py）
#endif

; ① 应用标识 / 图标 / 打开命令（AC-43 ① 的判据就是该键存在）
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe"; ValueType: string; ValueName: ""; ValueData: "JFLove 文本编辑器"
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\DefaultIcon"; ValueType: string; ValueName: ""; ValueData: "{app}\JFLove.exe,0"
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\JFLove.exe"" ""%1"""

; ② SupportedTypes：每个扩展名一个空值（空 REG_SZ；值名带前导点，见文件头说明）
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".json"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".jsonc"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".md"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".markdown"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".csv"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".tsv"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".sql"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".xml"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".html"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".svg"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".yaml"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".yml"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".toml"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".ini"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".conf"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".properties"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".css"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".py"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".js"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".ts"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".tsx"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".jsx"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".sh"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".bat"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".ps1"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".java"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".go"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".rs"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".c"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".cpp"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".log"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".txt"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".diff"; ValueData: ""
Root: HKCU; Subkey: "Software\Classes\Applications\JFLove.exe\SupportedTypes"; ValueType: string; ValueName: ".patch"; ValueData: ""
