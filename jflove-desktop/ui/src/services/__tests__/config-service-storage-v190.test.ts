/**
 * v1.9.0 存储与临时文件配置 —— 服务层可测部分
 *
 * 判据（设计 §3.5 / 需求 F52）：
 *   - config key 与后端 settings.py 完全一致；
 *   - 多选 ↔ 逗号串可逆、去重、过滤非法值、空 = 全部；
 *   - 立即清理走既有加密 HTTP 封装（`POST /api/v1/config/cleanup`），不直接 fetch。
 */

import { describe, expect, it, vi } from 'vitest';

const httpMock = vi.hoisted(() => ({
  get: vi.fn(),
  put: vi.fn(),
  post: vi.fn(),
}));

vi.mock('../../utils/http-client', () => ({ httpClient: httpMock }));

import {
  STORAGE_CLEANUP_DEFAULTS,
  STORAGE_CLEANUP_KEYS,
  STORAGE_CLIENT_TYPES,
  configService,
  joinClientTypes,
  parseClientTypes,
} from '../config-service';

describe('存储与临时文件配置键（v1.9.0）', () => {
  it('键名与设计 §3.5 一致', () => {
    expect(STORAGE_CLEANUP_KEYS).toEqual({
      enabled: 'upload_cleanup_enabled',
      resumeTtlHours: 'upload_resume_ttl_hours',
      cleanupIntervalMinutes: 'upload_cleanup_interval_minutes',
      cleanupClientTypes: 'upload_cleanup_client_types',
      orphanTtlHours: 'upload_orphan_ttl_hours',
      maxSessionsPerUser: 'upload_max_sessions_per_user',
      mergeConcurrency: 'upload_merge_concurrency',
      chunkConcurrency: 'upload_chunk_concurrency',
    });
  });

  it('默认值与后端 UPLOAD_CONFIG_DEFAULTS 一致', () => {
    expect(STORAGE_CLEANUP_DEFAULTS[STORAGE_CLEANUP_KEYS.resumeTtlHours]).toBe('24');
    expect(STORAGE_CLEANUP_DEFAULTS[STORAGE_CLEANUP_KEYS.cleanupIntervalMinutes])
      .toBe('60');
    expect(STORAGE_CLEANUP_DEFAULTS[STORAGE_CLEANUP_KEYS.orphanTtlHours]).toBe('24');
    expect(STORAGE_CLEANUP_DEFAULTS[STORAGE_CLEANUP_KEYS.maxSessionsPerUser]).toBe('8');
    expect(STORAGE_CLEANUP_DEFAULTS[STORAGE_CLEANUP_KEYS.mergeConcurrency]).toBe('1');
    expect(STORAGE_CLEANUP_DEFAULTS[STORAGE_CLEANUP_KEYS.chunkConcurrency]).toBe('2');
    expect(STORAGE_CLEANUP_DEFAULTS[STORAGE_CLEANUP_KEYS.cleanupClientTypes]).toBe('');
  });
});

describe('客户端类型多选 ↔ 逗号串', () => {
  it('空值 = 全部类型', () => {
    expect(parseClientTypes('')).toEqual([]);
    expect(parseClientTypes(undefined)).toEqual([]);
    expect(joinClientTypes([])).toBe('');
  });

  it('解析时按后端顺序、去重并过滤非法值', () => {
    expect(parseClientTypes('web, desktop,web,ios,unknown')).toEqual([
      'desktop',
      'web',
      'unknown',
    ]);
    expect(parseClientTypes(' app , desktop ')).toEqual(['app', 'desktop']);
  });

  it('序列化按固定顺序，避免勾选顺序造成无意义刷新', () => {
    expect(joinClientTypes(['web', 'app', 'web'])).toBe('app,web');
    expect(joinClientTypes(['unknown', 'desktop', 'app', 'web']))
      .toBe('app,desktop,web,unknown');
  });

  it('客户端类型常量与后端一致', () => {
    expect([...STORAGE_CLIENT_TYPES]).toEqual(['app', 'desktop', 'web', 'unknown']);
  });
});

describe('configService 立即清理', () => {
  it('走加密 HTTP 封装的 POST /api/v1/config/cleanup', async () => {
    httpMock.post.mockResolvedValue({
      removed_sessions: 1,
      removed_dirs: 2,
      removed_parts: 3,
      skipped: 4,
    });

    const result = await configService.cleanupNow();

    expect(httpMock.post).toHaveBeenCalledWith('/api/v1/config/cleanup');
    expect(result.removed_sessions).toBe(1);
    expect(result.removed_parts).toBe(3);
  });

  it('更新配置同样复用 configService.updateConfig（不直接 fetch）', async () => {
    httpMock.put.mockResolvedValue({ message: '配置已更新' });

    await configService.updateConfig(STORAGE_CLEANUP_KEYS.mergeConcurrency, '1');

    expect(httpMock.put).toHaveBeenCalledWith('/api/v1/config', {
      key: 'upload_merge_concurrency',
      value: '1',
    });
  });
});
