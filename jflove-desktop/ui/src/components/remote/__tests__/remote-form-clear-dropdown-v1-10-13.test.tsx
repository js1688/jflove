/**
 * 远程连接表单：凭据清空 + 跳板页内下拉（v1.10.13）
 *
 * ## 判据
 *
 * 1. 编辑态点「清空」后保存，payload 必须带 `clear_password` /
 *    `clear_private_key` / `clear_passphrase`（服务端才会把列写成空串）；
 * 2. 新建态清空只清本地输入，不得带清空标志（否则服务端可能误清历史值）；
 * 3. 跳板连接不再使用原生 `<select>`；选项按 id 去重、排除循环；
 * 4. 选择跳板后保存，`jump_connection_id` 正确。
 */

import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

const { callBridge } = vi.hoisted(() => ({ callBridge: vi.fn() }));

vi.mock('../../../utils/desktop-bridge', () => ({
  callBridge,
  isBridgeReady: () => true,
  onBridgeEvent: () => () => undefined,
  getChannelObject: () => null,
}));

import { RemoteConnectionForm } from '../RemoteConnectionForm';
import type { RemoteConnectionDetail } from '../../../services/remote-service';

function detail(overrides: Partial<RemoteConnectionDetail> = {}): RemoteConnectionDetail {
  return {
    id: 5,
    ssh_group_id: 0,
    name: 'au-fiserv',
    host: 'gateway.example.com',
    port: 22,
    username: 'APGW-KPAYG001',
    auth_type: 'password_publickey',
    mode: 'sftp',
    jump_connection_id: 0,
    terminal_type: 'xterm-256color',
    charset: 'UTF-8',
    init_command: '',
    host_key_fingerprint: '',
    private_key_fingerprint: 'SHA256:abc',
    has_password: true,
    has_private_key: true,
    ...overrides,
  };
}

/** 取字段容器（label → 标题行 → 容器；认证按钮同名但容器里没有输入控件） */
function fieldBox(label: string): HTMLElement {
  const labelEl = screen.getAllByText(label).find((element) => {
    const wrapper = element.parentElement?.parentElement;
    return Boolean(wrapper?.querySelector('input, textarea'));
  });
  const wrapper = labelEl?.parentElement?.parentElement;
  if (!wrapper) throw new Error(`找不到字段容器：${label}`);
  return wrapper;
}

function clearButton(label: string): HTMLButtonElement {
  const wrapper = fieldBox(label);
  const button = Array.from(wrapper.querySelectorAll('button')).find(
    (item) => item.textContent?.trim() === '清空',
  );
  if (!button) throw new Error(`找不到清空按钮：${label}`);
  return button as HTMLButtonElement;
}

function jumpButton(): HTMLButtonElement {
  return screen.getByTestId('jump-select') as HTMLButtonElement;
}

async function clickSave(onSave: ReturnType<typeof vi.fn>) {
  fireEvent.click(screen.getByRole('button', { name: '保存' }));
  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  return (onSave.mock.calls[0] as unknown[])[0] as Record<string, unknown>;
}

afterEach(() => {
  cleanup();
  callBridge.mockReset();
  vi.clearAllMocks();
});

