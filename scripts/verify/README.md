# scripts/verify — 开发/测试阶段实机验证入口

> 目标：**真实启动 + 可操作 + 可取证**，不是“打开页面截个图”。
>
> **Windows 端一律跑 KVM 真机虚拟机**：只有真 Windows 才能覆盖 Inno 安装器、文件关联、
> Defender/SmartScreen 与媒体解码；模拟层结果不得用于发布门禁。
> 判据统一为：截图/画面 + 日志或 HTTP 状态 + 状态确实改变（点了按钮有请求、登录后进主页）。

> **先跑 `scripts/verify/probe.sh`**：统一探测 Windows VM / Linux 显示 / Android，
> 再判“能否跳过”。禁止用默认 `virsh list` 或只试 `:99` 就断言“没有实机环境”。

## 先决条件

| 能力 | 本机现状 | 缺失时 |
| --- | --- | --- |
| 独立 X 显示 | `Xvnc`、`kwin_x11` | 用户会话锁屏时窗口会被标 HIDDEN，截屏全黑，必须用 Xvnc |
| 浏览器 | `microsoft-edge` | 无 Chromium/Firefox；Edge 就是 Chromium 内核 |
| Android | `~/sdk/android-sdk` + AVD `jflove-api36` + KVM | `flutter doctor` 检查 |
| Windows VM | libvirt 域 `jflove-win10` + `/dev/kvm` | `scripts/verify/vm/` 里的应答文件重建（见下节） |
| 截图 | `ffmpeg`（Xvnc 上可靠） | ImageMagick `import` 本机不可用 |

## 脚本

| 脚本 | 作用 | 快速用法 |
| --- | --- | --- |
| `env.sh` | 公共变量/函数（Xvnc、WM、抓帧、等 HTTP） | `source scripts/verify/env.sh` |
| `server.sh` | 临时库 + `run_server.py` + `/health` | `scripts/verify/server.sh start` |
| `web_shot.mjs` | Edge 无头 + CDP 点击/执行 JS + 截图 | `node scripts/verify/web_shot.mjs --click-text '连接服务端'` |
| `desktop_shot.py` | 桌面端 Web UI 离屏抓帧 | `jflove-desktop/venv-linux/bin/python scripts/verify/desktop_shot.py` |
| `android_smoke.sh` | 窗口化模拟器 + 安装 + 启动 + 截图 | `scripts/verify/android_smoke.sh` |
| `vm-win.sh` | Windows 真机 VM 生命周期（起停/截屏/断网/快照/资源占用） | `scripts/verify/vm-win.sh up` / `down` / `footprint` |
| `windows_vm.py` | 经 QGA 在客机执行命令、传文件、模拟键盘 | `python3 scripts/verify/windows_vm.py exec --ps "ver"` |
| `windows_vm_ui.mjs` | CDP 驱动 VM 内 QtWebEngine 界面（点击/输入/断言/截图） | `node scripts/verify/windows_vm_ui.mjs --host <ip> --port 9223 --wait-text '连接服务端'` |
| `windows_vm_e2e.sh` | Windows 端端到端验收（安装→启动→密钥交换→登录→上传校验） | `scripts/verify/windows_vm_e2e.sh` |
| `xinput.py` | X11 点击/输入/清空/聚焦（Shift 已映射） | `python3 scripts/verify/xinput.py click 718 593` |
| `run_all.sh` | 一键跑选中的端，产出证据摘要 | `scripts/verify/run_all.sh --ends all` |
| `check_uat_build.py` | Phase 6.5：核验 UAT 构建物是否由指定 commit 触发（head_sha + success） | `python scripts/verify/check_uat_build.py --sha <commit>` |

## 典型流程

### 1. 服务端 + Web

```bash
scripts/verify/server.sh start
(cd jflove-web && npm run dev -- --host 127.0.0.1 --port 3000 &)
node scripts/verify/web_shot.mjs --click-text '连接服务端' --out /tmp/web.png
scripts/verify/server.sh stop
```

判据：截图里有登录表单；服务端日志出现 `POST /api/v1/auth/key-exchange 200`。

### 2. Linux 桌面（可操作）

```bash
source scripts/verify/env.sh
DISP=$(jf_start_xvnc :99); jf_start_wm "$DISP"
DISPLAY=$DISP QTWEBENGINE_CHROMIUM_FLAGS="--disable-gpu --no-sandbox" \
  jflove-desktop/venv-linux/bin/python jflove-desktop/src/main.py &
sleep 10
jf_capture "$DISP" /tmp/desktop.png
python3 scripts/verify/xinput.py --display "$DISP" click 590 432
python3 scripts/verify/xinput.py --display "$DISP" text 'http://127.0.0.1:8989'
```

> 桌面端默认地址是 `http://localhost.jflove.cn:8989`，本机不解析；验证时改成 `127.0.0.1`。

### 3. Android

```bash
scripts/verify/android_smoke.sh          # 装包 + 启动 + 截图
adb shell uiautomator dump /sdcard/ui.xml && adb pull /sdcard/ui.xml /tmp/ui.xml
adb shell input tap 540 1114 && adb shell input text 'http://10.0.2.2:8989'
adb exec-out screencap -p > /tmp/android.png
```

