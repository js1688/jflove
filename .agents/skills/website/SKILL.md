---
name: website
description: "官网工程师，维护 JFLove 官方宣传站 / 文档站 / 下载站（独立模块 website/，Docker 镜像部署）。Use when: 用户明确说「更新官网」「官网发布」「官网改版」时人工触发；不对应产品版本流水线，禁止自动运行。"
---

# website

- 维护 JFLove **官网**：宣传首页、官方文档、API 公告、下载、各端获取与规格、各端安装指引、安全说明。
- 官网是**独立模块** `website/`，不参与 `AGENTS.md §7` 的版本流水线，**不被 devops 发布流程触发**。
- 模块边界：只写 `website/`（+ 本技能列明的例外），产品四端一行不改 —— 见 `AGENTS.md §1`。

## 触发契约（强制，不可放宽）

**只有**用户明确说出下列意图时才工作：`更新官网` / `官网改版` / `官网发布` / `把官网同步到 vX.Y.Z`。

**禁止**：

- 注册 `automation_create` / `schedule_create` 让官网「定期自动更新」；
- 被 `git tag`、产品发布、产品 commit 间接触发（官网 CI 不监听 tag，见 §7）；
- 在用户没有要求时"顺手"改官网；
- 用户只说「发版」时擅自更新官网 —— 那是 devops 的职责范围。

## 官网标准（最高约束，先读这个）

**站点骨架已定型，日常更新只改内容、不改骨架。**

> 完整标准见 `website/docs/SPEC.md`（骨架 / 导航 / 布局 / 各类页面必含内容 / 更新范围 / 截图标准）。
> **开工前必读**；与本文冲突时以 SPEC 为准（它更具体）。

### 定型的三件事（不得擅自改动）

| 项 | 定型内容 |
| --- | --- |
| **骨架** | 固定 8 个路由：`/` `/features` `/docs` `/api` `/api-announcements` `/download`（含安装） `/guide/{desktop,web,mobile}` `/security`；`/install` 仅重定向到 `/download` |
| **导航** | 顶栏固定 4 项（产品/功能/文档/下载）+ 版本号 + 下载按钮；页脚固定 4 列 |
| **首页布局** | Hero → 4 个产品场景 → 三端概览 → **资料入口只有 1 个区块** |

增删页面 / 改导航 / 改首页区块顺序都算**改骨架**：必须用户确认，并同步改 SPEC → 页面 → 设计稿 → 截图。

### 日常更新的范围（只做这些）

每次项目发版后，只更新 **内容**，顺序固定：

```bash
# 1. 先跑抽取（不改页面）
python website/scripts/extract_sources.py     # 特性 + 路由
python website/scripts/gen_api_docs.py        # 接口（OpenAPI）

# 2. 再跑门禁，红了修数据源，不要直接改页面
python scripts/check_website_sync.py
python scripts/check_ui_facts.py

# 3. 全绿后按需改页面内容
#    下载产物体积 / 操作指引文字 / 更新日志 / 截图
```

| 项目变了什么 | 官网要跟着改什么 |
| --- | --- |
| README「功能特性」 | 自动进 manifest，页面自动渲染（无需改页面） |
| 后端接口增删 | 同上（OpenAPI 管线自动） |
| 版本号 | `sync_version.py` 自动同步 |
| Release 产物 | `gen_releases.py` 自动取（文件名/体积/直链），**禁止手写**；门禁⑬会拦死链 |
| 三端 UI 变化 | 先改事实清单，再改指引，再重截截图 |
| 每版收尾 | 必产 `文档记录/官网记录/<版本>.md` |

### ⛔ 截图版本绑定是硬标准（用户明确要求，不可放行）

> **旧版本客户端的截图绝不允许作为操作指引发布。**

截图**没有自证版本的能力** —— 一张 v1.10.17 的界面和 v1.10.21 长得几乎一样。
实测踩过：本地 `app-release.apk` 是 v1.10.17，装上去截的图"看起来完全正确"，
但版本是过时的。

**机制**：`website/scripts/stamp_screenshots.py` 为每张图登记 `version` + `sha256`
→ `content/screenshots.json`；每端须有**版本锚点图**（`settings`，图上直接显示版本号）。

**三条铁律**：① 截图前核验客户端版本（APK `aapt dump badging`，产物 `grep 版本号`），
不等于 `version.json` 就重建；② 重截图后跑 `stamp_screenshots.py`；③ 升版本必须重拍
（⑫ **故意会红**，强迫发版重新取证）。细节见 `website/docs/SPEC.md` §9.1。

### 三端真实截图是硬性标准（不是可选项）

