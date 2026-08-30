## 🚀 v__VERSION__ 发布

### Docker 镜像（GHCR）

**数据库**
> 如果你不打算将数据库挂载到宿主机上，这一步可以跳过
```bash
# 也不是每次都需要这么做，主要是看开发记录，发布记录有没有涉及到数据库的变化，如果你实在不想看，可以先不更新数据库，只更新后端服务，看功能是否正常
# 下载数据库到本地，放到挂载到宿主机目录（例如：/home/tanjun/jflove/data）
wget https://github.com/js1688/jflove/blob/v__VERSION__/jflove-db/jflove-prod.db
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

从本页下方 **Assets** 下载对应平台产物：

- 桌面端：`JFLove.exe`（Windows）/ `JFLove`（Linux），单文件免安装
- 移动端：`app-release.apk`（正式安装）

```bash
# 移动端安装
adb install -r app-release.apk
```

### 完整发布记录

[文档记录/版本发布记录/v__VERSION__.md](https://github.com/__OWNER__/jflove/blob/main/文档记录/版本发布记录/v__VERSION__.md)