判据：模拟器从 `10.0.2.2:8989` 连到宿主后端；登录后 `files/disks 200`。

### 4. Windows（KVM 真机 VM）

```bash
scripts/verify/vm-win.sh up            # 起网络 + 起 VM + 等 QGA
scripts/verify/windows_vm_e2e.sh       # 全流程：装包→启动→密钥交换→登录→上传校验→关机
```

单步排障：

```bash
scripts/verify/vm-win.sh status                       # 域状态 / 客机 IP / QGA / 宿主机内存
scripts/verify/vm-win.sh shot /tmp/win.png            # 客机截屏（原生弹窗也能拍）
python3 scripts/verify/windows_vm.py exec --ps "ver"  # 客机执行命令
python3 scripts/verify/windows_vm.py type 'C:\path'  # 模拟键盘（文件选择框等原生控件）
scripts/verify/vm-win.sh net down                     # 断网注入（验证断点续传/重连）
scripts/verify/vm-win.sh snap clean / revert clean    # 快照回滚（秒级回到干净基线）
scripts/verify/vm-win.sh footprint                    # 实测「停了不占 CPU/内存」
```

判据：CDP 能 attach → `key-exchange 200` + `login 200` → 上传文件宿主机落盘 SHA256 与客机一致。

**VM 从零重建**（换机器 / 镜像损坏时）：`scripts/verify/vm/` 里是全部构建输入

```bash
scripts/verify/vm/build-autounattend.py    # 生成 autounattend.xml + 1.44MB 软盘镜像
scripts/verify/vm/create-vm.sh             # virt-install 建域（Win10 22H2 + virtio-win 驱动 ISO）
```

前提：`sudo dnf install -y qemu-kvm-core libvirt-daemon-kvm libvirt-client virt-install \
genisoimage xorriso guestfs-tools swtpm swtpm-tools`；`virtio-win.iso` 放到
`/var/lib/libvirt/images/`；Windows ISO 同理。域默认**开机不自启**，关机即零占用。

## 无桌面登录时（纯后台 Linux）

不需要 SDDM / 用户会话：`Xvnc` 是虚拟 X 服务器，Qt / Android 模拟器只依赖它；Windows VM 走 libvirt + spice，不需要 X。
`env.sh` 在 `XDG_RUNTIME_DIR` 缺失时会回退到 `/tmp/jf-runtime-<uid>`（0700），
`kwin_x11` 通过 `dbus-run-session` 自建会话总线，**不依赖 pam_systemd**。

实测（`env -i` + 私有 DBus，不继承任何桌面会话）：

- 真实桌面客户端：启动 → 点击/输入 → 服务端 `key-exchange 200`；
- Android 模拟器：`-gpu host` 启动 → 装包 → 截图；
- Windows VM：`vm-win.sh up` → CDP attach → 截图与接口证据。

> 性能会低一些（Xvnc 走 llvmpipe 软件 GL），但「能操作、能取证」不受影响。

## 报告证据门禁

测试/发布报告必须在 `## 验证证据` 下给出表格（可直接引用 `run_all.sh` 的摘要）：

```markdown
## 验证证据

| 端 | 命令 | 结果 | 证据 |
| --- | --- | --- | --- |
| 服务端 | `scripts/verify/server.sh smoke` | 退出码 0；/health 200 | /tmp/jf-server.log |
| Web | `node scripts/verify/web_shot.mjs --click-text '连接服务端'` | key-exchange 200 | /tmp/jf-web.png |
| Linux 桌面 | `python scripts/verify/desktop_shot.py` | 1180x760 抓帧 | /tmp/jf-desktop.png |
| Android | `scripts/verify/android_smoke.sh --stop` | 安装 Success；启动截图 | /tmp/jf-android.png |
| Windows | `scripts/verify/windows_vm_e2e.sh --keep` | 安装→启动→密钥交换→登录→上传校验 | /tmp/jf-win/e2e/SUMMARY.md |
```

收工前必须跑：

```bash
python scripts/check_verification_report.py --version <版本号> \
    --require server,web,desktop,android,windows
```

退出码 0 才允许出报告；CI 在打 tag 时会独立复核同一份报告结构。

## 已知边界

- **锁屏会话**：KWin 把所有窗口标 `_NET_WM_STATE_HIDDEN`，X11 截屏黑/白；一律用 Xvnc。
- **无头模拟器**：`-no-window` 会 framebuffer 失败；swiftshader/guest 会 SIGSEGV；必须窗口化 + `-gpu host`。
- **服务端临时库**：必须 `run_server.py`；`uvicorn src.main` 会绕过 `JFLOVE_DB_PATH`。
- **Windows VM**：不覆盖真实 GPU 渲染与硬件解码、真机低配性能、第三方杀软策略；这些在报告里写「未覆盖（原因）」，不写「通过」。
- **启动即零占用**：VM 关机后 `qemu` 进程必须为 0（`vm-win.sh down` 内含断言），否则视为缺陷。
- **MCP**：`.mcp.json` 是 VS Code 格式，DSH 未加载；本目录走 CLI，MCP 仅 VS Code 可选。
