/**
 * 远程连接表单：私钥选文件 → token 入库 / 密码 + 密钥双因素（v1.10.11）
 *
 * ## 判据出处
 *
 * 用户实测要求（2026-11-17）：
 * 1. 点「选择文件…」后由 Python 读取文件**内容**保存到服务端，配置里**不存本地路径**；
 * 2. 「用户名 + 密码 + 私钥 + 私钥口令」要能同时配置；
 * 3. 保存前先校验私钥（格式/口令错误当场提示，别等到连接）。
 *
 * 渲染层只应拿到 token；`private_key` 字段必须只在**粘贴文本**时出现。
 */

import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const { callBridge } = vi.hoisted(() => ({ callBridge: vi.fn() }));

vi.mock('../../../utils/desktop-bridge', () => ({
  callBridge,
  isBridgeReady: () => true,
  onBridgeEvent: () => () => undefined,
  getChannelObject: () => null,
}));

import { RemoteConnectionForm } from '../RemoteConnectionForm';

function field(label: string): HTMLInputElement | HTMLTextAreaElement {
  // v1.10.13 起字段标题行另起一层：label → 标题行 → 字段容器（输入控件在同一容器内）。
  // 认证方式按钮也可能叫「密码」，但它所在的字段容器没有输入控件，借此区分。
  const labelEl = screen.getAllByText(label).find((element) => {
    const fieldWrapper = element.parentElement?.parentElement;
    return Boolean(fieldWrapper?.querySelector('input, textarea'));
  });
  const element = labelEl?.parentElement?.parentElement?.querySelector('input, textarea');
  if (!element) throw new Error(`找不到字段：${label}`);
  return element as HTMLInputElement | HTMLTextAreaElement;
}

function savedPayload(mock: unknown): Record<string, unknown> {
  const calls = (mock as { mock: { calls: Array<[Record<string, unknown>]> } }).mock.calls;
  return calls[0][0];
}

function baseProps() {
  return {
    mode: 'create' as const,
    connections: [],
    onCancel: vi.fn(),
    onSave: vi.fn(async () => 1),
  };
}

async function fillConnection() {
  fireEvent.change(field('名称'), { target: { value: 'fiserv-test' } });
  fireEvent.change(field('主机'), { target: { value: 'test2-gw-apac.firstdataclients.com' } });
  fireEvent.change(field('用户名'), { target: { value: 'APGW-KPAYG001' } });
}

async function chooseKeyAuth() {
  fireEvent.click(screen.getByRole('button', { name: '密码 + 密钥' }));
}

afterEach(() => {
  cleanup();
  callBridge.mockReset();
});

