# jflove 自动化测试用 Windows 客机初始化脚本
# 执行时机：无人值守安装的 specialize 阶段（SYSTEM 权限，早于 OOBE 与首次登录）
# 目标：打通 QGA 自动化通道、提高显示分辨率、掐掉会引入"测试抖动"的后台服务
$ErrorActionPreference = 'SilentlyContinue'
$log = 'C:\provision.log'
function Log([string]$m) { "$(Get-Date -Format 'HH:mm:ss') $m" | Out-File -Append -Encoding utf8 $log }
Log '=== provision start ==='

# --- 1. 定位 virtio-win 驱动 ISO（按内容特征找，不依赖盘符） ---
$virtio = $null
foreach ($d in (Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=5' | Select-Object -ExpandProperty DeviceID)) {
    if (Test-Path "$d\guest-agent\qemu-ga-x86_64.msi") { $virtio = $d; break }
}
Log "virtio-win ISO => $virtio"

if ($virtio) {
    # 1.1 驱动：vioserial（QGA 依赖）、viogpudo（高分辨率）、balloon（气球内存）
    foreach ($inf in @(
            "$virtio\vioserial\w10\amd64\vioser.inf",
            "$virtio\viogpudo\w10\amd64\viogpudo.inf",
            "$virtio\Balloon\w10\amd64\balloon.inf"
        )) {
        if (Test-Path $inf) {
            $p = Start-Process -FilePath 'pnputil.exe' -ArgumentList "/add-driver `"$inf`" /install" -Wait -PassThru -NoNewWindow
            Log "pnputil $(Split-Path $inf -Leaf) => $($p.ExitCode)"
        }
    }
    Start-Sleep -Seconds 3
    # 1.2 QEMU Guest Agent：宿主机通过 virtio-serial 执行 guest-exec / guest-file-*，完全离线
    #     注意：在 specialize 阶段装 MSI 会因 VSS COM 注册尚未就绪而失败（Error 1722/1603，实测），
    #     所以先试一次，失败则登记开机任务，等 OOBE 之后的正常会话里以 SYSTEM 最高权限重试。
    New-Item 'C:\jf' -ItemType Directory -Force | Out-Null
    Copy-Item "$virtio\guest-agent\qemu-ga-x86_64.msi" 'C:\jf\qemu-ga.msi' -Force
    $m = Start-Process -FilePath 'msiexec.exe' -ArgumentList '/i C:\jf\qemu-ga.msi /qn /l*v C:\qga-install.log' -Wait -PassThru -NoNewWindow
    Log "qga msi (specialize) => $($m.ExitCode)"
    if ($m.ExitCode -ne 0) {
        $ps = 'if (-not (Get-Service qemu-ga -ErrorAction SilentlyContinue)) { Start-Process msiexec ''/i C:\jf\qemu-ga.msi /qn /l*v C:\qga-retry.log'' -Wait }'
        $act = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -WindowStyle Hidden -Command `"$ps`""
        $prin = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
        Register-ScheduledTask -TaskName 'JF-QGA-Install' -Action $act -Trigger (New-ScheduledTaskTrigger -AtStartup) -Principal $prin -Force | Out-Null
        Log 'qga msi 已登记开机重试任务 JF-QGA-Install'
    }
    # 1.3 自动登录：无人值守靶机必须能重启后自己回到桌面
    $wl = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
    Set-ItemProperty $wl -Name AutoAdminLogon -Value '1'
    Set-ItemProperty $wl -Name DefaultUserName -Value 'jf'
    Set-ItemProperty $wl -Name DefaultPassword -Value 'jflove'
    # LogonCount 用尽后 Windows 会把 AutoAdminLogon 抹成 0（实测踩过），这里把计数拉满
    Set-ItemProperty $wl -Name AutoLogonCount -Value 999 -Type DWord -ErrorAction SilentlyContinue
    # 锁屏会让 GUI 自动化无窗口可点：禁用锁屏与自动锁屏超时
    New-Item 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Personalization' -Force | Out-Null
    Set-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Personalization' -Name NoLockScreen -Value 1 -Type DWord
    Set-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System' -Name InactivityTimeoutSecs -Value 0 -Type DWord
    # 1.4 首次登录的"网络发现"浮层会挡住自动化截图
    New-Item 'HKLM:\SYSTEM\CurrentControlSet\Control\Network' -Force | Out-Null
    Set-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Network' -Name NewNetworkWindowOff -Value 1 -Type DWord
}

# --- 2. RDP 备用通道（QGA 失效时的兜底） ---
Set-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server' -Name fDenyTSConnections -Value 0
Enable-NetFirewallRule -DisplayGroup '远程桌面' -ErrorAction SilentlyContinue
Log 'rdp enabled'

# --- 3. 关闭混合关机：保证 ACPI 关机是真正的 S5 断电，快照/回滚才干净 ---
Set-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Power' -Name HiberbootEnabled -Value 0

# --- 4. 电源策略：永不睡眠/休眠/关屏（测试期间不允许自动挂起） ---
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 0
powercfg /change disk-timeout-ac 0

# --- 5. 关闭自动更新相关服务：自发重启是"测试抖动"最大来源 ---
foreach ($s in @('wuauserv', 'UsoSvc', 'WaaSMedicSvc', 'DoSvc')) {
    Set-Service -Name $s -StartupType Disabled
}
Stop-Service -Name 'wuauserv' -Force
Log 'windows update disabled'

# --- 6. 关掉空闲时吃 CPU 的计划任务（否则"空闲"VM 也会烧 5~15% 宿主机 CPU） ---
$tasks = @(
    @{ P = '\Microsoft\Windows\Defrag\'; N = 'ScheduledDefrag' },
    @{ P = '\Microsoft\Windows\Windows Defender\'; N = 'Windows Defender Scheduled Scan' },
    @{ P = '\Microsoft\Windows\UpdateOrchestrator\'; N = 'Schedule Scan' },
    @{ P = '\Microsoft\Windows\UpdateOrchestrator\'; N = 'USO_UxBroker' },
    @{ P = '\Microsoft\Windows\DiskDiagnostic\'; N = 'Microsoft-Windows-DiskDiagnosticDataCollector' },
    @{ P = '\Microsoft\Windows\Application Experience\'; N = 'Microsoft Compatibility Appraiser' },
    @{ P = '\Microsoft\Windows\Application Experience\'; N = 'ProgramDataUpdater' },
    @{ P = '\Microsoft\Windows\Customer Experience Improvement Program\'; N = 'Consolidator' },
    @{ P = '\Microsoft\Windows\Feedback\Siuf\'; N = 'DmClient' }
)
foreach ($t in $tasks) {
    try { Disable-ScheduledTask -TaskPath $t.P -TaskName $t.N -ErrorAction Stop | Out-Null; Log "task off $($t.N)" } catch { }
}

# --- 7. Defender：关实时监控与脚本扫描（不影响 SmartScreen 对安装包的拦截测试） ---
Set-MpPreference -DisableRealtimeMonitoring $true -DisableScriptScanning $true -DisableBehaviorMonitoring $true

# --- 8. 遥测拉满关闭 ---
New-Item 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection' -Force | Out-Null
Set-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection' -Name AllowTelemetry -Value 0 -Type DWord

# --- 8.5 关掉首次登录的"隐私设置/让我们完成设置"向导与首登动画，避免卡在无人值守流程 ---
New-Item 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\OOBE' -Force | Out-Null
Set-ItemProperty 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\OOBE' -Name DisablePrivacyExperience -Value 1 -Type DWord
Set-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\OOBE' -Name DisablePrivacyExperience -Value 1 -Type DWord
Set-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System' -Name EnableFirstLogonAnimation -Value 0 -Type DWord

# --- 9. 记录客机指纹，便于宿主机侧断言版本一致性 ---
"$(Get-CimInstance Win32_OperatingSystem | Select-Object -ExpandProperty Caption) | $((Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion').DisplayVersion) | build $((Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion').CurrentBuild)" |
    Out-File -Encoding utf8 'C:\guest-version.txt'
Log '=== provision done ==='