**Web / 桌面 / 移动三端都必须有当前版本的真实截图**，不能用示意图代替：

| 端 | 工具 | 关键前提 |
| --- | --- | --- |
| Web | `website/tools/shoot-product.mjs` | 需 `proxy-dist.py` 提供 SPA fallback + `/api` 反代 |
| 桌面 | `website/tools/shoot-desktop.py` | **必须设干净 `XDG_DATA_HOME`**（否则读到残留旧会话 → 401） |
| 移动 | `adb` + 模拟器 | **必须窗口化 + `-gpu host` + `Xvnc :99`** |

#### 移动端截图的正确姿势（已踩坑，照做即可）

```bash
source scripts/verify/env.sh
export ANDROID_HOME="$JF_ANDROID_HOME"
export PATH="$JF_FLUTTER_HOME/bin:$ANDROID_HOME/platform-tools:$ANDROID_HOME/emulator:$PATH"
DISP=$(jf_start_xvnc :99); jf_start_wm "$DISP"          # ① 真实显示（L49/L51）
DISPLAY=$DISP emulator -avd jflove-api36 -gpu host -no-audio -no-boot-anim \
  -no-snapshot -read-only >/tmp/jf-emulator.log 2>&1 &   # ② 窗口化 + -gpu host
adb wait-for-device && adb shell getprop sys.boot_completed
export JAVA_HOME="$HOME/文档/jdk/jdk-17.0.18"           # ③ 必须带 javac 的 JDK17
cd jflove-app && flutter build apk --release && cd ..
aapt dump badging .../app-release.apk | grep versionName  # 必须 == version.json
adb reverse tcp:8989 tcp:8989                            # ④ 设备访问宿主后端
adb shell uiautomator dump /sdcard/ui.xml                # ⑤ 取真实坐标，别按截图估
```

> 底部导航在 y≈2300（1080×2400）；键盘挡按钮时先 `input keyevent 111` 收起再点。

截图后**必须目视检查每张图**：不能是 401 / 加载失败 / 空白页 / 停在上一页，
账号要与预期一致。

> **两个实测坑**：
> ① 桌面端首批截图全是「[401] 令牌无效或已过期」—— 根因是本机残留 `vmtest` 会话，
>    修法：`XDG_DATA_HOME` 指向临时目录（`shoot-desktop.py` 已内置）。
> ② 移动端按截图像素估坐标点了 5 次全没反应 —— 底部导航实际在 y≈2300 而非 1795，
>    且每次截图文件大小几乎一样就是"没翻页"的信号，必须用 uiautomator 取真实 bounds。

## 发布时序（用户既定流程，不可颠倒）

> **先发产品，最后再更新官网。**

```
产品发版（devops：tag → CI → Release 产物就绪）
        ↓
官网更新（website 技能，人工触发）
```

**为什么不能反**：官网下载链接指向 GitHub Release 资产，Release 没产物时链接是死的；
截图也必须来自已发布的当前版本。

开工第一件事：确认四个资产都是 `state=uploaded`（命令见 `website/docs/SPEC.md` §11）。
**资产缺失 ⇒ 停下等产品发完**，不要先把官网推上去（会出现死链）。
下载链接由 `releases/download/v<版本>/<文件名>` 拼出，版本号取 `content/meta.json`。

## 开工前置（每次必做，缺一不可）

```bash
# 1. 版本对表：官网必须跟上项目当前版本
python -c "import json;print(json.load(open('version.json',encoding='utf-8'))['version'])"

# 2. 基线留痕：开工前记录工作区状态，收尾时逐条比对（防越界，AGENTS.md §6.1）
git status --porcelain

# 3. 读真身，不看二手总结
read README.md                          # 「功能特性」= 官网特性的唯一真相
read version.json
read 文档记录/版本发布记录/<最新版本>.md   # 本版用户可见变化
```

**任一步失败即停**，先把情况报告给用户，不要边猜边改。

## 目录白名单（硬边界）

```text
允许写：
  website/**                                    官网真身（页面 / 内容数据 / 设计稿 / 构建脚本）
  .agents/skills/website/**                     本技能自身
  文档记录/官网记录/<版本号>.md                  每轮更新对照表（唯一允许写的文档记录）
  scripts/check_website_sync.py                 官网门禁
  scripts/sync_version.py                       （仅在需要新增版本位置时，且必须复验 --check）
  website-deploy 相关 workflow                  （Step 4 新建后，仅官网自己那一条）

禁止写：
  jflove-server/  jflove-desktop/  jflove-web/  jflove-app/  jflove-db/
  文档记录/ 下除 官网记录/ 以外的任何文件
  .agents/skills/<其它技能>/  AGENTS.md  README.md  .gitignore  .git/
  其它任何 workflow
```

