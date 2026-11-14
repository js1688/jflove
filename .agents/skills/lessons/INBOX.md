# 复盘收件箱（INBOX）

> **唯一真相**：本文件。规则见 `AGENTS.md` §6.5，执行体见 `.agents/skills/skills-self-growth/SKILL.md`。
> **append-only**：新条目追加到表尾；只有 triage 才允许改「状态」列（`open` → `triaged` / `rejected`）。
> 本文件由 `scripts/check_retro.py` 校验（结构 + 脱敏），退出码即判据。

## 什么时候写（满足任一才写，否则**什么都不写**）

1. 某条门禁红过，或同一任务需要返工；
2. 实际行为与某技能写的判据不符；
3. 同类问题**第二次**出现；
4. 技能里写了某条规则，但实际被绕过。

## 写作硬要求

- **一行一条**，8 列，列头不得增删改（脚本按表头判定）。
- **证据必须可复现**：`<命令> → exit <码>` 或 `<文件>:<行>`。**没有证据的自评结论不算证据**，会被 triage 直接驳回。
- **脱敏**：token / 密码 / 私钥 / Cookie / 真实 IP / 真实域名 / 绝对家目录路径 一律写 `<redacted>`。
  允许 `127.0.0.1` / `localhost`（脚本白名单）。
- 「复发」写累计次数（本次算 1）。**第二次复发 = 停止加文字，升级成脚本门禁**（`AGENTS.md` §6.5 C）。
- 不要在这里写解决方案的完整实现 —— 那是 triage 后的提案内容。

## 表（未 triage 条目应少于 40 条，超了先 triage 再写新的）

| 日期 | 角色/任务 | 现象 | 证据 | 归因 | 复发 | 建议动作 | 状态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-11-14 | testing / Windows VM | 应答文件把 12KB Base64 脚本塞进 `RunSynchronousCommand` 的 `Path`，Win10 安装直接报「无法分析或处理 pass [specialize] 的无人参与应答文件」；根因是命令行 8191 字符上限 | `scripts/verify/vm/build-autounattend.py:24`（改为 `-File A:\setup-guest.ps1`，命令 76 字符） | 技能缺判据：无人值守安装未写「命令长度上限」 | 1 | 在 testing 技能写死：应答文件里的命令一律走脚本文件，禁止内嵌长 Base64 | triaged |
| 2026-11-14 | testing / Windows VM | 在 `specialize` 阶段装 qemu-ga MSI 必失败（Error 1722 `Rundll32 ... DLLCOMRegister` → 1603），QGA 通道起不来，宿主机无法自动化 | `scripts/verify/vm/setup-guest.ps1:34`（改为先试装，失败则登记 `JF-QGA-Install` 开机任务在 OOBE 后重试） | 技能缺判据：VSS COM 注册在 specialize 阶段不可用 | 1 | 写进技能：VM 内 agent/服务类安装一律延后到 OOBE 之后，用计划任务以 SYSTEM 重试 | triaged |
| 2026-11-14 | testing / Windows VM | 无人值守 VM 重启后停在锁屏，GUI 自动化无窗口可点；根因是应答文件 `LogonCount` 用尽后 Windows 把 `AutoAdminLogon` 抹成 0 | `scripts/verify/vm/setup-guest.ps1:49`（`AutoLogonCount=999` + 禁锁屏 `:52`） | 技能缺判据：靶机必须「重启后自动回到可交互桌面」 | 1 | 写进技能：VM 靶机初始化必须包含自动登录长效化 + 禁锁屏 + 禁自动锁屏超时 | triaged |
| 2026-11-14 | testing / Windows VM | `virsh undefine --remove-all-storage` 把挂着的 Windows ISO / virtio-win ISO / 软盘镜像一起删了，重建要重下 6G+ | `scripts/verify/vm/create-vm.sh:17`（改为 `undefine --nvram` + 只 `rm` 系统盘 `:20`） | 技能缺判据：libvirt 存储清理的连带范围 | 1 | 写进技能：销毁 VM 只删系统盘，介质一律 `--nvram` 后手工确认 | triaged |
| 2026-11-14 | testing / Windows VM | 想用 guestfish 热修已装系统的 `C:\Windows\Panther\unattend.xml`，NTFS 卷被以只读挂载（脏卷），无法写入；结论是**重装比热修更省时** | `guestfish --rw -a <disk> -m /dev/sda1 upload ...` → 报 `Read-only file system`、`ntfsfix` 后仍只读 | 技能缺判据：guestfs 对脏 NTFS 的写能力被高估 | 1 | 写进技能：应答文件错误一律重建 VM，不要试图热修已安装系统 | triaged |
| 2026-11-14 | testing / Windows VM | CDP 自动化在客户端刚启动时连续超时：`/json/version` 先可用而页面未就绪，每次 attach 约 8s 握手，30s 超时×多轮白烧近 8 分钟（同一问题连续 3 轮返工） | `scripts/verify/windows_vm_e2e.sh:132`（单次 attach 给 12s、外层 25s）、`:136`（先 sleep 6 再轮询） | 技能缺判据：QtWebEngine 的 CDP 就绪判据与握手开销 | 3 | **升级门禁**：把「CDP 就绪 = 能 evaluate 出页面文本」写进脚本，超时按 12s 起算 | triaged |
| 2026-11-14 | testing / Windows VM | 原生「选择文件」框里输入的路径已显示，首次 Enter 被吞、上传不触发；重试 Enter 才生效 | `scripts/verify/windows_vm_e2e.sh:186`（重试 5 轮 + 轮询落盘） | 技能缺判据：原生模态框的确认键时序 | 2 | 写进技能：原生对话框一律「输入 → 等 2s → 重试 Enter → 轮询结果」 | triaged |
| 2026-11-14 | cross-platform-desktop / v1.10.x 实机验证 | Windows VM 被误判“不存在”（用 `virsh list` 默认 `qemu:///session`，而域在 `qemu:///system`），连续 3 个版本未做真机验证；Linux 只试 `:99` 就断言无显示，未看 `loginctl` 与 `/run/user/*/wayland-*` | `virsh -c qemu:///system list --all` → `jflove-win10 关闭`；`DISPLAY`/`WAYLAND_DISPLAY` 均空、`loginctl` 有 `tty2` 会话、`/run/user/1000/wayland-1` 存在；`:0/:1/:99 xdpyinfo` 全失败 | 技能缺统一探测入口：把“默认命令/默认 URI”的输出当真相 | 2 | 新增 `scripts/verify/probe.sh`；技能写死“先跑 probe 再判跳过” | open |
