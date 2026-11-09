/**
 * 上传断点续传 + 合并异步化测试（v1.9.0，AC-321 / AC-326 / AC-327 / AC-330 / AC-331 / AC-363）
 *
 * 服务层单测（http-client 整体 mock，不发真实请求）：
 *   - init 必带 `client_type="web"`，分片默认 512KB，按 `received_chunks` 跳过已传片；
 *   - 单片失败指数退避重试 ≥5 次；失败**不** DELETE 服务端会话；仅用户取消才 DELETE；
 *   - complete 必带 `async_mode=true`，每 2.5s 轮询 progress，处理 queued/merging/done/failed；
 *   - 旧服务端「complete 响应无 state 字段」时才静默降级为同步完成。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  fileService,
  UPLOAD_CHUNK_SIZE,
  UPLOAD_CHUNK_MAX_ATTEMPTS,
  UPLOAD_PROGRESS_POLL_INTERVAL_MS,
  type UploadStatusEvent,
} from '../../src/services/file-service';

const mocks = vi.hoisted(() => ({
  post: vi.fn(),
  get: vi.fn(),
  delete: vi.fn(),
  downloadStream: vi.fn(),
}));

vi.mock('../../src/utils/http-client', () => ({ httpClient: mocks }));

const CHUNK = UPLOAD_CHUNK_SIZE;

/**
 * 构造指定大小的 File 替身。
 *
 * jsdom 的 Blob/File 没有实现 `arrayBuffer()`（file-service 用它算 SHA256 与切片），
 * 这里按 file-service 实际用到的契约（name / size / slice().arrayBuffer / arrayBuffer）
 * 提供最小实现，避免依赖浏览器的 Blob 实现。
 */
function makeFile(size: number, name = 'big.bin'): File {
  const bytes = new Uint8Array(size);
  for (let i = 0; i < size; i += 1) bytes[i] = i % 251;
  const fake = {
    name,
    size,
    async arrayBuffer(): Promise<ArrayBuffer> {
      return new Uint8Array(bytes).buffer;
    },
    slice(start: number, end: number) {
      const part = bytes.slice(start, end);
      return {
        async arrayBuffer(): Promise<ArrayBuffer> {
          return new Uint8Array(part).buffer;
        },
      };
    },
  };
  return fake as unknown as File;
}

/** 推进假定时器直到 Promise 落定（用于含退避 sleep / 2.5s 轮询的场景） */
async function runUntilSettled<T>(promise: Promise<T>): Promise<T> {
  let settled = false;
  promise.then(
    () => { settled = true; },
    () => { settled = true; },
  );
  for (let i = 0; i < 40 && !settled; i += 1) {
    await vi.advanceTimersByTimeAsync(UPLOAD_PROGRESS_POLL_INTERVAL_MS);
  }
  return promise;
}

function chunkCalls(): Array<Record<string, unknown>> {
  return mocks.post.mock.calls
    .filter(([url]) => url === '/api/v1/files/upload/chunk')
    .map(([, body]) => body as Record<string, unknown>);
}

beforeEach(() => {
  vi.useFakeTimers();
  mocks.post.mockReset();
  mocks.get.mockReset();
  mocks.delete.mockReset();
  mocks.get.mockResolvedValue({ files: [] });
});

afterEach(() => {
  vi.useRealTimers();
});