**收尾必须自证**：

```bash
git status --porcelain   # 白名单外出现任何改动 → 立即清理并向用户报告
```

## 数据来源（唯一真相，禁止手抄）

官网内容**不另存副本**，每次从项目真身现场解析。手抄一份 = 迟早过期。

| 内容 | 真身 | 抽取 |
| --- | --- | --- |
| 功能特性（名称 / 摘要 / 三端支持） | 根 `README.md` §「功能特性」（速览表 + 小节） | `website/content/features.json` |
| API 路由（方法 / 路径 / 摘要 / 分组） | `jflove-server/src/controllers/*.py` | `website/content/api-routes.json` |
| 明文白名单 | `jflove-server/src/main.py::_PLAIN_PATHS` | `website/content/meta.json` |
| 版本号 | 仓库根 `version.json` | `meta.json` + `sync_version.py` |
| 各端产物与体积 | GitHub Releases API（匿名可读） | 构建时拉取，不写死 |
| 接口清单（方法/路径/摘要/描述/分组） | `jflove-server` 的 `app.openapi()`（离线生成，不启服务） | `website/content/api-openapi.json` |
| 接口调用流程与加密细节 | `jflove-server/src/utils/{crypto,middleware}.py` + `services/auth_service.py` | `website/docs/api-contract.md` |

> **页面禁止硬编码**上述清单（判据 ⑥ 会拦）。特性 / 路由一律从 `website/content/*.json` 取数。
> 判据 ⑥ 从页面建立后才生效；页面一旦存在就必须消费 manifest。

## 操作指引：必须三端同时核对（强制，不可跳过）

> **只看一端就写指引 = 一定写错。** 三端的**菜单位置、按钮文案、交互模型**都不同。

### 已实测的坑（写指引前必读）

| 功能点 | 桌面端 | Web 端 | 移动端 |
| --- | --- | --- | --- |
| 上传按钮 | `上传` | `上传` | **`上传文件`**（图标 tooltip） |
| 文件操作 | 行尾三点菜单 | 行尾三点菜单 | **长按 → 底部弹层** |
| 无写权限 | 按钮消失 + `只读` 徽章 | 同左 | 按钮禁用 + `无写权限` |
| 主导航 | 侧栏 **7** | 侧栏 **4** | 底部 **6** |
| 桌面专属 | 云盘挂载 / 文本编辑器 / 远程连接 | ❌ | ❌ |
| 移动专属 | ❌ | ❌ | 相册同步 |
| 安全状态 | 独立菜单 | 独立菜单 | **在设置页内** |
| 设置分区 | **8** | **5**（PC） | **5** |
| 「服务端」叫法 | 服务端 | 服务端 | **服务器** |

> 完整版（含上传机制、拖拽、逐条证据）见 `website/docs/SPEC.md` §4.6 与 `ui-facts*.md`。

### 强制流程

1. **三端各读一遍源码**，不得用一端推断另一端：
   - 桌面：`jflove-desktop/ui/src/pages/**` + `layouts/DesktopLayout.tsx`
   - Web：`jflove-web/src/pages/**` + `layouts/DesktopLayout.tsx`
   - 移动：`jflove-app/lib/pages/**` + `lib/app.dart`（底部导航）
2. 每端产出一份事实清单：`website/docs/ui-facts-{desktop,web,mobile}.md`，**每条带 `文件:行`**。
3. 写完指引后跑两道门禁：
   ```bash
   python scripts/check_ui_facts.py              # ⑧ 清单里的每条文案必须真在源码里
   python scripts/check_website_sync.py          # ⑦ 指引里的「UI 文案」必须在清单里
   ```
   两道缺一不可：⑦ 保证「指引 ↔ 清单」一致，⑧ 保证「清单 ↔ 源码」一致。
   只有 ⑦ 会把写错的清单当基准放行。
4. **禁止**从一端复制段落改几个字充当另一端 —— ⑦ 会漏掉，但用户会踩坑。
5. 清单被 `scripts/check_ui_facts.py` 判红时，**先回源码核实该文案是否存在**：
   存在 ⇒ 修清单行号/写法；不存在 ⇒ 从清单删掉。**不要放宽校验**。

### 配图必须是真实产品截图（用户已确认）

- 指引页的每个关键步骤配 **当前版本实拍图**，不用手绘示意图代替。
- 截图必须在**同一浏览器会话内登录后逐页拍**（登录态不保留会全部跳回登录页）。
- 截图流程（工具已备好，`website/tools/`）：