describe('RemoteConnectionForm · v1.10.11', () => {
  it('「密码 + 密钥」同时显示密码、私钥、私钥口令', () => {
    render(<RemoteConnectionForm {...baseProps()} />);
    chooseKeyAuth();
    expect(field('密码')).toBeTruthy();
    expect(field('私钥内容或文件路径').tagName).toBe('TEXTAREA');
    expect(screen.getByText('私钥口令（可选）')).toBeTruthy();
  });

  it('选文件后提交 token（不提交本地路径），保存前先校验', async () => {
    callBridge.mockImplementation(async (_service: string, method: string) => {
      if (method === 'pick_private_key') {
        return { token: 'tok-1', name: 'au-fiserv.pem', encrypted: true };
      }
      if (method === 'validate_private_key') return { ok: true };
      throw new Error(`unexpected bridge: ${method}`);
    });
    const props = baseProps();
    render(<RemoteConnectionForm {...props} />);
    await fillConnection();
    chooseKeyAuth();

    fireEvent.change(field('密码'), { target: { value: '9tT[$h6Lm' } });
    fireEvent.change(field('私钥口令（可选）'), { target: { value: 'zC5+aL4$lV1=' } });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '选择文件…' }));
    });
    expect(screen.getByText(/au-fiserv\.pem/)).toBeTruthy();

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '保存' }));
    });

    await waitFor(() => expect(props.onSave).toHaveBeenCalledTimes(1));
    expect(callBridge).toHaveBeenCalledWith(
      'remote',
      'validate_private_key',
      expect.objectContaining({ private_key_file_token: 'tok-1', passphrase: 'zC5+aL4$lV1=' }),
    );
    const payload = savedPayload(props.onSave);
    expect(payload).toMatchObject({
      auth_type: 'password_publickey',
      password: '9tT[$h6Lm',
      passphrase: 'zC5+aL4$lV1=',
      private_key_file_token: 'tok-1',
    });
    // 关键：不能把本地路径/空内容写进 private_key
    expect(payload.private_key).toBeUndefined();
  });

  it('粘贴文本时提交 private_key（不发 token）', async () => {
    callBridge.mockResolvedValue({ ok: true });
    const props = baseProps();
    render(<RemoteConnectionForm {...props} />);
    await fillConnection();
    chooseKeyAuth();

    fireEvent.change(field('密码'), { target: { value: 'login-pw' } });
    fireEvent.change(field('私钥内容或文件路径'), {
      target: { value: '-----BEGIN RSA PRIVATE KEY-----\nAAAA\n-----END RSA PRIVATE KEY-----' },
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '保存' }));
    });

    await waitFor(() => expect(props.onSave).toHaveBeenCalledTimes(1));
    const payload = savedPayload(props.onSave);
    expect(payload.private_key).toContain('BEGIN RSA PRIVATE KEY');
    expect(payload.private_key_file_token).toBeUndefined();
  });

  it('私钥校验失败 ⇒ 显示原因且不保存', async () => {
    callBridge.mockRejectedValue(new Error('私钥口令错误，或私钥文件已损坏'));
    const props = baseProps();
    render(<RemoteConnectionForm {...props} />);
    await fillConnection();
    chooseKeyAuth();

    fireEvent.change(field('密码'), { target: { value: 'login-pw' } });
    fireEvent.change(field('私钥内容或文件路径'), {
      target: { value: '-----BEGIN RSA PRIVATE KEY-----\nAAAA\n-----END RSA PRIVATE KEY-----' },
    });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '保存' }));
    });

    expect(await screen.findByText(/私钥口令错误/)).toBeTruthy();
    expect(props.onSave).not.toHaveBeenCalled();
  });

  it('新建时双因素缺私钥 ⇒ 前端拦截，不发请求', async () => {
    const props = baseProps();
    render(<RemoteConnectionForm {...props} />);
    await fillConnection();
    chooseKeyAuth();
    fireEvent.change(field('密码'), { target: { value: 'login-pw' } });

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '保存' }));
    });

    expect(await screen.findByText(/需要私钥/)).toBeTruthy();
    expect(callBridge).not.toHaveBeenCalled();
    expect(props.onSave).not.toHaveBeenCalled();
  });

  /**
   * v1.10.12：选到 `.ppk` 必须**当场有反应**。
   *
   * 旧实现把桥抛出的拒绝原因写进抽屉最底部的 `error`（表单比可视区高，看不到），
   * 用户实测观感是「选 ppk 毫无反应」。现在必须在私钥字段内就地显示。
   */
  it('选到 .ppk（桥拒绝）⇒ 私钥字段内就地显示转换指引', async () => {
    callBridge.mockRejectedValue(
      new Error(
        '这是 PuTTY 私钥（.ppk），当前版本不直接支持。'
          + '请用 PuTTYgen 打开它并执行 Conversions → Export OpenSSH key 导出成 .pem 后再选择',
      ),
    );
    render(<RemoteConnectionForm {...baseProps()} />);
    await fillConnection();
    chooseKeyAuth();

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: '选择文件…' }));
    });

    const hint = await screen.findByText(/PuTTY 私钥/);
    // 提示必须落在「私钥内容或文件路径」字段块内（与选择按钮同一块），而不是抽屉底部
    const keyField = field('私钥内容或文件路径').parentElement as HTMLElement;
    expect(keyField.contains(hint)).toBe(true);
    expect(hint.textContent).toMatch(/Conversions → Export OpenSSH key/);
    // 仍然没有私钥 ⇒ 不能提交
    expect(callBridge).not.toHaveBeenCalledWith('remote', 'validate_private_key', expect.anything());
  });
});
