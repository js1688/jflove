/**
 * HTTP 客户端测试
 *
 * 覆盖安全宪法 §9.6 testing 第④⑤类：
 *   - 错误响应加密（404/401 解密后 detail 正确）
 *   - 白名单边界（明文/加密路径处理）
 *   - 加密请求构造（token 在加密 body 内，不走 Authorization header）
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { httpClient } from '../../src/utils/http-client';
import {
  setSessionKey, setSessionId, setToken,
  setServerUrl, clearSession,
} from '../../src/utils/session';
import {
  generateKeyPair, deriveSessionKey,
  encryptEnvelope, decryptEnvelope,
} from '../../src/utils/crypto';

/** 生成测试用 session_key */
async function makeSessionKey(): Promise<Uint8Array> {
  const alice = await generateKeyPair();
  const bob = await generateKeyPair();
  return deriveSessionKey(alice.privateKey, bob.publicKeyB64);
}

/** 构造加密响应 JSON */
async function makeEncryptedResponse(
  sessionKey: Uint8Array,
  data: Record<string, unknown>,
): Promise<Response> {
  const plaintext = new TextEncoder().encode(JSON.stringify(data));
  const { nonce, ciphertext } = encryptEnvelope(sessionKey, plaintext);
  const body = JSON.stringify({ nonce, ciphertext });
  return new Response(body, {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

/** 构造加密错误响应 JSON */
async function makeEncryptedError(
  sessionKey: Uint8Array,
  status: number,
  detail: string,
): Promise<Response> {
  const plaintext = new TextEncoder().encode(JSON.stringify({ detail }));
  const { nonce, ciphertext } = encryptEnvelope(sessionKey, plaintext);
  const body = JSON.stringify({ nonce, ciphertext });
  return new Response(body, {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('http-client 加密请求', () => {
  let sessionKey: Uint8Array;

  beforeEach(async () => {
    sessionKey = await makeSessionKey();
    setSessionKey(sessionKey);
    setSessionId('test-session-id');
    setToken('test-jwt-token');
    setServerUrl('http://test.local:8989');
  });

  afterEach(() => {
    clearSession();
    vi.restoreAllMocks();
  });

  it('POST 请求体是加密信封，token 在解密后的 body 内（不走 Authorization header）', async () => {
    const mockResponse = await makeEncryptedResponse(sessionKey, { message: '已重命名' });
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(mockResponse);

    await httpClient.post('/api/v1/files/rename', {
      disk_id: 1,
      path: 'a.txt',
      new_name: 'b.txt',
    });

    // 断言 fetch 被调用且带 X-Session-ID
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe('http://test.local:8989/api/v1/files/rename');
    expect(options.headers['X-Session-ID']).toBe('test-session-id');

    // 断言没有 Authorization header
    expect(options.headers['Authorization']).toBeUndefined();

    // 断言 body 是加密信封，且 token 在解密后的 JSON 内
    const bodyObj = JSON.parse(options.body);
    expect(bodyObj.nonce).toBeTruthy();
    expect(bodyObj.ciphertext).toBeTruthy();

    const decrypted = decryptEnvelope(sessionKey, bodyObj.nonce, bodyObj.ciphertext);
    const parsed = JSON.parse(new TextDecoder().decode(decrypted));
    expect(parsed.token).toBe('test-jwt-token');
    expect(parsed.disk_id).toBe(1);
    expect(parsed.new_name).toBe('b.txt');
  });

  it('GET 请求用真 GET + URL query 携带加密信封（含 token）', async () => {
    const mockResponse = await makeEncryptedResponse(sessionKey, { disks: [] });
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(mockResponse);

    await httpClient.get('/api/v1/files/disks');

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, options] = fetchMock.mock.calls[0];
    // 必须是真 GET 方法（兼容服务端 GET-only 路由）
    expect(options.method).toBe('GET');
    expect(String(url)).toContain('http://test.local:8989/api/v1/files/disks?');
    expect(options.headers['X-Session-ID']).toBe('test-session-id');

    // 加密信封在 URL query 中（nonce/ciphertext），token 在解密后的 JSON 内
    const queryStr = String(url).split('?')[1];
    const params = new URLSearchParams(queryStr);
    const nonce = params.get('nonce')!;
    const ciphertext = params.get('ciphertext')!;
    expect(nonce).toBeTruthy();
    expect(ciphertext).toBeTruthy();

    const decrypted = decryptEnvelope(sessionKey, nonce, ciphertext);
    const parsed = JSON.parse(new TextDecoder().decode(decrypted));
    expect(parsed.token).toBe('test-jwt-token');
  });

  it('成功响应解密后返回业务数据', async () => {
    const respData = { files: [{ name: 'a.txt', is_dir: false }] };
    const mockResponse = await makeEncryptedResponse(sessionKey, respData);
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(mockResponse);

    const result = await httpClient.post<{ files: unknown[] }>('/api/v1/files/list', {
      disk_id: 1,
      path: '',
    });

    expect(result.files).toHaveLength(1);
    expect(result.files[0]).toEqual({ name: 'a.txt', is_dir: false });
  });

  it('加密错误响应解密后抛出 ApiError（detail 正确）', async () => {
    const mockResponse = await makeEncryptedError(sessionKey, 403, '对目标磁盘没有读取权限');
    vi.spyOn(globalThis, 'fetch').mockResolvedValue(mockResponse);

    await expect(
      httpClient.post('/api/v1/files/list', { disk_id: 1, path: '' }),
    ).rejects.toMatchObject({
      status: 403,
      detail: '对目标磁盘没有读取权限',
    });
  });

  it('未建立加密会话时抛 ApiError', async () => {
    clearSession();
    setSessionKey(null);
    setSessionId(null);

    await expect(
      httpClient.post('/api/v1/auth/login', { username: 'a', password: 'b' }),
    ).rejects.toMatchObject({ status: 0 });
  });

  it('明文 POST（key-exchange）不带加密信封', async () => {
    const mockResponse = new Response(
      JSON.stringify({ session_id: 's1', server_public_key: 'dGVzdA==' }),
      { status: 200, headers: { 'Content-Type': 'application/json' } },
    );
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(mockResponse);

    const result = await httpClient.postPlain<{ session_id: string }>(
      '/api/v1/auth/key-exchange',
      { client_public_key: 'dGVzdA==' },
    );

    expect(result.session_id).toBe('s1');
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toContain('/api/v1/auth/key-exchange');
    // 明文请求 body 不是加密信封
    const bodyObj = JSON.parse(options.body);
    expect(bodyObj.nonce).toBeUndefined();
    expect(bodyObj.client_public_key).toBe('dGVzdA==');
  });

  it('GET 超长 path 的 query 信封完整无截断，且 URL 不含明文路径', async () => {
    const mockResponse = await makeEncryptedResponse(sessionKey, { files: [] });
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(mockResponse);

    // 构造超长相对路径（>4KB，验证 URL 长度边界风险 B1）
    const longPath = `dir/${'sub/'.repeat(1200)}target.txt`;
    expect(longPath.length).toBeGreaterThan(4000);

    await httpClient.get('/api/v1/files/list', { disk_id: 1, path: longPath });

    const [url, options] = fetchMock.mock.calls[0];
    expect(options.method).toBe('GET');

    const queryStr = String(url).split('?')[1];
    const params = new URLSearchParams(queryStr);
    // URL 中不含明文路径内容
    expect(String(url)).not.toContain('target.txt');
    expect(String(url)).not.toContain('/sub/');

    // 解密后 path 完整保留（无截断）
    const decrypted = decryptEnvelope(
      sessionKey, params.get('nonce')!, params.get('ciphertext')!,
    );
    const parsed = JSON.parse(new TextDecoder().decode(decrypted));
    expect(parsed.path).toBe(longPath);
    expect(parsed.disk_id).toBe(1);
  });

  it('downloadStream 走真 GET + URL query 信封并返回流式 body', async () => {
    // 构造带 body 的流式响应（stream-frame 帧内容此处不展开，仅验证请求形态）
    const streamBody = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new Uint8Array([1, 2, 3]));
        controller.close();
      },
    });
    const mockResponse = new Response(streamBody, {
      status: 200,
      headers: { 'Content-Type': 'application/octet-stream' },
    });
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue(mockResponse);

    const stream = await httpClient.downloadStream('/api/v1/files/download', {
      disk_id: 1,
      path: 'a.txt',
      filename: 'a.txt',
    });

    // 返回流式响应体
    expect(stream).toBeInstanceOf(ReadableStream);
    const [url, options] = fetchMock.mock.calls[0];
    expect(options.method).toBe('GET');
    expect(options.headers['X-Session-ID']).toBe('test-session-id');
    // 信封在 query，不在 body
    expect(options.body).toBeUndefined();
    const queryStr = String(url).split('?')[1];
    const params = new URLSearchParams(queryStr);
    expect(params.get('nonce')).toBeTruthy();
    expect(params.get('ciphertext')).toBeTruthy();

    // 解密后参数完整（token + 下载参数）
    const decrypted = decryptEnvelope(
      sessionKey, params.get('nonce')!, params.get('ciphertext')!,
    );
    const parsed = JSON.parse(new TextDecoder().decode(decrypted));
    expect(parsed.token).toBe('test-jwt-token');
    expect(parsed.path).toBe('a.txt');
  });
});