```bash
# ① 起临时后端（复制 dev 库到 /tmp，绝不碰真实库）
bash scripts/verify/server.sh start
# ② 重建当前版本 dist（旧 dist 会拍出旧界面）
cd jflove-web && npx vite build && cd ..
# ③ 起静态服务（SPA fallback + /api 反代，缺一个登录就会失败）
python website/tools/proxy-dist.py --dist jflove-web/dist --port 8099 &
# ④ 登录一次，逐页截图
node website/tools/shoot-product.mjs --base http://127.0.0.1:8099 \
     --user admin --pass <临时口令> --out website/public/screenshots/web
```

- 收尾必须 `bash scripts/verify/server.sh stop` 并 kill 静态服务。
- **账号口令只用于 /tmp 副本库**，不得写进任何入库文件。

### 判断"同一功能"是否真的相同

问三个问题，任一不同就必须分端写：

- **入口在哪**？（侧栏 / 底部栏 / 设置内 / 长按）
- **叫什么**？（按钮文案可能只差一个字，但差一个字就找不到）
- **点了之后发生什么**？（原生对话框 / 浏览器选择器 / 系统选择器）

## 更新流程（8 步，顺序不可颠倒）

### 1. 对齐版本

```bash
python scripts/sync_version.py --scope website     # 官网三处版本号跟随 version.json
python scripts/sync_version.py --check             # 全部位置必须一致
```

### 2. 重新抽取内容（两条管线，都要跑）

```bash
python website/scripts/extract_sources.py   # 特性 + 路由（正则抽取）
python website/scripts/gen_api_docs.py      # 接口文档（从 app.openapi() 生成）
```

> 两条管线**互为交叉验证**：正则数装饰器、OpenAPI 读真实路由表，
> 任何一方漏条都会被门禁 ⑩ 差出来（历史上正是这类漏条让官网少写了 8 个接口）。

- 解析失败 ⇒ **修抽取口径，不得绕过**（说明项目结构变了，例如新增了无法识别的路由写法）。
- 抽取器内置兜底：`装饰器声明数 != 解析出的路由数` 直接报错，防静默漏条。

### 3. 门禁三道，全绿才继续

```bash
python scripts/check_website_sync.py               # ①~⑥（含 ②b）内容一致性（退出码 0）
python scripts/check_website_sync.py --self-test   # 门禁自证：喂坏数据必须被拦住（退出码 0）
python scripts/check_skills.py                     # 技能形态 + 体积（退出码 0）
python scripts/check_ui_facts.py                   # ⑧ 事实清单可溯源（退出码 0）
```

> 主门禁现为 **14 条**（①~⑬，含 ②b）。其中三条是本轮新增、容易忘的：
> ⑨ Logo 必须与产品逐字节一致｜⑩ OpenAPI 必须新鲜且与正则抽取交叉一致｜
> ⑪ 设计稿不得出现渐变/大圆角/外链/字母方块 Logo。

**改了版本号时，两步都要做**（否则 ②b 会红）：

```bash
python scripts/sync_version.py                     # 同步官网三处版本号
python website/scripts/extract_sources.py          # meta.json 的版本快照也要跟着重抽
```

### 4. 更新页面

- **本版有变化的**：功能特性、API、下载产物、安装步骤、安全说明。
- **全部内容无遗漏**：不因为"这页上一版没问题"就跳过核对 —— 逐页对 manifest。
- 文案与数据分离：文案可改，数据必须来自 manifest。
- 视觉改动必须遵守 `website/docs/design-spec.md`（Step 2 确认后建立）里的审美判据。

### 5. 本地实机验证（不可省略）

```bash
cd website && npm ci && npm run lint && npm run test && npm run build
```

```bash
# 双视口截图（复用项目既有 CDP 截图器，不引入 Playwright）
node ../scripts/verify/web_shot.mjs --url http://127.0.0.1:4173/ --out /tmp/site-desktop.png --width 1440 --height 900
node ../scripts/verify/web_shot.mjs --url http://127.0.0.1:4173/ --out /tmp/site-mobile.png  --width 390  --height 844 --mobile
```

- 截图必须**贴给用户看**，不能只写"已验证"。
- 门禁绿 ≠ 页面对：必须有人眼可判的证据。

### 6. 写更新对照表

`文档记录/官网记录/<版本号>.md`，格式固定：

```markdown
# 官网更新对照表 v<版本号>

| # | 项目变化（真身证据） | 官网改动（页面/文件） | 判据 |
| --- | --- | --- | --- |
| 1 | `README.md:120` 新增「XXX」特性 | `src/pages/Features.tsx` 自动渲染（manifest 驱动） | 门禁③ |
```

