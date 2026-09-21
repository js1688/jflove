## 🚀 v__VERSION__ 发布

### Docker 镜像（GHCR）

**数据库**
> 如果你不打算将数据库挂载到宿主机上，这一步可以跳过
```bash
# 也不是每次都需要这么做，主要是看开发记录，发布记录有没有涉及到数据库的变化，如果你实在不想看，可以先不更新数据库，只更新后端服务，看功能是否正常
# 下载数据库到本地，放到挂载到宿主机目录（例如：/home/tanjun/jflove/data）
# ⚠ 必须用 raw 地址：`/blob/` 是 GitHub 的网页地址，wget 下来的是 HTML 而不是 SQLite 文件
wget -O jflove-prod.db \
  https://raw.githubusercontent.com/__OWNER__/jflove/v__VERSION__/jflove-db/jflove-prod.db
```

**服务端**
```bash
# 拉取镜像（自动发布->一定会存在）
docker pull ghcr.io/__OWNER__/jflove-server:__VERSION__
# 拉取镜像（国内推荐->非自动发布，不一定存在，但可以尝试）
docker pull ccr.ccs.tencentyun.com/jflove/jflove-server:__VERSION__

# 启动（端口 8989；/data 数据目录、/storage 磁盘目录(系统内添加磁盘的时候，注意要使用容器内的目录)，宿主机路径按需调整）
docker run -d --name jflove-server \
  -p 8989:8989 \
  -v /home/tanjun/jflove/data:/data \
  -v /mnt:/storage \
  --restart=always \
  ghcr.io/__OWNER__/jflove-server:__VERSION__
```

**Web 端**
```bash
# 拉取镜像（自动发布->一定会存在）
docker pull ghcr.io/__OWNER__/jflove-web:__VERSION__
# 拉取镜像（国内推荐->非自动发布，不一定存在，但可以尝试）
docker pull ccr.ccs.tencentyun.com/jflove/jflove-web:__VERSION__

# 启动（宿主机 18080 → 容器 80）
docker run -d --name jflove-web \
  -p 18080:80 \
  --restart=always \
  ghcr.io/__OWNER__/jflove-web:__VERSION__
```

### 桌面端 / 移动端

从本页下方 **Assets** 下载对应平台的产物：

**桌面端**

- `JFLove-__VERSION__-win64-setup.exe` —— Windows **安装版**：双击安装，含开始菜单快捷方式与卸载器
- `JFLove-__VERSION__-win64-portable.zip` —— Windows **免安装版**：解压即用，不写注册表
- `jflove-desktop-__VERSION__-*.x86_64.rpm` —— Fedora / RHEL 系 **RPM 安装包**

```bash
# Fedora / RHEL 安装
sudo dnf install ./jflove-desktop-__VERSION__-*.x86_64.rpm
# 之后从应用菜单启动，或命令行运行 jflove
```

**移动端**

- `app-release.apk` —— Android 安装包

```bash
# Android 安装（手机已连电脑并开启 USB 调试）
adb install -r app-release.apk
```

> 桌面端为 **onedir（目录形态）**打包：不提供单体免安装 exe —— 单体形态每次启动都要把
> 数百 MB 运行时解压到临时目录，启动明显更慢。

### 完整发布记录

[文档记录/版本发布记录/v__VERSION__.md](https://github.com/__OWNER__/jflove/blob/main/文档记录/版本发布记录/v__VERSION__.md)
