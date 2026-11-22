import { describe, expect, it } from 'vitest';
import {
  allOperations,
  apiRoutes,
  features,
  meta,
  openapi,
  platforms,
  supportCount,
  supports,
} from '../src/content';

/**
 * 内容层契约测试。
 *
 * 官网的风险不是样式写错，而是**数据读错**（列错位、版本对不上、统计不自洽）。
 * 这里把「manifest 内部自洽」钉成断言，配合 Python 侧的门禁①③④⑩形成双保险。
 */
describe('内容层：特性清单', () => {
  it('三端名称存在且非空', () => {
    expect(platforms.length).toBeGreaterThanOrEqual(3);
    platforms.forEach((p) => expect(p).toBeTruthy());
  });

  it('count 与实际条目数一致', () => {
    expect(features.count).toBe(features.features.length);
  });

  it('每条特性都有三端的支持标记，且值只有 ✅ 或 —', () => {
    features.features.forEach((f) => {
      platforms.forEach((p) => {
        expect([true, false]).toContain(supports(f, p));
        expect(['✅', '—']).toContain(f.supports[p]);
      });
    });
  });

  it('每条特性都有非空摘要', () => {
    features.features.forEach((f) => {
      expect(f.summary.trim().length).toBeGreaterThan(0);
    });
  });

  it('supportCount 不会超过总数', () => {
    platforms.forEach((p) => {
      const n = supportCount(p);
      expect(n).toBeGreaterThanOrEqual(0);
      expect(n).toBeLessThanOrEqual(features.count);
    });
  });
});

describe('内容层：接口数据', () => {
  it('OpenAPI total 等于所有分组的操作之和', () => {
    const summed = openapi.groups.reduce((acc, g) => acc + g.operations.length, 0);
    expect(summed).toBe(openapi.total);
  });

  it('每个分组的 count 等于其操作数', () => {
    openapi.groups.forEach((g) => {
      expect(g.count).toBe(g.operations.length);
    });
  });

  it('allOperations 与分组展开一致', () => {
    expect(allOperations.length).toBe(openapi.total);
  });

  it('明文白名单恰好 3 条，且都能在接口清单里找到', () => {
    expect(openapi.plainPaths.length).toBe(3);
    openapi.plainPaths.forEach((p) => {
      const hit = allOperations.some((op) => op.path === p);
      expect(hit, `明文路径 ${p} 未出现在接口清单中`).toBe(true);
    });
  });

  it('被标记为 plain 的接口恰好等于明文白名单', () => {
    const marked = allOperations.filter((op) => op.plain).map((op) => op.path);
    expect(marked.sort()).toEqual([...openapi.plainPaths].sort());
  });

  it('正则抽取的路由数 + /health 等于 OpenAPI 操作数', () => {
    // 两条独立管线交叉验证：正则数装饰器 vs OpenAPI 读真实路由表
    const regexCount = apiRoutes.count;
    expect(regexCount + 1).toBe(openapi.total);
  });

  it('每个操作都有方法、路径与非空摘要', () => {
    allOperations.forEach((op) => {
      expect(['GET', 'POST', 'PUT', 'DELETE', 'PATCH']).toContain(op.method);
      expect(op.path.startsWith('/')).toBe(true);
      expect(op.summary.trim().length).toBeGreaterThan(0);
    });
  });
});

describe('内容层：版本', () => {
  it('meta.version 形如 x.y.z', () => {
    expect(meta.version).toMatch(/^\d+\.\d+\.\d+$/);
  });

  it('OpenAPI info.version 与 meta.version 一致（官网不允许接口文档落后）', () => {
    expect(openapi.info.version).toBe(meta.version);
  });
});