**要求**：能让人 30 秒核完"项目改了什么 → 官网改了哪里"。

### 7. 询问用户是否触发 CICD（强制，不可跳过）

按用户既定流程（与产品发布一致）：

```
更新完成 → 主动询问「是否触发官网 CICD？」
        → 用户同意 → git add/commit/push（仅在用户同意后）
        → 触发 website-deploy workflow
        → 等待构建结果
        → 拉回产物验证
```

- **git 写操作必须用户逐次同意**（`AGENTS.md §6.5 E`），不得自行 commit/push/tag。
- 用户不同意 ⇒ 停在工作区，交付"本地已验证"状态。

### 8. 回验构建产物（阻塞项，未通过不算完成）

```bash
# 等 CI 绿后，拉回镜像并实测
docker pull ghcr.io/js1688/jflove-website:<版本号>
docker run -d --rm -p 18081:80 ghcr.io/js1688/jflove-website:<版本号>

# 逐路由状态码 + 双视口截图
for p in / /features /docs /api /download /install /security; do
  printf '%-12s %s\n' "$p" "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18081$p)"
done
node scripts/verify/web_shot.mjs --url http://127.0.0.1:18081/ --out /tmp/ci-site.png
```

- 只验"CI 绿了"不算完成 —— 必须验**构建产物本身**（与产品发布回验同一口径，`AGENTS.md §7.3 Phase 8 第 7 步`）。
- 回验发现问题的处置：修 → 递增版本（或按项目约定的修复通道）→ 重跑。**严禁移动已推送的 tag**。

## 版本号一致性（强制）

- 唯一真相 = 仓库根 `version.json`；官网**没有独立版本号**。
- 三处同步位置：`website/package.json`（`version`）、`website/package-lock.json`（`version`，含 `packages[""].version`）、`website/src/config/constants.ts`（`APP_VERSION`）。
- 镜像 tag 与 Release 版本一致：`ghcr.io/js1688/jflove-website:<version>`。
- 禁止手改这三处；改版本只能 `echo '{"version":"x.y.z"}' > version.json && python scripts/sync_version.py`。

## CI/CD 与产品 CI 的隔离（强制）

| | 产品 CI（5 条既有 workflow） | 官网 CI（`website-deploy.yml`） |
| --- | --- | --- |
| 触发 | `push: tags: v*` | `workflow_dispatch` + `push: branches:[main] paths:[website/**]` |
| 打 tag 时 | 全部触发 | **不触发**（`branches` 不匹配 tag 事件） |
| 改产品代码 | — | **不触发**（`paths` 不匹配） |
| 产物 | 4 类安装包 + 2 个 GHCR 镜像 | `ghcr.io/js1688/jflove-website:<ver>` + `latest` |

**隔离性必须可证**，每次改动该 workflow 后自证：

```bash
# ① 断言不含 tag 触发器（输出为空才算通过）
grep -nE "tags:|'v\*'" .github/workflows/website-deploy.yml || echo "OK：不监听 tag"

# ② 打 tag 时 Actions 里不应出现官网 job（人工在 Actions 页确认一次即可）
```

## 设计确认（Step 2 起）

- 视觉方案**必须先给用户看稿件**：单文件 HTML（零构建依赖，双击可开）+ 截图。
- 至少 2~3 套方案对比，逐轮收敛；用户书面选定后才进入建站。
- 确认后的审美判据写入 `website/docs/design-spec.md`，后续每轮按它核对，**不靠"我觉得好看"**。

## 常见问题
门禁红了怎么修 → 见 `website/docs/SPEC.md` §12。

## 安全宪法约束（不可绕过）

- 官网**不是**产品的一部分，**不得**实现任何产品接口调用、也不得明文展示任何凭据 / token / 密钥。
- 官网展示的接口信息**只来自** `api-routes.json`（从代码抽取），不得凭记忆写接口文档。
- 官网镜像只暴露 `:80`；TLS / 反向代理 / 域名由部署侧负责（见 `AGENTS.md` 官网章节）。

## 文档职责边界

| 内容 | 唯一去处 |
| --- | --- |
| 官网技术说明（目录 / 构建 / 部署） | `website/README.md` |
| 每轮官网更新对照表 | `文档记录/官网记录/<版本号>.md` |
| 视觉规范（确认后的审美判据） | `website/docs/design-spec.md` |
| 本技能规则 | `.agents/skills/website/SKILL.md`（本文件） |

> **不得**引用 `plans/` 下任何文件（`AGENTS.md §1.1.1`：plans 不入库，跨设备即悬空）。
