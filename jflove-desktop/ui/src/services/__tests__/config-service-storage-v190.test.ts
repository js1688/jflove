/**
 * v1.9.0 存储与临时文件配置 —— 服务层可测部分（v1.9.1 精简）
 *
 * 判据：
 *   - 只剩 3 个 config key（总开关 / 期限 / 间隔），与后端 `UPLOAD_CONFIG_DEFAULTS` 一致；
 *   - 已删除键（客户端类型 / 孤儿期 / 会话上限 / 合并 & 分片并发）不得再出现；
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
  configService,
} from '../config-service';

describe('存储与临时文件配置键（v1.9.1）', () => {
  it('只保留 3 个可配置键', () => {
    expect(STORAGE_CLEANUP_KEYS).toEqual({
      enabled: 'upload_cleanup_enabled',
      resumeTtlHours: 'upload_resume_ttl_hours',
      cleanupIntervalMinutes: 'upload_cleanup_interval_minutes',
    });
    const keys = Object.values(STORAGE_CLEANUP_KEYS) as string[];
    expect(keys).not.toContain('upload_cleanup_client_types');
    expect(keys).not.toContain('upload_orphan_ttl_hours');
    expect(keys).not.toContain('upload_max_sessions_per_user');
    expect(keys).not.toContain('upload_merge_concurrency');
    expect(keys).not.toContain('upload_chunk_concurrency');
  });

  it('默认值与后端 UPLOAD_CONFIG_DEFAULTS 一致', () => {
    expect(STORAGE_CLEANUP_DEFAULTS[STORAGE_CLEANUP_KEYS.enabled]).toBe('1');
    expect(STORAGE_CLEANUP_DEFAULTS[STORAGE_CLEANUP_KEYS.resumeTtlHours]).toBe('24');
    expect(
      STORAGE_CLEANUP_DEFAULTS[STORAGE_CLEANUP_KEYS.cleanupIntervalMinutes],
    ).toBe('60');
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

    await configService.updateConfig(STORAGE_CLEANUP_KEYS.resumeTtlHours, '24');

    expect(httpMock.put).toHaveBeenCalledWith('/api/v1/config', {
      key: 'upload_resume_ttl_hours',
      value: '24',
    });
  });
});
