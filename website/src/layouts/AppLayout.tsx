import { Link, NavLink, Outlet } from 'react-router';
import { ISSUES_URL, REPO_URL, siteVersion } from '../content';

/**
 * 全站布局：顶栏 + 内容 + 页脚。
 *
 * 导航项固定 4 个（design-spec §8）；当前项用蒂芙尼蓝下划线标记。
 * 版本号常驻顶栏，来源是 `content/meta.json`（由 version.json 派生），禁止硬编码。
 */

const NAV = [
  { to: '/', label: '产品', end: true },
  { to: '/features', label: '功能', end: false },
  { to: '/docs', label: '文档', end: false },
  { to: '/download', label: '下载', end: false },
];

const FOOT_LINKS: { title: string; items: { to: string; label: string }[] }[] = [
  {
    title: '产品',
    items: [
      { to: '/features', label: '功能特性' },
      { to: '/download', label: '下载' },
      { to: '/download', label: '下载与安装' },
    ],
  },
  {
    title: '文档',
    items: [
      { to: '/docs', label: '文档中心' },
      { to: '/api', label: 'API 参考' },
      { to: '/api-announcements', label: 'API 公告' },
    ],
  },
  {
    title: '指引',
    items: [
      { to: '/guide/web', label: 'Web 端指引' },
      { to: '/guide/desktop', label: '桌面端指引' },
      { to: '/guide/mobile', label: '移动端指引' },
    ],
  },
  {
    title: '关于',
    items: [
      { to: '/security', label: '安全说明' },
      { to: '/docs#changelog', label: '更新日志' },
    ],
  },
];

/** 页脚里需要外链的项（GitHub / 问题反馈），单独渲染 */
const FOOT_EXTERNAL: { label: string; href: string }[] = [
  { label: 'GitHub 仓库', href: REPO_URL },
  { label: '问题反馈', href: ISSUES_URL },
  { label: 'Releases', href: `${REPO_URL}/releases` },
];

export function AppLayout() {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="sticky top-0 z-20 border-b border-line bg-white/92 backdrop-blur">
        <div className="mx-auto flex h-[62px] max-w-[1140px] items-center gap-9 px-7">
          <Link to="/" className="flex items-center gap-2.5 text-[15.5px] font-[680] tracking-tight">
            <img src="/logo-64.png" alt="JFLove" width={24} height={24} className="rounded-[5px]" />
            JFLove
          </Link>
          <nav className="hidden items-center gap-6.5 text-[14px] text-ink-700 sm:flex">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `border-b-2 pb-0.5 transition-colors ${
                    isActive
                      ? 'border-brand-500 font-[560] text-ink-900'
                      : 'border-transparent hover:text-brand-600'
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3.5">
            <span className="tnum rounded border border-line px-2 py-0.5 text-[12.5px] text-ink-500">
              v{siteVersion}
            </span>
            <a
              href={REPO_URL}
              target="_blank"
              rel="noreferrer"
              title="开源仓库"
              aria-label="GitHub 开源仓库"
              className="hidden items-center gap-1.5 rounded-[7px] border border-line px-3 py-2 text-[13px] text-ink-700 transition-colors hover:border-brand-500 hover:text-brand-600 sm:inline-flex"
            >
              <GitHubIcon />
              GitHub
            </a>
            <Link
              to="/download"
              className="rounded-[7px] bg-brand-500 px-4 py-2 text-[13.5px] font-[520] text-white transition-colors hover:bg-brand-600"
            >
              下载客户端
            </Link>
          </div>
        </div>
      </header>

      <main className="flex-1">
        <Outlet />
      </main>

      <footer className="border-t border-line bg-canvas-soft">
        <div className="mx-auto max-w-[1140px] px-7 py-10">
          <div className="flex flex-wrap justify-between gap-9">
            <div className="text-[12.8px] text-ink-500">
              <div className="mb-2 flex items-center gap-2 text-[13px] font-[600] text-ink-900">
                <img src="/logo-64.png" alt="" width={18} height={18} className="rounded" />
                JFLove
              </div>
              <p className="max-w-[260px] leading-relaxed">
                端到端加密的个人数字工作台。数据只存在你自己的磁盘里。
              </p>
              <p className="mt-2">MIT License</p>
            </div>
            {FOOT_LINKS.map((col) => (
              <div key={col.title} className="text-[12.8px]">
                <div className="mb-2 font-[600] text-ink-900">{col.title}</div>
                {col.items.map((item) => (
                  <Link
                    key={item.to + item.label}
                    to={item.to}
                    className="mb-1.5 block text-ink-500 transition-colors hover:text-brand-600"
                  >
                    {item.label}
                  </Link>
                ))}
              </div>
            ))}
            <div className="text-[12.8px]">
              <div className="mb-2 font-[600] text-ink-900">开源</div>
              {FOOT_EXTERNAL.map((item) => (
                <a
                  key={item.href}
                  href={item.href}
                  target="_blank"
                  rel="noreferrer"
                  className="mb-1.5 block text-ink-500 transition-colors hover:text-brand-600"
                >
                  {item.label}
                </a>
              ))}
            </div>
          </div>
          <div className="tnum mt-8 flex flex-wrap justify-between gap-2.5 border-t border-line pt-5 text-[12px] text-ink-500">
            <span>© 2026 JFLove · 本站版本 v{siteVersion}（与项目版本一致）</span>
            <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
              <a href={REPO_URL} target="_blank" rel="noreferrer" className="hover:text-brand-600">
                github.com/js1688/jflove
              </a>
              <span aria-hidden className="text-ink-300">·</span>
              <span>ghcr.io/js1688/jflove-website:{siteVersion}</span>
            </span>
          </div>
        </div>
      </footer>
    </div>
  );
}

/** GitHub 图标（内联 SVG，不引外部资源） */
function GitHubIcon() {
  return (
    <svg viewBox="0 0 16 16" width="14" height="14" fill="currentColor" aria-hidden>
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27s1.36.09 2 .27c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}
