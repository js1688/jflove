/**
 * 离线下载服务契约测试（v1.8.0）
 *
 * 断言 10 个接口的 **HTTP 方法 + 路径 + 请求字段**与设计文档 §2.2 完全一致：
 *   - GET 走 `httpClient.get`（query 加密信封），POST 走 `httpClient.post`；
 *   - 路径**无参数**，`task_id` 等一律在 body 内（安全宪法 §9.2 / §9.3）；
 *   - GET 的 `/meta` 不带任何业务字段。
 *
 * 这里 mock 掉 `http-client` 本身：加密信封的正确性由 `tests/utils/http-client.test.ts` 覆盖，
 * 本文件只锁「service 层契约」。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../../src/utils/http-client', () => ({
  httpClient: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

import { httpClient } from '../../src/utils/http-client';
import {
  OFFLINE_PAGE_SIZE,
  offlineDownloadService,
} from '../../src/services/offline-download-service';

const getMock = vi.mocked(httpClient.get);
const postMock = vi.mocked(httpClient.post);

beforeEach(() => {
  getMock.mockReset();
  postMock.mockReset();
  getMock.mockResolvedValue({});
  postMock.mockResolvedValue({});
});

describe('offline-download-service 接口契约', () => {
  it('analyze：POST /api/v1/offline/analyze { url }', async () => {
    await offlineDownloadService.analyze('https://example.com/v/1');
    expect(postMock).toHaveBeenCalledWith('/api/v1/offline/analyze', {
      url: 'https://example.com/v/1',
    });
  });

  it('listTasks：GET /api/v1/offline/tasks { page, page_size }', async () => {
    await offlineDownloadService.listTasks(2, 20);
    expect(getMock).toHaveBeenCalledWith('/api/v1/offline/tasks', {
      page: 2,
      page_size: 20,
    });
  });

  it('listTasks：默认分页参数', async () => {
    await offlineDownloadService.listTasks();
    expect(getMock).toHaveBeenCalledWith('/api/v1/offline/tasks', {
      page: 1,
      page_size: OFFLINE_PAGE_SIZE,
    });
  });

  it('detail：GET /api/v1/offline/detail { task_id }', async () => {
    await offlineDownloadService.detail(42);
    expect(getMock).toHaveBeenCalledWith('/api/v1/offline/detail', { task_id: 42 });
  });

  it('select：POST /select 字段名转 snake_case，pack_zip 默认 false', async () => {
    await offlineDownloadService.select({
      taskId: 7,
      itemIds: [1, 2, 3],
      diskId: 5,
      targetPath: 'downloads/media',
    });
    expect(postMock).toHaveBeenCalledWith('/api/v1/offline/select', {
      task_id: 7,
      item_ids: [1, 2, 3],
      disk_id: 5,
      target_path: 'downloads/media',
      pack_zip: false,
    });
  });

  it('select：pack_zip 显式 true', async () => {
    await offlineDownloadService.select({
      taskId: 7,
      itemIds: [1],
      diskId: 5,
      targetPath: 'downloads',
      packZip: true,
    });
    expect(postMock).toHaveBeenCalledWith(
      '/api/v1/offline/select',
      expect.objectContaining({ pack_zip: true }),
    );
  });

  it('nextPage：POST /api/v1/offline/next-page { task_id }', async () => {
    await offlineDownloadService.nextPage(9);
    expect(postMock).toHaveBeenCalledWith('/api/v1/offline/next-page', { task_id: 9 });
  });

  it('cancel / retry / deleteRecord：各自路径 + { task_id }', async () => {
    await offlineDownloadService.cancel(11);
    await offlineDownloadService.retry(12);
    await offlineDownloadService.deleteRecord(13);
    expect(postMock).toHaveBeenNthCalledWith(1, '/api/v1/offline/cancel', { task_id: 11 });
    expect(postMock).toHaveBeenNthCalledWith(2, '/api/v1/offline/retry', { task_id: 12 });
    expect(postMock).toHaveBeenNthCalledWith(3, '/api/v1/offline/delete-record', { task_id: 13 });
  });

  it('meta：GET /api/v1/offline/meta 且不带业务字段', async () => {
    await offlineDownloadService.meta();
    expect(getMock).toHaveBeenCalledWith('/api/v1/offline/meta');
  });

  it('saveSettings：POST /settings { disk_id, rel_path, create }，create 默认 false', async () => {
    await offlineDownloadService.saveSettings(3, 'a/b');
    expect(postMock).toHaveBeenCalledWith('/api/v1/offline/settings', {
      disk_id: 3,
      rel_path: 'a/b',
      create: false,
    });
  });

  it('saveSettings：create=true 透传', async () => {
    await offlineDownloadService.saveSettings(3, 'a/b', true);
    expect(postMock).toHaveBeenCalledWith('/api/v1/offline/settings', {
      disk_id: 3,
      rel_path: 'a/b',
      create: true,
    });
  });

  it('saveSettings：disk_id=0 表示清除（不额外传 user_id）', async () => {
    await offlineDownloadService.saveSettings(0, '');
    const [, body] = postMock.mock.calls[0];
    expect(body).toEqual({ disk_id: 0, rel_path: '', create: false });
    expect(body).not.toHaveProperty('user_id');
  });

  it('全部路径都在 /api/v1/offline 前缀下（无明文路径 / 无路径参数）', async () => {
    await offlineDownloadService.analyze('https://example.com');
    await offlineDownloadService.listTasks();
    await offlineDownloadService.detail(1);
    await offlineDownloadService.nextPage(1);
    await offlineDownloadService.cancel(1);
    await offlineDownloadService.retry(1);
    await offlineDownloadService.deleteRecord(1);
    await offlineDownloadService.meta();
    await offlineDownloadService.saveSettings(1, '');

    const paths = [
      ...getMock.mock.calls.map((call) => call[0]),
      ...postMock.mock.calls.map((call) => call[0]),
    ];
    expect(paths.length).toBeGreaterThan(0);
    for (const path of paths) {
      expect(path.startsWith('/api/v1/offline/')).toBe(true);
      // 无路径参数：`/offline/detail/1` 这类写法不允许
      expect(path).not.toMatch(/\/\d+$/);
    }
  });
});
