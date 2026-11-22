import { Link } from 'react-router';
import {
  features,
  meta,
  openapi,
  platforms,
  REPO_URL,
  supportCount,
  supports,
} from '../content';

/**
 * 首页：**产品展示为主**（design-spec P1）。
 *
 * 结构（自上而下）：
 *   Hero（价值主张 + 双 CTA + 真实统计 + 产品界面）
 *   → 4 个产品场景（每个配界面示意）
 *   → 三端概览
 *   → 资料入口（只有这一个区块，4 张卡）
 *
 * 所有数字来自 content/*.json；产品界面示意图用纯 CSS 绘制（不依赖截图，随代码演进不失效）。
 */
export function HomePage() {
  const featureCount = features.count;
  const apiCount = openapi.total;

  return (
    <>
      {/* ── Hero ── */}
      <section className="border-b border-line">
        <div className="mx-auto grid max-w-[1140px] items-center gap-16 px-7 py-20 lg:grid-cols-[1fr_460px]">
          <div>
            <span className="mb-5 inline-flex items-center gap-1.5 rounded-full bg-brand-50 px-3 py-1 text-[12.5px] font-[560] text-brand-600">
              v{meta.version} · 已发布 · MIT 开源
            </span>
            {/* 窄屏字号下调，避免「传输」被拆行；强制换行只在 sm 以上生效 */}
            <h1 className="mb-4 text-[28px] leading-[1.28] font-[680] tracking-[-0.022em] sm:text-[34px] lg:text-[40px] lg:leading-[1.24]">
              把文件、笔记与传输
              <br className="hidden sm:block" />
              放在<span className="text-brand-600">一个客户端</span>里
            </h1>
            <p className="mb-7 max-w-[520px] text-[16px] text-ink-700">
              自建的端到端加密私有云：虚拟磁盘映射你自己的目录，桌面 / Web / 移动三端同源，
              传文件、写笔记、挂离线下载、连远程主机，都不用再换工具。
            </p>
            <div className="flex flex-wrap gap-3">
              <Link
                to="/download"
                className="rounded-[7px] bg-brand-500 px-5 py-2.5 text-[14px] font-[520] text-white transition-colors hover:bg-brand-600"
              >
                免费下载
              </Link>
              <Link
                to="/features"
                className="rounded-[7px] border border-line px-5 py-2.5 text-[14px] font-[520] text-ink-700 transition-colors hover:border-brand-500 hover:text-brand-600"
              >
                看看能做什么
              </Link>
              <a
                href={REPO_URL}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1.5 rounded-[7px] border border-line px-5 py-2.5 text-[14px] font-[520] text-ink-700 transition-colors hover:border-brand-500 hover:text-brand-600"
              >
                <GitHubIcon />
                开源仓库
              </a>
            </div>
            <div className="mt-9 flex flex-wrap gap-8 border-t border-line pt-6">
              <Stat value={featureCount} label="项能力" />
              <Stat value={apiCount} label="个 API" />
              <Stat value={3} label="种客户端形态" />
              <Stat value="MIT" label="开源许可" />
            </div>
          </div>

          <AppWindow />
        </div>
      </section>

      {/* ── 产品场景 ── */}
      <section className="border-b border-line py-16">
        <div className="mx-auto max-w-[1140px] px-7">
          <SectionHead
            eyebrow="核心场景 01"
            title="文件管理"
            sub="挂上自己的目录，像本地一样用"
            desc="一个虚拟磁盘 = 服务器上的一个真实目录。树形浏览、拖拽上传、断点续传、多格式预览，大文件也不怕中途断网。"
            bullets={[
              '虚拟磁盘：完全依赖文件系统结构，不在数据库里存一份目录信息',
              '多格式预览：图片 / 视频 / 音频 / 文本 / Markdown',
              '端到端加密：所有传输走加密通道',
            ]}
            flip={false}
          />
          <SectionHead
            eyebrow="核心场景 02"
            title="笔记管理"
            sub="写 Markdown，右边即时成形"
            desc="直接读写服务端上的 .md 文件，左写右看。代码块高亮、Mermaid 图表、搜索标签收藏一样不缺。"
            bullets={[
              '实时预览：GitHub 风格 CSS，表格、代码块、引用都按标准渲染',
              'Mermaid：画流程图、时序图、甘特图，语法错误给中文提示',
              '按人隔离：每个账号在设置里配置自己的笔记目录，互相不可见',
            ]}
            flip
          />
          <SectionHead
            eyebrow="核心场景 03"
            title="离线下载"
            sub="贴个网址，勾选就进你的磁盘"
            desc="把无需登录的网页地址贴进来，服务端分析出视频 / 音频 / 图片清单，勾选后直接下载到你账号的磁盘目录。"
            bullets={[
              '三级降级分析：站点解析 → 静态抓取 → 无头浏览器兜底',
              '自动补音轨：音视频分离的站点自动合并成带音轨的 mp4',
              '产物直接进文件管理，不用再拖来拖去',
            ]}
            flip={false}
          />
          <SectionHead
            eyebrow="核心场景 04"
            title="安全通信"
            sub="连服务器这条路，本身是密的"
            desc="除 3 条明文白名单外，所有请求、成功响应、失败响应全部走 ChaCha20-Poly1305 信封。每次连接都临时协商密钥，用完即销毁。"
            bullets={[
              '前向保密：X25519 ECDH + HKDF-SHA256，会话密钥只存内存',
              '文件流加密：下载按 64KB 分片，每片独立加密成帧',
              'URL 不带明文参数：业务参数只在加密 body 里',
            ]}
            flip
          />
        </div>
      </section>

      {/* ── 三端概览 ── */}
      <section className="border-b border-line py-16">
        <div className="mx-auto max-w-[1140px] px-7">
          <SectionTitle
            title="一个账号，三种形态"
            sub="同一套后端与协议。桌面端重能力，浏览器最轻量，手机随身带。"
          />
          <div className="mt-8 grid gap-4 md:grid-cols-3">
            <PlatformCard
              name="桌面客户端"
              meta="Windows / Linux (Fedora)"
              desc="功能最全：本地文本编辑器、云盘挂载、远程终端都只在这里。"
              highlight={`${supportCount(platforms[0])}/${features.count} 项能力`}
            />
            <PlatformCard
              name="Web 端"
              meta="PC 与移动端浏览器"
              desc="浏览器打开即用，免安装；保留文件、笔记、传输、离线下载。"
              highlight={`${supportCount(platforms[2] ?? platforms[platforms.length - 1])}/${features.count} 项能力`}
            />
            <PlatformCard
              name="移动端"
              meta="Android（iOS / 鸿蒙预留）"
              desc="随身看文件、写笔记、发起离线下载；相册备份是它独有的能力。"
              highlight={`${supportCount(platforms[1])}/${features.count} 项能力`}
            />
          </div>

          {/* 三端支持矩阵 */}
          <div className="mt-6 overflow-x-auto rounded-[10px] border border-line">
            <table className="w-full text-[13px]">
              <thead className="bg-canvas-soft text-[12px] text-ink-500">
                <tr>
                  <th className="px-4 py-2.5 text-left font-[560]">能力</th>
                  {platforms.map((p) => (
                    <th key={p} className="px-4 py-2.5 text-center font-[560]">
                      {p}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {features.features.map((f) => (
                  <tr key={f.name} className="border-t border-line">
                    <td className="px-4 py-2.5">{f.name}</td>
                    {platforms.map((p) => (
                      <td key={p} className="px-4 py-2.5 text-center">
                        {supports(f, p) ? (
                          <span className="text-brand-600">✓</span>
                        ) : (
                          <span className="text-ink-300">—</span>
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      {/* ── 资料入口（唯一的入口区块，必须显眼）── */}
      <section className="py-16">
        <div className="mx-auto max-w-[1140px] px-7">
          <SectionTitle
            title="要动手了？"
            sub="四个入口，各自一页。产品说明留在上面，这里只给要看文档的人带路。"
          />
          <div className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <DocCard
              to="/download"
              title="下载与安装"
              desc="Windows 安装包与便携版、Fedora RPM、Android APK 直接点下载；服务端 Docker 一行起。"
              cta="去下载"
            />
            <DocCard
              to="/guide/web"
              title="操作指引"
              desc="逐菜单逐按钮的实操说明，附三端菜单差异对照表。"
              cta="进操作指引"
            />
            <DocCard
              to="/api"
              title="API 文档"
              desc={`${apiCount} 个接口按模块分组，含加密信封与调用流程。`}
              cta="看接口清单"
            />
            <DocCard
              to="/api-announcements"
              title="API 公告"
              desc={`当前版本 v${meta.version} 的接口变化与升级注意事项。`}
              cta="看本版变化"
            />
          </div>
        </div>
      </section>
    </>
  );
}

function Stat({ value, label }: { value: number | string; label: string }) {
  return (
    <div>
      <b className="tnum block text-[20px] font-[660] tracking-[-0.02em]">{value}</b>
      <span className="text-[12.5px] text-ink-500">{label}</span>
    </div>
  );
}

function SectionHead({
  eyebrow,
  title,
  sub,
  desc,
  bullets,
  flip,
}: {
  eyebrow: string;
  title: string;
  sub: string;
  desc: string;
  bullets: string[];
  flip: boolean;
}) {
  return (
    <div
      className={`mb-14 grid items-center gap-12 lg:grid-cols-2 ${flip ? 'lg:[&>*:first-child]:order-2' : ''}`}
    >
      <div>
        <div className="mb-3 text-[12.5px] font-[560] text-brand-600">{eyebrow}</div>
        <h2 className="mb-1 text-[24px] font-[660] tracking-[-0.018em]">{title}：</h2>
        <h3 className="mb-3 text-[24px] font-[660] tracking-[-0.018em]">{sub}</h3>
        <p className="mb-5 max-w-[480px] text-[14px] text-ink-700">{desc}</p>
        <ul className="space-y-2">
          {bullets.map((b) => (
            <li key={b} className="flex gap-2 text-[13.5px] text-ink-700">
              <span className="mt-0.5 text-brand-500">✓</span>
              <span>{b}</span>
            </li>
          ))}
        </ul>
      </div>
      <MockPanel title={title} />
    </div>
  );
}

function SectionTitle({ title, sub }: { title: string; sub: string }) {
  return (
    <div>
      <h2 className="text-[24px] font-[660] tracking-[-0.018em]">{title}</h2>
      <p className="mt-1.5 text-[13.5px] text-ink-500">{sub}</p>
    </div>
  );
}

function PlatformCard({
  name,
  meta,
  desc,
  highlight,
}: {
  name: string;
  /** 平台说明（如「Windows / Linux」），非内容层 meta */
  meta: string;
  desc: string;
  highlight: string;
}) {
  return (
    <div className="rounded-[10px] border border-line p-5 transition-colors hover:border-brand-300">
      <div className="mb-1 flex items-center justify-between gap-2">
        <h3 className="text-[15px] font-[620]">{name}</h3>
        <span className="tnum rounded bg-brand-50 px-2 py-0.5 text-[11px] text-brand-600">
          {highlight}
        </span>
      </div>
      <div className="mb-2 text-[11.5px] text-ink-500">{meta}</div>
      <p className="text-[13px] text-ink-700">{desc}</p>
    </div>
  );
}

function DocCard({
  to,
  title,
  desc,
  cta,
}: {
  to: string;
  title: string;
  desc: string;
  cta: string;
}) {
  return (
    <Link
      to={to}
      className="group flex flex-col rounded-[10px] border border-line p-5 transition-colors hover:border-brand-500"
    >
      <b className="mb-2 text-[14px] font-[620]">{title}</b>
      <p className="mb-3 flex-1 text-[12.8px] leading-relaxed text-ink-500">{desc}</p>
      <span className="text-[12.5px] font-[540] text-brand-600">
        {cta} <span aria-hidden>→</span>
      </span>
    </Link>
  );
}

/**
 * 产品界面示意：纯 CSS 绘制的客户端窗口。
 *
 * 为什么不用真截图：首页要表达「产品长这样」，用示意图能随信息架构演进保持准确；
 * 具体操作步骤在 `/guide/*` 里用**真实截图**（design-spec §6）。
 */
function AppWindow() {
  const menu = ['文件管理', '笔记管理', '文本编辑器', '云盘挂载', '传输任务', '离线下载', '远程连接'];
  const files = [
    ['发布说明.md', '12.4 KB'],
    ['界面预览.png', '248 KB'],
    ['演示视频.mp4', '84.1 MB'],
    ['归档包.zip', '1.6 GB'],
  ];

  return (
    <div className="overflow-hidden rounded-[10px] border border-line shadow-[0_1px_2px_rgba(16,24,32,0.04)]">
      <div className="flex items-center gap-1.5 border-b border-line bg-white px-3 py-2.5">
        <i className="block h-2.5 w-2.5 rounded-full bg-[#f0b3ae]" />
        <i className="block h-2.5 w-2.5 rounded-full bg-[#ead9a8]" />
        <i className="block h-2.5 w-2.5 rounded-full bg-[#aedcc8]" />
        <span className="ml-2 text-[11.5px] text-ink-500">JFLove · 文件管理</span>
      </div>
      <div className="grid grid-cols-[132px_1fr]">
        <div className="border-r border-line bg-canvas-soft px-2 py-2.5">
          <div className="mb-2 flex items-center gap-1.5 px-1.5">
            <img src="/logo-64.png" alt="" width={16} height={16} className="rounded" />
            <b className="text-[11.5px]">JFLove</b>
          </div>
          {menu.map((m, i) => (
            <div
              key={m}
              className={`mb-0.5 truncate rounded px-1.5 py-1 text-[11.5px] ${
                i === 0 ? 'bg-brand-50 font-[560] text-brand-600' : 'text-ink-700'
              }`}
            >
              {m}
            </div>
          ))}
        </div>
        <div className="bg-white p-3">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-[11.5px] font-[560]">我的磁盘 / 项目资料</span>
            <span className="flex gap-1.5">
              <span className="rounded bg-brand-500 px-2 py-0.5 text-[10.5px] text-white">上传</span>
              <span className="rounded border border-line px-2 py-0.5 text-[10.5px] text-ink-700">
                新建目录
              </span>
            </span>
          </div>
          {files.map(([n, s]) => (
            <div
              key={n}
              className="flex items-center justify-between border-b border-line py-1.5 text-[11.5px] last:border-0"
            >
              <span className="truncate text-ink-700">{n}</span>
              <span className="tnum shrink-0 text-ink-500">{s}</span>
            </div>
          ))}
          <div className="mt-2.5">
            <div className="mb-1 flex justify-between text-[10.5px] text-ink-500">
              <span>备份.tar.gz</span>
              <span className="tnum">62%</span>
            </div>
            <div className="h-1 overflow-hidden rounded-full bg-line">
              <div className="h-full w-[62%] rounded-full bg-brand-500" />
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/** 场景配图：不同场景给不同颜色的示意骨架，避免四张图一模一样 */
function MockPanel({ title }: { title: string }) {
  const bars = [72, 54, 88, 40, 66];
  return (
    <div className="rounded-[10px] border border-line bg-canvas-soft p-4">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-[12px] font-[560] text-ink-700">{title} · 界面示意</span>
        <span className="flex gap-1.5">
          <i className="block h-2 w-2 rounded-full bg-line" />
          <i className="block h-2 w-2 rounded-full bg-line" />
          <i className="block h-2 w-2 rounded-full bg-line" />
        </span>
      </div>
      <div className="space-y-2">
        {bars.map((w, i) => (
          <div key={i} className="flex items-center gap-2">
            <div className="h-2 w-2 shrink-0 rounded-sm bg-brand-100" />
            <div
              className="h-2 rounded-sm bg-line"
              style={{ width: `${w}%` }}
              aria-hidden
            />
          </div>
        ))}
      </div>
      <div className="mt-3 flex gap-1.5">
        <div className="h-1.5 flex-1 rounded-full bg-brand-500/80" />
        <div className="h-1.5 w-1/3 rounded-full bg-line" />
      </div>
    </div>
  );
}

/** GitHub 图标（内联 SVG） */
function GitHubIcon() {
  return (
    <svg viewBox="0 0 16 16" width="15" height="15" fill="currentColor" aria-hidden>
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}