describe('RemoteConnectionForm · 凭据清空（v1.10.13）', () => {
  it('清空已保存的密码 → 保存 payload 带 clear_password，且不发 password 原文', async () => {
    const onSave = vi.fn(async () => 5);
    render(
      <RemoteConnectionForm
        mode="edit"
        detail={detail()}
        connections={[]}
        onCancel={vi.fn()}
        onSave={onSave}
      />,
    );
    fireEvent.click(clearButton('密码'));
    expect(screen.getByText('已标记清空：保存后服务端密码列写为空')).toBeTruthy();

    const payload = await clickSave(onSave);
    expect(payload.clear_password).toBe(true);
    expect(payload.password).toBeUndefined();
  });

  it('清空私钥 → clear_private_key（服务端连带清口令），不再提交口令', async () => {
    const onSave = vi.fn(async () => 5);
    render(
      <RemoteConnectionForm
        mode="edit"
        detail={detail()}
        connections={[]}
        onCancel={vi.fn()}
        onSave={onSave}
      />,
    );
    fireEvent.click(clearButton('私钥内容或文件路径'));
    const payload = await clickSave(onSave);
    expect(payload.clear_private_key).toBe(true);
    expect(payload.private_key).toBeUndefined();
    expect(payload.passphrase).toBeUndefined();
  });

  it('只清口令 → clear_passphrase，私钥保留（不提交 clear_private_key）', async () => {
    const onSave = vi.fn(async () => 5);
    render(
      <RemoteConnectionForm
        mode="edit"
        detail={detail()}
        connections={[]}
        onCancel={vi.fn()}
        onSave={onSave}
      />,
    );
    fireEvent.click(clearButton('私钥口令（可选）'));
    const payload = await clickSave(onSave);
    expect(payload.clear_passphrase).toBe(true);
    expect(payload.clear_private_key).toBeUndefined();
  });

  it('新建态清空密码不发 clear_password；输入新密码会撤销清空标记', () => {
    render(
      <RemoteConnectionForm
        mode="create"
        connections={[]}
        onCancel={vi.fn()}
        onSave={vi.fn(async () => 1)}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '密码 + 密钥' }));
    const passwordInput = fieldBox('密码').querySelector('input') as HTMLInputElement;
    fireEvent.change(passwordInput, { target: { value: 'first-wrong' } });
    fireEvent.click(clearButton('密码'));
    expect(passwordInput.value).toBe('');
    // 新建态没有「保存后清空」语义
    expect(screen.queryByText(/已标记清空/)).toBeNull();
  });
});

describe('RemoteConnectionForm · 跳板页内下拉（v1.10.13）', () => {
  it('不使用原生 select；重复连接只渲染一次；选择后保存 jump_connection_id', async () => {
    const onSave = vi.fn(async () => 5);
    const connections = [
      { id: 1, name: '跳板A', jump_connection_id: 0 },
      { id: 1, name: '跳板A（重复）', jump_connection_id: 0 },
      { id: 2, name: '跳板B', jump_connection_id: 1 },
    ];
    render(
      <RemoteConnectionForm
        mode="edit"
        detail={detail()}
        connections={connections}
        onCancel={vi.fn()}
        onSave={onSave}
      />,
    );
    const form = screen.getByRole('complementary');
    expect(form.querySelector('select')).toBeNull();

    fireEvent.click(jumpButton());
    const listbox = screen.getByRole('listbox');
    expect(within(listbox).getAllByText('跳板A').length).toBe(1);
    expect(within(listbox).getByText('跳板B')).toBeTruthy();

    fireEvent.click(within(listbox).getByText('跳板B'));
    expect(jumpButton().textContent).toContain('跳板B');

    const payload = await clickSave(onSave);
    expect(payload.jump_connection_id).toBe(2);
  });

  it('排除会形成循环的下游连接（候选以当前连接为跳板）', () => {
    render(
      <RemoteConnectionForm
        mode="edit"
        detail={detail({ id: 5 })}
        connections={[
          { id: 1, name: '会成环', jump_connection_id: 5 },
          { id: 2, name: '正常跳板', jump_connection_id: 0 },
        ]}
        onCancel={vi.fn()}
        onSave={vi.fn(async () => 5)}
      />,
    );
    fireEvent.click(jumpButton());
    const listbox = screen.getByRole('listbox');
    expect(within(listbox).queryByText('会成环')).toBeNull();
    expect(within(listbox).getByText('正常跳板')).toBeTruthy();
  });

  it('点击 select 外部会收起列表（页内列表不是系统弹层）', async () => {
    render(
      <RemoteConnectionForm
        mode="create"
        connections={[{ id: 1, name: '跳板A', jump_connection_id: 0 }]}
        onCancel={vi.fn()}
        onSave={vi.fn(async () => 1)}
      />,
    );
    fireEvent.click(jumpButton());
    expect(screen.getByRole('listbox')).toBeTruthy();
    await act(async () => {
      fireEvent.pointerDown(document.body);
    });
    expect(screen.queryByRole('listbox')).toBeNull();
  });
});
