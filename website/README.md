# JFLove 官网（website）

> **本目录是官网的唯一真身**，独立于四个产品模块（`jflove-server` / `jflove-desktop` / `jflove-web` / `jflove-app`）。
> 官网内容的更新由 `.agents/skills/website/SKILL.md` 技能执行，**必须人工触发**，不参与产品版本流水线。

## 与其他模块的关系

| | 关系 |
| --- | --- |
| 版本号 | **与项目版本号强制一致**，由仓库根 `version.json` 经 `scripts/sync_version.py --scope website` 派生 |
| 内容来源 | 根 `README.md`「功能特性」+ controllers 路由 + `app.openapi()` + GitHub Releases —— **只读抽取，不复制粘贴** |
| 构建产物 | `ghcr.io/js1688/jflove-website:<版本号>`（Docker 镜像），由 `.github/workflows/website-deploy.yml` 独立触发（**不监听 tag**） |
| 触发方式 | 技能人工触发 → 询问用户 → 用户同意 → 提交并推送 → 独立 CI 构建 → 拉回产物验证 |

## 目录结构

```text
website/
├── package.json / tsconfig.json / vite.config.ts / vitest.config.ts
├── index.html                # SPA 入口
├── Dockerfile                # 多阶段：node 构建 → nginx 托管（只暴露 :80）
├── nginx.conf                # SPA fallback + 静态缓存 + 安全头 + gzip
├── content/                  # 构建期 import 的数据（唯一数据源）
│   ├── features.json         # 功能特性（README「功能特性」）
│   ├── api-routes.json       # 路由（正则抽取 controllers）
│   ├── api-openapi.json      # 接口文档（app.openapi() 生成）
│   ├── releases.json         # 下载产物（GitHub Release 生成，禁止手写）
│   ├── screenshots.json      # 截图版本台账（version + sha256）
│   └── meta.json             # 版本号 / 明文白名单 / 来源
├── docs/                     # 事实与规范（构建期不打包，供人读 + 门禁核对）
│   ├── ui-facts*.md          # 三端 UI 事实清单（1179 条，逐条带 文件:行）
│   ├── guides-{desktop,web,mobile}.md  # 三端操作指引正文
│   ├── api-contract.md       # 五步调用流程 + 加密细节
│   └── design-spec.md        # 可判定的审美判据
├── scripts/
│   ├── extract_sources.py    # 抽取特性与路由
│   └── gen_api_docs.py       # 从 OpenAPI 生成接口数据
├── tools/
│   ├── shoot-product.mjs     # 同会话登录后逐页截真实产品图
│   └── proxy-dist.py         # 截图用静态服务（SPA + /api 反代）
├── src/
│   ├── content/index.ts      # 内容层：页面只能从这里取数
│   ├── layouts/AppLayout.tsx # 顶栏 + 页脚
│   ├── components/ui.tsx     # 通用组件
│   └── pages/                # 9 个页面
├── tests/content.test.ts     # 内容契约测试（14 条）
├── public/                   # logo + 真实产品截图
└── design/                   # 设计稿（v2 现行 / _archive-v1 归档）
```

## 内容管线（强制，顺序不可颠倒）

```bash
PY=jflove-server/venv-linux/bin/python

# 1. 从项目真身重新抽取（三条管线，互为交叉验证）
$PY website/scripts/extract_sources.py     # 特性 + 路由（正则）
$PY website/scripts/gen_api_docs.py        # 接口文档（app.openapi()）
$PY website/scripts/gen_releases.py        # 下载产物（GitHub Release）

# 2. 门禁（退出码即判据）
$PY scripts/check_website_sync.py          # 14 条（①~⑬，含 ②b）
$PY scripts/check_website_sync.py --self-test
$PY scripts/check_ui_facts.py              # 1179 条文案溯源到源码
```

**任何一步非 0，官网更新视为失败**，不得进入提交阶段。

## 本地开发与验证

