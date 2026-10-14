import { useEffect, useState } from 'react';
import { Button, Modal } from '../ui';

/**
 * 退出确认对话框（v1.6.0 §6.4 / AC-56 ~ AC-59）
 *
 * **触发链路**：Python 侧（托盘「退出」/ 关窗）发布 `shell.quitRequested {timeoutMs}` →
 * 渲染层弹本对话框 → 用户选择后回执 `editor.confirmQuit(retain)` 或 `editor.cancelQuit()`；
 * Python 侧另有**超时兜底**，因此本组件即使不响应也不会把应用卡死（只是走了兜底路径）。
 *
 * **三个选择 + 一个全局勾选**：
 *   - 保存并退出：把**有保存路径**的脏窗格写回原文件。草稿形态（`path === null`）的窗格
 *     **永不静默写盘**（D17 红线），它们的正文仍留在草稿库里；
 *   - 放弃并退出：不写用户文件；草稿库里的自动暂存照旧（是否保留下次恢复由勾选决定）；
 *   - 取消：不退出，编辑器保持打开、内容不动。
 *
 * **文案口径**（§7.4 / `AGENTS.md §9.4.13`）：只报数量与文件名级信息，**不含目录与正文**。
 */
export interface QuitConfirmDialogProps {
  /** 是否显示（由 `shell.quitRequested` 驱动） */
  open: boolean;
  /** 未保存（脏）窗格数 */
  dirtyCount: number;
  /** 其中有保存路径、可写回原文件的窗格数 */
  savableCount: number;
  /** 正在执行保存/退出回执（此期间禁止重复点击） */
  busy: boolean;
  onSaveAndQuit: (retain: boolean) => void;
  onDiscardAndQuit: (retain: boolean) => void;
  onCancel: () => void;
}

export function QuitConfirmDialog({
  open,
  dirtyCount,
  savableCount,
  busy,
  onSaveAndQuit,
  onDiscardAndQuit,
  onCancel,
}: QuitConfirmDialogProps) {
  // 默认**勾选保留**：草稿是意外丢失的最后一道保险（用户主动取消勾选即"下次不恢复"）
  const [retain, setRetain] = useState(true);
  useEffect(() => {
    if (open) setRetain(true);
  }, [open]);

  const draftOnly = Math.max(0, dirtyCount - savableCount);

  return (
    <Modal
      open={open}
      title="退出 JFLove"
      onClose={busy ? () => undefined : onCancel}
      width={520}
      footer={
        <>
          <Button variant="ghost" onClick={onCancel} disabled={busy}>
            取消
          </Button>
          <Button variant="secondary" onClick={() => onDiscardAndQuit(retain)} disabled={busy}>
            放弃并退出
          </Button>
          <Button
            variant="primary"
            onClick={() => onSaveAndQuit(retain)}
            disabled={busy || savableCount === 0}
          >
            保存并退出
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3 text-sm">
        <p>
          {dirtyCount > 0 ? (
            <>有 <strong>{dirtyCount}</strong> 个编辑窗格尚未保存。</>
          ) : (
            <>没有未保存的更改。</>
          )}
        </p>

        {draftOnly > 0 && (
          <p className="text-muted">
            其中 <strong>{draftOnly}</strong> 个窗格尚未指定保存位置（草稿形态）：
            它们<strong>不会</strong>被写入磁盘，正文仍留在草稿库中。
          </p>
        )}

        {dirtyCount > 0 && savableCount === 0 && (
          <p className="text-muted">当前没有可写回原文件的窗格，只能选择「放弃并退出」。</p>
        )}

        <label className="flex items-center gap-2 select-none">
          <input
            type="checkbox"
            checked={retain}
            disabled={busy}
            onChange={(event) => setRetain(event.target.checked)}
          />
          <span>保留下次恢复（下次启动自动还原这些窗格）</span>
        </label>

        <p className="text-muted">
          草稿暂存在本机草稿库中；取消勾选后退出会<strong>清空</strong>草稿库，且不可恢复。
        </p>
      </div>
    </Modal>
  );
}
