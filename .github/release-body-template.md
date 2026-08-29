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

从本页下方 **Assets** 下载对应平台产物：

- 桌面端：`JFLove.exe`（Windows）/ `JFLove`（Linux），单文件免安装
- 移动端：`app-release.apk`（正式安装）/ `app-debug.apk`（调试）

```bash
# 移动端安装
adb install -r app-release.apk
```

### 完整发布记录

[文档记录/版本发布记录/v__VERSION__.md](https://github.com/__OWNER__/jflove/blob/main/文档记录/版本发布记录/v__VERSION__.md)
