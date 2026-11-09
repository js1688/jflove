/**
 * 服务端配置服务 · v1.9.0 存储与临时文件测试（AC-351 / AC-352 / AC-353）
 *
 * 锁死：
 *   - 配置键名 / 默认值 / 范围与服务端设计文档 §2.10、§3.5 完全一致；
 *   - 「立即清理」走加密 POST `/api/v1/config/cleanup`，返回统计字段；
 *   - 客户端类型名单解析（空 = 全部，逗号集去空格）。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  CLEANUP_CLIENT_TYPES,
  UPLOAD_CLEANUP_DEFAULTS,
  UPLOAD_CLEANUP_KEYS,
  UPLOAD_CLEANUP_RANGES,
  WEAK_SERVER_PRESET,
  configService,
  parseCleanupClientTypes,
} from '../../src/services/config-service';

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  get: vi.fn(),
  put: vi.fn(),
}));

vi.mock('../../src/utils/http-client', () => ({ httpClient: mocks }));

beforeEach(() => {
  mocks.post.mockReset();
  mocks.get.mockReset();
  mocks.put.mockReset();
});

describe('存储与临时文件 · 键名 / 默认值 / 范围', () => {
  it('键名与设计文档 §3.5 一致', () => {
    expect(UPLOAD_CLEANUP_KEYS).toEqual({
      enabled: 'upload_cleanup_enabled',
      resumeTtlHours: 'upload_resume_ttl_hours',
      cleanupIntervalMinutes: 'upload_cleanup_interval_minutes',
      clientTypes: 'upload_cleanup_client_types',
      orphanTtlHours: 'upload_orphan_ttl_hours',
      maxSessionsPerUser: 'upload_max_sessions_per_user',
      mergeConcurrency: 'upload_merge_concurrency',
      chunkConcurrency: 'upload_chunk_concurrency',
    });
  });

  it('默认值：开关开 / TTL 24h / 间隔 60min / 孤儿 24h / 会话上限 8 / 合并 1 / 分片并发 2', () => {
    expect(UPLOAD_CLEANUP_DEFAULTS[UPLOAD_CLEANUP_KEYS.enabled]).toBe('1');
    expect(UPLOAD_CLEANUP_DEFAULTS[UPLOAD_CLEANUP_KEYS.resumeTtlHours]).toBe('24');
    expect(UPLOAD_CLEANUP_DEFAULTS[UPLOAD_CLEANUP_KEYS.cleanupIntervalMinutes]).toBe('60');
    expect(UPLOAD_CLEANUP_DEFAULTS[UPLOAD_CLEANUP_KEYS.orphanTtlHours]).toBe('24');
    expect(UPLOAD_CLEANUP_DEFAULTS[UPLOAD_CLEANUP_KEYS.maxSessionsPerUser]).toBe('8');
    expect(UPLOAD_CLEANUP_DEFAULTS[UPLOAD_CLEANUP_KEYS.mergeConcurrency]).toBe('1');
    expect(UPLOAD_CLEANUP_DEFAULTS[UPLOAD_CLEANUP_KEYS.chunkConcurrency]).toBe('2');
    expect(UPLOAD_CLEANUP_DEFAULTS[UPLOAD_CLEANUP_KEYS.clientTypes]).toBe('');
  });

  it('范围与需求 D26 / F51.1 / D35 一致', () => {
    expect(UPLOAD_CLEANUP_RANGES.resumeTtlHours).toEqual({ min: 1, max: 168 });
    expect(UPLOAD_CLEANUP_RANGES.cleanupIntervalMinutes).toEqual({ min: 5, max: 1440 });
    expect(UPLOAD_CLEANUP_RANGES.orphanTtlHours).toEqual({ min: 1, max: 168 });
    expect(UPLOAD_CLEANUP_RANGES.maxSessionsPerUser).toEqual({ min: 1, max: 64 });
    expect(UPLOAD_CLEANUP_RANGES.mergeConcurrency).toEqual({ min: 0, max: 4 });
    expect(UPLOAD_CLEANUP_RANGES.chunkConcurrency).toEqual({ min: 1, max: 8 });
  });

  it('客户端类型枚举与弱服务端预设（合并 1 / 上传并发 2 / 会话上限 4）', () => {
    expect(CLEANUP_CLIENT_TYPES).toEqual(['app', 'desktop', 'web', 'unknown']);
    expect(WEAK_SERVER_PRESET).toEqual({
      upload_merge_concurrency: '1',
      upload_chunk_concurrency: '2',
      upload_max_sessions_per_user: '4',
    });
  });

  it('parseCleanupClientTypes：空 = 全部，逗号集去空格', () => {
    expect(parseCleanupClientTypes('')).toEqual([]);
    expect(parseCleanupClientTypes(undefined)).toEqual([]);
    expect(parseCleanupClientTypes('web, desktop')).toEqual(['web', 'desktop']);
  });
});

describe('配置服务 · 请求契约', () => {
  it('立即清理 POST /api/v1/config/cleanup 并返回清理统计（加密 body，无路径参数）', async () => {
    mocks.post.mockResolvedValue({
      removed_sessions: 2,
      removed_dirs: 1,
      removed_parts: 3,
      skipped: 1,
      executed_at: 1760000000,
    });

    const result = await configService.runCleanup();
    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(mocks.post.mock.calls[0][0]).toBe('/api/v1/config/cleanup');
    expect(result.removed_sessions).toBe(2);
    expect(result.removed_parts).toBe(3);
    expect(result.skipped).toBe(1);
  });

  it('更新配置 PUT /api/v1/config，{key, value} 原样传给加密层', async () => {
    mocks.put.mockResolvedValue({ message: '配置已更新' });
    await configService.updateConfig(UPLOAD_CLEANUP_KEYS.resumeTtlHours, '72');
    expect(mocks.put).toHaveBeenCalledWith('/api/v1/config', {
      key: 'upload_resume_ttl_hours',
      value: '72',
    });
  });
});