```bash
cd website
npm ci
npx tsc -b          # 类型检查
npm run lint        # ESLint（0 警告）
npm run test        # vitest（14 passed）
npm run build       # 产出 dist/
npm run preview     # 本地预览 http://127.0.0.1:4173
```

**一条命令端到端验证**（构建镜像 + 起容器 + 8 项检查 + 双视口截图）：

```bash
bash website/scripts/verify-site.sh              # 全流程
bash website/scripts/verify-site.sh --no-build   # 复用已有镜像
```

检查项：版本号 / 14 条门禁 / 镜像构建 / 11 条路由 / `/install` 重定向 /
下载链接指向真实 Release / 面包屑可点 / 双视口截图。退出码 0 即全部通过。

双视口实机截图（复用项目既有 CDP 截图器，不引入 Playwright）：

```bash
node ../scripts/verify/web_shot.mjs --url http://127.0.0.1:4173/ \
     --out /tmp/site-desktop.png --width 1440 --height 900
node ../scripts/verify/web_shot.mjs --url http://127.0.0.1:4173/ \
     --out /tmp/site-mobile.png  --width 390 --height 844 --mobile
```

## 门禁覆盖什么（12 条）

| # | 判据 | 失败含义 |
| --- | --- | --- |
| ① | manifest 与实时解析项目一致 | 官网内容陈旧 |
| ② | 版本号三处一致 | 违反版本号一致性约束 |
| ②b | `meta.json` 版本快照 == `version.json` | 改了版本却没重跑抽取器 |
| ③ | 特性表行数 == 小节数 == manifest 数 | 有特性被遗漏 |
| ④ | controller 装饰器数 == manifest 路由数 | API 文档有遗漏 |
| ⑤ | `_PLAIN_PATHS` 与安全页一致 | 安全说明与实现漂移 |
| ⑥ | 页面消费 manifest 而非硬编码 | 页面绕开数据源 |
| ⑦ | 指引里的 UI 文案在事实清单里 | 指引臆造按钮 |
| ⑧ | 事实清单里的文案在源码里 | 清单本身写错 |
| ⑨ | 官网 Logo 与产品逐字节一致 | 品牌标识漂移 |
| ⑩ | OpenAPI 新鲜且与正则抽取交叉一致 | 接口文档落后 |
| ⑪ | 设计稿无渐变/大圆角/外链/字母方块 Logo | 违反已确认的视觉规范 |
| ⑫ | 截图绑定当前版本 + 每端有版本锚点图 | 旧版本截图被当指引发布 |
| ⑬ | 下载产物体积/文件名与 Release 一致 | 下载按钮死链 |

## 部署

```bash
# 本地构建并自测
docker build -f website/Dockerfile -t jflove-website:1.10.21 .
docker run -d --rm -p 18080:80 jflove-website:1.10.21

# 逐路由验证
for p in / /features /docs /api /download /install /guide/web /security; do
  printf '%-14s %s\n' "$p" "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:18080$p)"
done
```

镜像只暴露 `:80`。**域名 / HTTPS / 反向代理由部署侧决定**，不写进镜像。

## 记录

每轮官网更新产出一份对照表：`文档记录/官网记录/<版本号>.md`
（项目改了什么 → 官网改了哪一页 → 证据），供 30 秒内核对。

## 已知的产品侧问题（不属官网范围）

| # | 现象 | 位置 |
| --- | --- | --- |
| P-1 | 嵌套路由下侧栏 Logo 破图（相对路径 `./logo-128.png`） | `jflove-web/src/layouts/DesktopLayout.tsx:101` |
| P-2 | 登录页同样用相对路径 | `jflove-web/src/pages/LoginPage.tsx:113` |
| P-3 | `/docs`、`/openapi.json` 公网可访问（用户已明确保持现状） | `jflove-server/src/main.py` |

详见 `文档记录/官网记录/v1.10.21.md` §6。