describe('上传断点续传 · init 与分片', () => {
  it('init 带 client_type="web"，按 512KB 计算 total_chunks，并跳过 received_chunks', async () => {
    const file = makeFile(CHUNK * 2 + 100);
    mocks.post.mockImplementation((url: string) => {
      if (url === '/api/v1/files/upload/init') {
        return Promise.resolve({
          upload_id: 'u-1',
          received_chunks: [0, 2],
          resumed: true,
          expires_at: 1760000000,
        });
      }
      if (url === '/api/v1/files/upload/complete') {
        return Promise.resolve({ state: 'done', path: '/root/big.bin' });
      }
      return Promise.resolve({ message: 'ok' });
    });

    const progress = vi.fn();
    const statuses: UploadStatusEvent[] = [];
    const result = await fileService.uploadFile(3, 'dir', file, progress, {
      onStatus: event => statuses.push(event),
    });

    const initBody = mocks.post.mock.calls[0][1] as Record<string, unknown>;
    expect(mocks.post.mock.calls[0][0]).toBe('/api/v1/files/upload/init');
    expect(initBody.client_type).toBe('web');
    expect(initBody.total_chunks).toBe(3);
    // total_chunks=3 与 512KB 分片互相印证（旧 1MB 分片会算成 2 片）
    expect(UPLOAD_CHUNK_SIZE).toBe(512 * 1024);

    // 已收 0、2 → 只补第 1 片
    expect(chunkCalls()).toHaveLength(1);
    expect(chunkCalls()[0].chunk_index).toBe(1);
    expect(chunkCalls()[0].upload_id).toBe('u-1');

    // 初始进度包含已收分片字节（512KB + 100B）
    expect(progress.mock.calls[0][0]).toBe(CHUNK + 100);
    expect(progress.mock.calls[progress.mock.calls.length - 1][0]).toBe(file.size);

    expect(statuses[0].state).toBe('resumed');
    expect(statuses[0].message).toContain('从第 1 片继续');
    expect(result.path).toBe('/root/big.bin');
  });

  it('restart_required（原会话已失败/过期）→ 从第 0 片重传并明确提示', async () => {
    const file = makeFile(CHUNK + 10);
    mocks.post.mockImplementation((url: string) => {
      if (url === '/api/v1/files/upload/init') {
        return Promise.resolve({
          upload_id: 'u-2',
          received_chunks: [0],
          restart_required: true,
        });
      }
      if (url === '/api/v1/files/upload/complete') {
        return Promise.resolve({ state: 'done', path: '/root/big.bin' });
      }
      return Promise.resolve({ message: 'ok' });
    });

    const statuses: UploadStatusEvent[] = [];
    await fileService.uploadFile(3, 'dir', file, undefined, {
      onStatus: event => statuses.push(event),
    });

    expect(chunkCalls().map(call => call.chunk_index)).toEqual([0, 1]);
    expect(statuses[0].message).toContain('重新上传');
  });

  it('上传前远端列表核验命中同名同大小 → 直接判定完成，不 init、不传分片（F46.6）', async () => {
    mocks.get.mockResolvedValue({
      files: [{ name: 'big.bin', path: 'dir/big.bin', size: 1024, is_dir: false, modified_at: 0 }],
    });
    const statuses: UploadStatusEvent[] = [];
    const result = await fileService.uploadFile(3, 'dir', makeFile(1024), undefined, {
      onStatus: event => statuses.push(event),
    });

    expect(mocks.post).not.toHaveBeenCalled();
    expect(result.alreadyExists).toBe(true);
    expect(statuses[0].state).toBe('done');
    expect(statuses[0].message).toContain('无需重复上传');
  });

  it('远端已存在同大小文件（already_exists）→ 不上传任何分片', async () => {
    mocks.post.mockResolvedValueOnce({
      already_exists: true,
      already_path: '/root/big.bin',
    });
    const file = makeFile(1024);
    const statuses: UploadStatusEvent[] = [];
    const result = await fileService.uploadFile(3, 'dir', file, undefined, {
      onStatus: event => statuses.push(event),
    });

    expect(chunkCalls()).toHaveLength(0);
    expect(mocks.post).toHaveBeenCalledTimes(1);
    expect(result.alreadyExists).toBe(true);
    expect(statuses[0].state).toBe('done');
  });
});

describe('上传断点续传 · 失败重试与取消', () => {
  it('单片失败按指数退避重试，≥5 次后仍失败则抛错且不 DELETE 会话', async () => {
    mocks.post.mockImplementation((url: string) => {
      if (url === '/api/v1/files/upload/chunk') {
        return Promise.reject(new Error('network down'));
      }
      if (url === '/api/v1/files/upload/init') {
        return Promise.resolve({ upload_id: 'u-3', received_chunks: [] });
      }
      return Promise.resolve({});
    });

    const file = makeFile(1024);
    await expect(runUntilSettled(fileService.uploadFile(1, '', file))).rejects.toThrow(
      'network down',
    );
    expect(chunkCalls()).toHaveLength(UPLOAD_CHUNK_MAX_ATTEMPTS);
    expect(UPLOAD_CHUNK_MAX_ATTEMPTS).toBeGreaterThanOrEqual(5);
    // 关键：网络失败不得主动清理服务端会话（F46.5）
    expect(mocks.delete).not.toHaveBeenCalled();
  });

  it('用户取消 → 中止上传并 DELETE 服务端会话（唯一允许清理的路径）', async () => {
    const controller = new AbortController();
    mocks.post.mockImplementation((url: string) => {
      if (url === '/api/v1/files/upload/init') {
        return Promise.resolve({ upload_id: 'u-cancel', received_chunks: [] });
      }
      return Promise.resolve({ message: 'ok' });
    });

    const file = makeFile(CHUNK * 2);
    const statuses: UploadStatusEvent[] = [];
    await expect(fileService.uploadFile(1, '', file, undefined, {
      signal: controller.signal,
      onStatus: (event) => {
        statuses.push(event);
        // 进入上传阶段立刻模拟用户点「取消」
        if (event.state === 'uploading') controller.abort();
      },
    })).rejects.toMatchObject({ name: 'AbortError' });

    expect(mocks.delete).toHaveBeenCalledTimes(1);
    expect(mocks.delete.mock.calls[0][0]).toBe('/api/v1/files/upload/u-cancel');
    expect(statuses.some(s => s.state === 'uploading')).toBe(true);
  });

  it('合并失败（failed + missing_chunks）→ 中文原因 + 缺失片索引，且不 DELETE', async () => {
    mocks.post.mockImplementation((url: string) => {
      if (url === '/api/v1/files/upload/init') {
        return Promise.resolve({ upload_id: 'u-4', received_chunks: [] });
      }
      if (url === '/api/v1/files/upload/complete') {
        return Promise.resolve({ state: 'failed', error: 'missing_chunks', missing_chunks: [2] });
      }
      return Promise.resolve({ message: 'ok' });
    });

    const file = makeFile(CHUNK * 2);
    await expect(fileService.uploadFile(1, '', file)).rejects.toThrow(
      /缺少分片 2/,
    );
    expect(mocks.delete).not.toHaveBeenCalled();
  });
});

