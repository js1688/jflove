## 🚀 v__VERSION__ 发布

### Docker 镜像（GHCR）

**服务端**
```bash
# 拉取镜像
docker pull ghcr.io/__OWNER__/jflove-server:__VERSION__

# 启动（端口 8989；/data 数据目录、/storage 磁盘目录(多盘,配置磁盘时要使用容器内的磁盘目录,例如:/mnt/disk-a)，宿主机路径按需调整）
docker run -d --name jflove-server \
  -p 8989:8989 \
  -v /opt/jflove/data:/data \
  -v /mnt/disk-a:/storage/disk-a \
  -v /mnt/disk-b:/storage/disk-b \
  --restart=always \
  ghcr.io/__OWNER__/jflove-server:__VERSION__
```

**Web 端**
```bash
# 拉取镜像
docker pull ghcr.io/__OWNER__/jflove-web:__VERSION__

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
