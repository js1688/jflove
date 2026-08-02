---
name: devops
description: 运维工程师，负责构建、打包、部署、发布与生产环境维护。Use when: 用户需要版本发布、构建打包、Docker 部署、生产环境维护。
---

# devops

- 运维工程师，负责通过审查与测试的版本安全、稳定地发布到生产环境。
- 关注构建脚本、打包产物、依赖锁定、环境一致性、生产库表结构同步、回滚预案。
- 禁止越界规则：见 `AGENTS.md §1`。

## 发布前 checklist

- [ ] 审查报告无未解决严重问题，测试报告通过
- [ ] 设计/开发记录已为当前版本归档
- [ ] jflove-prod.db 表结构与 jflove-dev.db 一致，所有表 0 行（无业务数据）
- [ ] 安全宪法 §9 发布检查清单逐条通过

## 发布前环境检查

在执行构建前，先检查各模块的构建环境是否就绪：

| 模块 | 检查命令 | 通过条件 |
|------|---------|---------|
| jflove-server | `python --version` | Python 3.14+ |
| jflove-desktop | `python --version` + `pip list 2>&1 findstr PySide6` | Python 3.14+ + PySide6 |
| jflove-app | `flutter doctor --verbose 2>&1 findstr "Android SDK"` | Android SDK 已配置 |
| jflove-web | `node --version` + `docker version 2>&1 findstr "Server"` | Node 22+ + Docker 已启动 |
| jflove-app R8 检查 | `findstr "isMinifyEnabled\s*=\s*true\|isShrinkResources\s*=\s*true" jflove-app\android\app\build.gradle.kts` | **无输出**（R8 必须关闭，见下方 ⚠️） |

> ⚠️ **移动端 R8 禁令（强制）**：`android/app/build.gradle.kts` 的 release buildType 中 `isMinifyEnabled` 和 `isShrinkResources` 必须为 `false`。Flutter 的 Dart 编译器已做 tree-shaking，再叠加 R8 的 `proguard-android-optimize.txt` 激进优化会破坏 Flutter 引擎和插件的反射/FFI 调用链，导致 release APK 闪退或白屏。**此项检查为发布阻塞项，不通过则阻断发布。**

**如果环境不满足，在版本发布记录中标记为"❌ 环境未就绪"并列出需安装的组件，不得跳过构建步骤直接输出"无变更"**。

> ⚠️ **Android licenses 自动接受**：`flutter doctor` 若提示 `licenses not accepted`，用以下命令自动回复 y（不要等用户手动输入）：
> ```powershell
> "y`ny`ny`ny`ny`ny`ny`ny`ny`ny" | flutter doctor --android-licenses 2>&1
> ```

## 构建规则（强制）

- **必须构建所有已开发模块**：
  - **jflove-server**：`cd jflove-server && python build.py` → Docker 镜像 `jflove-server:<version>` + `latest`
  - **jflove-desktop**：`cd jflove-desktop && python build.py` → PyInstaller 单文件 `build/dist/JFLove`
  - **jflove-app**：
    1. `cd jflove-app && flutter build apk --debug` → APK `build/app/outputs/flutter-apk/app-debug.apk`
    2. `cd jflove-app && flutter build apk` → APK `build/app/outputs/flutter-apk/app-release.apk`
    3. **两个 APK 都必须能在真机安装并正常启动**（发布前冒烟测试必须覆盖 release APK）
  - **jflove-web**：`cd jflove-web && python build.py` → Docker 镜像 `jflove-web:<version>`（可选 `--save` 导出 tar）
- 版本号核查（发布阻塞项）：
  - `jflove-server/src/main.py` `FastAPI(version=)`
  - `jflove-server/build.py` `VERSION`
  - `jflove-server/Dockerfile` `LABEL version=`
  - `jflove-desktop/src/config/settings.py` `APP_VERSION`
  - `jflove-desktop/build.py` `VERSION`
  - `jflove-app/pubspec.yaml` `version`
  - `jflove-web/package.json` `version`
- 生产库 DDL 变更必须有回滚脚本；Docker 镜像内置空表结构 DB，支持 `-v /data` 挂载持久化

## 发布步骤

1. 核查全部交付物归档
2. 7 处版本号字段一致性检查（服务端 3 + 桌面端 2 + 移动端 1 + Web 端 1）
3. §9 安全发布清单逐条通过
4. 生产库表结构同步（升级 DDL + 回滚脚本）
5. **环境检查**：执行「发布前环境检查」表格中的命令，确认各模块构建环境就绪
6. 构建服务端：`cd jflove-server && python build.py`
7. 构建桌面端：`cd jflove-desktop && python build.py`
8. 构建移动端（如启用）：
   - `cd jflove-app && flutter build apk --debug`（日常调试用）
   - `cd jflove-app && flutter build apk`（release APK，必须真机验证）
   - **如果构建失败，先 `flutter clean` 再重试**
   - **如果报 Gradle 锁冲突**：`taskkill /F /IM java.exe` → 删除 `android\.gradle` → 重试
9. 构建 Web 端 Docker 镜像（本地构建，**人工推送到镜像仓库**）：
   - `cd jflove-web && python build.py`（对标服务端 build.py，构建前自检 lock 文件）
   - 可选：`python build.py --save` 导出 `build/jflove-web-<version>.tar` 离线包
   - 构建失败排查：`package-lock.json` 是否缺失（先 `npm install`）、Node 版本、Docker daemon 状态
10. 冒烟测试：
   - 启动容器 → 客户端连接 → 密钥交换 → 登录 → 功能抽样
   - **Web 端**：`docker run -p 8080:80 jflove-web:<version>` → 浏览器访问 → 登录页渲染 → SPA 路由 fallback 正常
   - **移动端**：debug 和 release 两个 APK 都必须在真机安装并完成：启动 → 登录 → 文件浏览 → 笔记编辑
11. 输出 `文档记录/版本发布记录/<版本号>.md`

## 文档更新范围

- 路径：`文档记录/版本发布记录/<版本号>.md`
- 必须包含：版本号/发布时间、交付物清单（含 debug + release 两个 APK）、构建产物（路径+校验值）、依赖变更、生产库 DDL（升级+回滚）、部署步骤（含 Docker 启动命令）、冒烟结果、回滚预案、加密协议版本

## 安全宪法

详见 `AGENTS.md §9`。你的角色约束见 §9.6 表格 `devops` 行。发布前额外检查 `_PLAIN_PATHS` 白名单未扩大、`_session_store` 仍为内存字典。
