/**
 * 布局与信息架构回归（v1.5.0，Web 端）
 *
 * 覆盖 AC-18 / AC-19 / AC-20 里"结构不能被后续改动悄悄破坏"的部分：
 *   - 导航 6 → 5：`MobileLayout` 的 TabBar 恰好 5 项，且不再出现「修复中心」；
 *   - `/repair` 旧路径**必须保留**（深链与"查看修复进度"跳转），落在同一页的「修复」标签；
 *   - 修复中心的全部功能在 Web 端仍可从合并页访问（AC-18 功能无缺失）；
 *   - 移动端导航点按区域 ≥ 44×44、底部安全区有真实定义（AC-19 / AC-20）；
 *   - PC 侧栏分区（主导航 / 管理 / 底部固定）+ rail 折叠 + 存储卡与用户卡（AC-20）。
 *
 * 为什么用源码断言而不是渲染断言：底部导航与路由表都是**声明式常量**，渲染
 * 需要拉齐 store / 路由 / 响应式环境，脆弱且不增加可信度；结构约束用文本断言更稳。
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

const SRC = resolve(__dirname, '../../src');

function read(relative: string): string {
  return readFileSync(resolve(SRC, relative), 'utf8');
}

describe('Web 移动布局导航（v1.7.0 移除同步 → 4 项；v1.8.0 补入离线下载 → 5 项；AC-18 / AC-20 / AC-200）', () => {
  const mobileLayout = read('layouts/MobileLayout.tsx');

  it('底部导航恰好 5 项，顺序为 文件/笔记/传输/离线/设置', () => {
    const block = mobileLayout.split('const TABS: TabEntry[] = [')[1].split('];')[0];
    const labels = [...block.matchAll(/label: '([^']+)'/g)].map((m) => m[1]);
    expect(labels).toEqual(['文件', '笔记', '传输', '离线', '设置']);
    expect(labels).not.toContain('修复');
    // v1.7.0：浏览器端不提供同步能力，不得再出现「同步」tab
    expect(labels).not.toContain('同步');
  });

  it('导航项路由与移动端 App 的信息架构一致（含 v1.8.0 的 /offline）', () => {
    const block = mobileLayout.split('const TABS: TabEntry[] = [')[1].split('];')[0];
    const paths = [...block.matchAll(/path: '([^']+)'/g)].map((m) => m[1]);
    expect(paths).toEqual(['/files', '/notes', '/transfer', '/offline', '/settings']);
    expect(paths).not.toContain('/sync');
  });

  it('图标已矢量化（走统一 Icon 组件，不再有 emoji 导航图标）', () => {
    expect(mobileLayout).toContain("import { Icon, type IconName } from '../components/ui'");
    expect(mobileLayout).toContain('<Icon name={tab.icon}');
    // 导航常量块里不得出现 emoji
    const block = mobileLayout.split('const TABS: TabEntry[] = [')[1].split('];')[0];
    expect(/[\u{1F300}-\u{1FAFF}]/u.test(block)).toBe(false);
  });

  it('底部导航适配安全区（AC-19 / 刘海屏不被手势条遮挡）', () => {
    expect(mobileLayout).toContain('safe-area-bottom');
    const css = read('styles/components.css');
    expect(css).toContain('.safe-area-bottom');
    expect(css).toContain('padding-bottom: env(safe-area-inset-bottom, 0px)');
    expect(css).toContain('padding-top: env(safe-area-inset-top, 0px)');
  });

  it('导航项点按区域不小于 44×44（AC-19）', () => {
    const css = read('styles/components.css');
    const item = css.split('.tabbar-item {')[1].split('}')[0];
    expect(item).toMatch(/min-height:\s*44px/);
    const tabbar = css.split('.tabbar {')[1].split('}')[0];
    expect(tabbar).toMatch(/height:\s*var\(--tabbar-h\)/);
    expect(read('styles/tokens.css')).toMatch(/--tabbar-h:\s*60px/);
  });
});

describe('Web 路由与修复中心可达性（AC-18）', () => {
  const routes = read('config/routes.tsx');
  const tabsPage = read('pages/TransferTabsPage.tsx');
  const repairPage = read('pages/RepairCenterPage.tsx');

  it('/repair 旧路径仍然保留并指向合并页', () => {
    expect(routes).toContain("{ path: 'repair', element: <TransferTabsPage /> }");
    expect(routes).toContain("{ path: 'transfer', element: <TransferTabsPage /> }");
  });

  it('/repair 进入时默认落在「修复」标签，并保持 URL 与标签一致', () => {
    expect(tabsPage).toContain("location.pathname.startsWith('/repair') ? 'repair' : 'transfer'");
    expect(tabsPage).toContain("next === 'repair' ? '/repair' : '/transfer'");
    expect(tabsPage).toContain("value: 'repair', label: '修复'");
    expect(tabsPage).toContain("value: 'transfer', label: '传输'");
  });

  it('修复中心全部操作入口仍在（取消/验证播放/覆盖/删除产物/删除记录）', () => {
    for (const action of ['取消', '验证播放', '覆盖原文件', '删除产物', '删除记录']) {
      expect(repairPage).toContain(action);
    }
    // 覆盖属于破坏性操作，必须有二次确认
    expect(repairPage).toContain('覆盖原文件（不可恢复）');
    expect(repairPage).toContain('确认覆盖');
  });

  it('修复中心内容被合并页复用（不是被复制一遍）', () => {
    expect(tabsPage).toContain("import { RepairCenterContent } from './RepairCenterPage'");
    expect(repairPage).toContain('export function RepairCenterContent()');
    expect(repairPage).toContain('export function RepairTasksView(');
  });
});

describe('离线下载入口与路由（v1.8.0 / F30 / AC-200 / AC-232）', () => {
  const routes = read('config/routes.tsx');
  const desktopLayout = read('layouts/DesktopLayout.tsx');
  const mobileLayout = read('layouts/MobileLayout.tsx');
  const page = read('pages/OfflineDownloadPage.tsx');
  const service = read('services/offline-download-service.ts');

  it('/offline 路由指向 OfflineDownloadPage', () => {
    expect(routes).toContain("import { OfflineDownloadPage } from '../pages/OfflineDownloadPage'");
    expect(routes).toContain("{ path: 'offline', element: <OfflineDownloadPage /> }");
  });

  it('PC 侧栏有「离线下载」入口', () => {
    expect(desktopLayout).toContain("{ path: '/offline', label: '离线下载', icon: 'download' }");
  });

  it('移动浏览器底部菜单有「离线」入口', () => {
    expect(mobileLayout).toContain("{ path: '/offline', label: '离线', icon: 'download' }");
  });

  it('页面不直接发请求，全部经 service → http-client 加密信封', () => {
    expect(page).toContain(
      "import { offlineDownloadService } from '../services/offline-download-service'",
    );
    expect(page).not.toContain('fetch(');
    expect(page).not.toContain('localStorage.setItem');
    expect(page).not.toContain('sessionStorage');
    expect(service).toContain("from '../utils/http-client'");
    expect(service).not.toContain('fetch(');
    // 10 个接口全部在 /api/v1/offline 前缀下，且无路径参数
    const paths = [...service.matchAll(/'\/api\/v1\/offline\/[a-z-]+'/g)].map((m) => m[0]);
    expect(new Set(paths).size).toBe(10);
    for (const path of paths) {
      expect(path).toMatch(/^'\/api\/v1\/offline\/[a-z-]+'$/);
    }
  });

  it('轮询遵守可见性守卫并在卸载时清理定时器（AC-232）', () => {
    expect(page).toContain("document.visibilityState !== 'visible'");
    expect(page).toContain("document.addEventListener('visibilitychange'");
    expect(page).toContain("document.removeEventListener('visibilitychange'");
    expect(page).toContain('clearInterval');
    // 间隔来自 /offline/meta 的 poll_interval_ms（默认 2500）
    expect(page).toContain('resolvePollInterval(meta?.poll_interval_ms)');
  });
});

describe('Web PC 布局（AC-20）', () => {
  const desktopLayout = read('layouts/DesktopLayout.tsx');

  it('侧栏分区：主导航 / 管理 / 底部固定三区', () => {
    expect(desktopLayout).toContain('const NAV_ITEMS');
    expect(desktopLayout).toContain('const ADMIN_ITEMS');
    expect(desktopLayout).toContain('const BOTTOM_ITEMS');
    expect(desktopLayout).toContain('管理');
  });

  it('折叠用真实 rail 宽度令牌，而不是临时数值', () => {
    expect(desktopLayout).toContain("collapsed ? 'var(--sidebar-w-rail)' : 'var(--sidebar-w)'");
    const tokens = read('styles/tokens.css');
    expect(tokens).toMatch(/--sidebar-w:\s*248px/);
    expect(tokens).toMatch(/--sidebar-w-rail:\s*68px/);
  });

  it('激活项带指示条与浅色底（AC-4 激活反馈）', () => {
    expect(desktopLayout).toContain('data-active={active}');
    expect(desktopLayout).toContain('aria-current={active');
    const css = read('styles/components.css');
    expect(css).toMatch(/\.nav-item\[data-active='true'\]/);
  });

  it('底部固定区含存储卡与用户卡', () => {
    expect(desktopLayout).toContain('存储空间');
    expect(desktopLayout).toContain('username');
    expect(desktopLayout).toContain('普通用户');
  });

  it('激活态按「每个条目自己的 path」判定（v1.4.2 硬编码 isActive("/admin") 永不命中）', () => {
    expect(desktopLayout).toContain('data-active={active}');
    expect(desktopLayout).toContain('item.prefix ? location.pathname.startsWith(item.path)');
    // 管理区 4 项各自独立判定，不存在写死的 /admin 判断
    for (const adminPath of ['/admin/users', '/admin/disks', '/admin/permissions', '/admin/system']) {
      expect(desktopLayout).toContain(`path: '${adminPath}'`);
    }
    // 代码里不得再出现写死的 /admin 判定（注释里记录历史缺陷是允许的）
    const codeLines = desktopLayout
      .split('\n')
      .filter((line) => !line.trim().startsWith('*') && !line.trim().startsWith('//'));
    expect(codeLines.some((line) => line.includes("isActive('/admin')"))).toBe(false);
  });
});
