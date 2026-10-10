/**
 * 品牌标识两端一致回归（v1.5.1 #1，AC-1 / AC-2 / AC-3 的 Web 端部分）
 *
 * 用户反馈：「web 端 logo 不一致 —— 桌面端左上角是一只可爱的猫猫，Web 端是一个盾牌」。
 * 本文件把"品牌位必须是猫猫 logo 图片"绑成**可执行断言**（`.claude/skills/LESSONS.md` L12）：
 *   - 侧边栏品牌位用 `./logo-128.png`，属性与桌面端逐属性一致；
 *   - 登录页品牌位用 `./logo-256.png`，保留原有尺寸 / 圆角类（版式不变）；
 *   - 全量源码中不再出现 `name="shield"`（替换前的品牌标识）；
 *   - 三份 logo 资产确实在 `public/` 下且是合法 PNG（避免"引用了不存在的图"）。
 *
 * 跨端"逐字节一致"由根级脚本 `scripts/check_logo_sync.py` 负责（不在本模块边界内），
 * 这里只锁 Web 端自身的引用点与资产存在性。
 */
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';

import { describe, expect, it } from 'vitest';

const SRC = resolve(__dirname, '../../src');
const PUBLIC = resolve(__dirname, '../../public');

function read(relativePath: string): string {
  return readFileSync(resolve(SRC, relativePath), 'utf8');
}

/** 递归收集 src 下的全部文件（用于全量静态扫描） */
function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    return statSync(full).isDirectory() ? walk(full) : [full];
  });
}

const PNG_MAGIC = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];

describe('#1 品牌标识与桌面端一致（AC-1 / AC-2）', () => {
  it('侧边栏品牌位用 logo-128.png，属性与桌面端逐属性一致', () => {
    const layout = read('layouts/DesktopLayout.tsx');
    expect(layout).toContain('src="./logo-128.png"');
    expect(layout).toContain('alt=""');
    expect(layout).toContain('aria-hidden="true"');
    expect(layout).toContain('className="h-8 w-8 shrink-0 rounded-[10px] object-cover"');
    // 原先的渐变方块 + 盾牌图标品牌位必须消失
    expect(layout).not.toContain('logo-mark');
    expect(layout).not.toContain('name="shield"');
  });

  it('登录页品牌位用 logo-256.png，且保留原尺寸 / 圆角类（版式不变）', () => {
    const login = read('pages/LoginPage.tsx');
    expect(login).toContain('src="./logo-256.png"');
    expect(login).toContain('className="mx-auto h-14 w-14 rounded-2xl object-cover"');
    expect(login).not.toContain('logo-mark');
    expect(login).not.toContain('name="shield"');
  });

  it('src 下不再有任何 name="shield"（两处品牌位是它仅有的使用点）', () => {
    const hits = walk(SRC)
      .filter((file) => readFileSync(file, 'utf8').includes('name="shield"'))
      .map((file) => relative(SRC, file));
    expect(hits, `以下文件仍在使用盾牌作为品牌标识：${hits.join(', ')}`).toEqual([]);
  });

  it('Icon 组件仍导出 Icon（品牌位改造不牵连图标层）', () => {
    expect(read('components/ui/Icon.tsx')).toContain('export function Icon(');
    expect(read('components/ui/index.ts')).toContain("export { Icon");
  });
});

describe('#1 logo 资产（AC-3 的 Web 端一半）', () => {
  it('public/ 下三份 logo 都存在且是合法 PNG', () => {
    for (const size of [64, 128, 256]) {
      const file = join(PUBLIC, `logo-${size}.png`);
      expect(existsSync(file), `缺少品牌资产 ${file}`).toBe(true);
      const bytes = readFileSync(file);
      expect([...bytes.subarray(0, 8)], `${file} 不是合法 PNG`).toEqual(PNG_MAGIC);
      // 非空占位图：三份资产都应是真实图片（桌面端同字节副本，最小 7KB）
      expect(bytes.length).toBeGreaterThan(1024);
    }
  });

  it('favicon 未被本次改动波及（仍是 svg，不在本次范围）', () => {
    expect(existsSync(join(PUBLIC, 'favicon.svg'))).toBe(true);
    expect(readFileSync(join(PUBLIC, 'favicon.svg'), 'utf8')).toContain('<svg');
  });
});
