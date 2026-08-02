/**
 * 服务端地址历史服务测试
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { serverHistoryService } from '../../src/services/server-history-service';

describe('server-history-service 服务端地址历史', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('无历史时返回空列表', () => {
    expect(serverHistoryService.listHistory()).toEqual([]);
  });

  it('无历史时默认地址为 localhost:8989', () => {
    expect(serverHistoryService.getDefault()).toBe('http://localhost:8989');
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