describe('上传断点续传 · complete 异步轮询', () => {
  it('complete 带 async_mode=true；每 2.5s 轮询 progress，按 queued→merging→done 收尾', async () => {
    let progressRound = 0;
    mocks.post.mockImplementation((url: string) => {
      if (url === '/api/v1/files/upload/init') {
        return Promise.resolve({ upload_id: 'u-5', received_chunks: [] });
      }
      if (url === '/api/v1/files/upload/complete') {
        return Promise.resolve({
          state: 'merging',
          upload_id: 'u-5',
          poll_interval_s: 2.5,
        });
      }
      if (url === '/api/v1/files/upload/progress') {
        progressRound += 1;
        if (progressRound === 1) {
          return Promise.resolve({ state: 'queued', queue_position: 2, percent: 0 });
        }
        if (progressRound === 2) {
          return Promise.resolve({ state: 'merging', percent: 40 });
        }
        return Promise.resolve({ state: 'done', path: '/root/big.bin' });
      }
      return Promise.resolve({ message: 'ok' });
    });

    const file = makeFile(1024);
    const statuses: UploadStatusEvent[] = [];
    const promise = fileService.uploadFile(1, '', file, undefined, {
      onStatus: event => statuses.push(event),
    });

    // 让 init / 分片 / complete 的微任务先完成，直到进入第一次 sleep（有 1 个挂起的定时器）
    for (let i = 0; i < 20 && vi.getTimerCount() === 0; i += 1) {
      await vi.advanceTimersByTimeAsync(0);
    }
    expect(progressRound).toBe(0);
    expect(vi.getTimerCount()).toBe(1);
    await vi.advanceTimersByTimeAsync(UPLOAD_PROGRESS_POLL_INTERVAL_MS);
    expect(progressRound).toBe(1);
    await vi.advanceTimersByTimeAsync(UPLOAD_PROGRESS_POLL_INTERVAL_MS);
    expect(progressRound).toBe(2);
    await vi.advanceTimersByTimeAsync(UPLOAD_PROGRESS_POLL_INTERVAL_MS);

    const result = await promise;
    expect(result.path).toBe('/root/big.bin');
    expect(UPLOAD_PROGRESS_POLL_INTERVAL_MS).toBe(2500);

    const completeBody = mocks.post.mock.calls
      .find(([url]) => url === '/api/v1/files/upload/complete')?.[1] as Record<string, unknown>;
    expect(completeBody.async_mode).toBe(true);

    expect(statuses.map(s => s.state)).toEqual(['uploading', 'queued', 'merging', 'done']);
    expect(statuses[1].message).toContain('排队');
    expect(statuses[2].message).toContain('服务端合并中');
  });

  it('旧服务端 complete 响应无 state 字段时静默降级为同步完成（不做无意义轮询）', async () => {
    mocks.post.mockImplementation((url: string) => {
      if (url === '/api/v1/files/upload/init') {
        return Promise.resolve({ upload_id: 'u-legacy', received_chunks: [] });
      }
      if (url === '/api/v1/files/upload/complete') {
        return Promise.resolve({ message: '上传完成', path: '/root/legacy.bin' });
      }
      return Promise.resolve({ message: 'ok' });
    });

    const statuses: UploadStatusEvent[] = [];
    const result = await fileService.uploadFile(1, '', makeFile(1024), undefined, {
      onStatus: event => statuses.push(event),
    });

    expect(result.path).toBe('/root/legacy.bin');
    expect(statuses[statuses.length - 1].state).toBe('done');
    expect(mocks.post.mock.calls.some(([url]) => url === '/api/v1/files/upload/progress'))
      .toBe(false);
  });

  it('progress 返回 unknown（会话丢失）时用远端列表核验，大小一致则判定完成（F46.6）', async () => {
    mocks.post.mockImplementation((url: string) => {
      if (url === '/api/v1/files/upload/init') {
        return Promise.resolve({ upload_id: 'u-6', received_chunks: [] });
      }
      if (url === '/api/v1/files/upload/complete') {
        return Promise.resolve({ state: 'merging' });
      }
      if (url === '/api/v1/files/upload/progress') {
        return Promise.resolve({ state: 'unknown' });
      }
      return Promise.resolve({ message: 'ok' });
    });
    mocks.get
      .mockResolvedValueOnce({ files: [] })
      .mockResolvedValueOnce({
        files: [{ name: 'big.bin', path: 'dir/big.bin', size: 1024, is_dir: false, modified_at: 0 }],
      });

    const result = await runUntilSettled(fileService.uploadFile(1, 'dir', makeFile(1024)));
    expect(result.alreadyExists).toBe(true);
  });
});
