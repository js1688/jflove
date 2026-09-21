import type { IconName } from '../components/ui';

/**
 * 业务页面占位配置
 *
 * 每个菜单项对应一个交付节点（见 `plans/desktop-webui-migration-v1.5.0.md` §3 Phase 2）。
 * 之所以把"归属节点"写进代码而不是只写在计划里：这样点进去就能看到
 * 「本页属于哪个节点、还没接」，不会误以为功能已经可用。
 *
 * 顺序与 Web 端 `DesktopLayout` 的导航完全一致。
 */

export interface PlaceholderEntry {
  /** 路由路径（与 Web 端一致） */
  path: string;
  /** 导航名（与 Web 端一致） */
  label: string;
  /** 图标（与 Web 端 DesktopLayout 一致） */
  icon: IconName;
  /** 交付节点编号 */
  node: string;
  /** 是否仅管理员可见 */
  adminOnly?: boolean;
  /**
   * 是否已交付真实页面。
   *
   * 已交付的条目仍保留在本表里（`titleForPath` 靠它算标题栏文字），
   * 但路由不再生成占位页，改由 `routes.tsx` 显式指向真实页面。
   */
  implemented?: boolean;
}

/** 主导航 4 项 */
export const MAIN_PLACEHOLDERS: PlaceholderEntry[] = [
  { path: '/files', label: '文件管理', icon: 'files', node: 'N7' },
  { path: '/notes', label: '笔记管理', icon: 'notes', node: 'N8', implemented: true },
  { path: '/sync', label: '同步管理', icon: 'sync', node: 'N9', implemented: true },
  { path: '/transfer', label: '传输任务', icon: 'transfer', node: 'N10', implemented: true },
];

/** 底部固定项 2 项 */
export const BOTTOM_PLACEHOLDERS: PlaceholderEntry[] = [
  { path: '/security', label: '安全状态', icon: 'security', node: 'N11', implemented: true },
  { path: '/settings', label: '设置', icon: 'settings', node: 'N12', implemented: true },
];

/** 管理面板 4 项（仅 admin） */
export const ADMIN_PLACEHOLDERS: PlaceholderEntry[] = [
  { path: '/admin/users', label: '用户管理', icon: 'users', node: 'N13', adminOnly: true, implemented: true },
  { path: '/admin/disks', label: '磁盘管理', icon: 'disks', node: 'N14', adminOnly: true, implemented: true },
  {
    path: '/admin/permissions',
    label: '权限配置',
    icon: 'permissions',
    node: 'N15',
    adminOnly: true,
    implemented: true,
  },
  { path: '/admin/system', label: '系统设置', icon: 'system', node: 'N16', adminOnly: true, implemented: true },
];

const ALL_ENTRIES: PlaceholderEntry[] = [
  ...MAIN_PLACEHOLDERS,
  ...BOTTOM_PLACEHOLDERS,
  ...ADMIN_PLACEHOLDERS,
];

/**
 * 路径 → 标题栏显示名。
 *
 * 标题栏**只显示当前页面名**，不再重复品牌与用户名 ——
 * 侧栏品牌区（Web 端设计）已经承担了应用身份，标题栏再写一遍会形成
 * 「左上角两个标题」的套层感（用户 2026-09-20 反馈）。
 */
export function titleForPath(pathname: string): string {
  if (pathname === '/login') return '登录';
  if (pathname === '/' || pathname === '') return MAIN_PLACEHOLDERS[0].label;
  const hit = ALL_ENTRIES.find(
    (entry) => pathname === entry.path || pathname.startsWith(`${entry.path}/`),
  );
  return hit ? hit.label : 'JFLove';
}
