import { Link, NavLink, Outlet } from 'react-router';
import { siteVersion } from '../content';

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
      { to: '/install', label: '安装指引' },
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
          </div>
          <div className="tnum mt-8 flex flex-wrap justify-between gap-2.5 border-t border-line pt-5 text-[12px] text-ink-500">
            <span>© 2026 JFLove · 本站版本 v{siteVersion}（与项目版本一致）</span>
            <span>ghcr.io/js1688/jflove-website:{siteVersion}</span>
          </div>
        </div>
      </footer>
    </div>
  );
}
