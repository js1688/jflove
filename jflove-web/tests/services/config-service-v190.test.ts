/**
 * 服务端配置服务 · v1.9.0 存储与临时文件测试（v1.9.1 精简）
 *
 * 锁死：
 *   - 只剩 3 个配置键（开关 / 期限 / 间隔），默认值与后端 `UPLOAD_CONFIG_DEFAULTS` 一致；
 *   - 已删除键（客户端类型 / 孤儿期 / 会话上限 / 合并 & 分片并发）不得再出现；
 *   - 「立即清理」走加密 POST `/api/v1/config/cleanup`，返回统计字段。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  UPLOAD_CLEANUP_DEFAULTS,
  UPLOAD_CLEANUP_KEYS,
  UPLOAD_CLEANUP_RANGES,
  configService,
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
  it('只保留 3 个可配置键', () => {
    expect(UPLOAD_CLEANUP_KEYS).toEqual({
      enabled: 'upload_cleanup_enabled',
      resumeTtlHours: 'upload_resume_ttl_hours',
      cleanupIntervalMinutes: 'upload_cleanup_interval_minutes',
    });
    const keys = Object.values(UPLOAD_CLEANUP_KEYS) as string[];
    expect(keys).not.toContain('upload_cleanup_client_types');
    expect(keys).not.toContain('upload_orphan_ttl_hours');
    expect(keys).not.toContain('upload_max_sessions_per_user');
    expect(keys).not.toContain('upload_merge_concurrency');
    expect(keys).not.toContain('upload_chunk_concurrency');
  });

  it('默认值：开关开 / 期限 24h / 间隔 60min', () => {
    expect(UPLOAD_CLEANUP_DEFAULTS[UPLOAD_CLEANUP_KEYS.enabled]).toBe('1');
    expect(UPLOAD_CLEANUP_DEFAULTS[UPLOAD_CLEANUP_KEYS.resumeTtlHours]).toBe('24');
    expect(UPLOAD_CLEANUP_DEFAULTS[UPLOAD_CLEANUP_KEYS.cleanupIntervalMinutes])
      .toBe('60');
    expect(Object.keys(UPLOAD_CLEANUP_DEFAULTS)).toHaveLength(3);
  });

  it('范围：期限 1~168 小时 / 间隔 5~1440 分钟', () => {
    expect(UPLOAD_CLEANUP_RANGES.resumeTtlHours).toEqual({ min: 1, max: 168 });
    expect(UPLOAD_CLEANUP_RANGES.cleanupIntervalMinutes).toEqual({
      min: 5,
      max: 1440,
    });
    expect(Object.keys(UPLOAD_CLEANUP_RANGES)).toHaveLength(2);
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
