/**
 * 服务端地址历史服务测试
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { serverHistoryService, normalizeServerUrl } from '../../src/services/server-history-service';

describe('server-history-service 服务端地址历史', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('无历史时返回空列表', () => {
    expect(serverHistoryService.listHistory()).toEqual([]);
  });

  it('无历史时默认地址为**同源**（空串）', () => {
    // v1.5.0 反馈修复：默认值由写死的 'http://localhost:8989' 改成空串。
    // 写死绝对地址会让每个请求变成跨源请求，浏览器先发 OPTIONS 预检 ——
    // 服务端收到的 HTTP 条数翻倍，用户感知为「接口像被调了两遍」。
    // 空串 ⇒ 走相对路径 `/api/v1/...`，由 nginx / dev proxy 同源反代。
    expect(serverHistoryService.getDefault()).toBe('');
  });

  it('同源模式下请求路径是相对路径（不拼主机名）', async () => {
    const { getServerUrl } = await import('../../src/utils/session');
    expect(getServerUrl()).toBe('');
    // 拼接结果必须是相对路径，否则又会变成跨源
    expect(`${getServerUrl()}/api/v1/auth/key-exchange`).toBe('/api/v1/auth/key-exchange');
  });

  it('记录地址后置顶', () => {
    serverHistoryService.record('http://a.com:8989');
    serverHistoryService.record('http://b.com:8989');

    const history = serverHistoryService.listHistory();
    expect(history).toEqual(['http://b.com:8989', 'http://a.com:8989']);
  });

  it('地址去重（含尾部斜杠）', () => {
    serverHistoryService.record('http://a.com:8989/');
    serverHistoryService.record('http://a.com:8989');

    const history = serverHistoryService.listHistory();
    expect(history).toEqual(['http://a.com:8989']);
  });

  it('超出 10 条按最旧淘汰', () => {
    for (let i = 1; i <= 15; i++) {
      serverHistoryService.record(`http://server${i}.com:8989`);
    }

    const history = serverHistoryService.listHistory();
    expect(history.length).toBe(10);
    // 最新的排在最前
    expect(history[0]).toBe('http://server15.com:8989');
    // 最旧的被淘汰
    expect(history).not.toContain('http://server1.com:8989');
  });

  it('删除某条历史', () => {
    serverHistoryService.record('http://a.com:8989');
    serverHistoryService.record('http://b.com:8989');

    serverHistoryService.delete('http://a.com:8989');

    const history = serverHistoryService.listHistory();
    expect(history).toEqual(['http://b.com:8989']);
  });
});

describe('normalizeServerUrl 服务端地址规范化', () => {
  it('无协议地址自动补全 http://', () => {
    expect(normalizeServerUrl('127.0.0.1:8989')).toBe('http://127.0.0.1:8989');
    expect(normalizeServerUrl('localhost:8989')).toBe('http://localhost:8989');
  });

  it('已带协议地址保持不变（保留 https）', () => {
    expect(normalizeServerUrl('https://jflove.example.com:8989'))
      .toBe('https://jflove.example.com:8989');
    expect(normalizeServerUrl('http://127.0.0.1:8989')).toBe('http://127.0.0.1:8989');
  });

  it('去除首尾空白与尾部斜杠', () => {
    expect(normalizeServerUrl('  http://127.0.0.1:8989/  '))
      .toBe('http://127.0.0.1:8989');
    expect(normalizeServerUrl('127.0.0.1:8989///')).toBe('http://127.0.0.1:8989');
  });

  it('空输入原样返回', () => {
    expect(normalizeServerUrl('')).toBe('');
    expect(normalizeServerUrl('   ')).toBe('');
  });
});
