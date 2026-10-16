/**
 * 编辑器工作区**空态**（设计 v1.6.1 §4.6 末行 / AC-69）
 *
 * ## 为什么必须有它
 *
 * AC-69 要求「关闭最后一个标签页 → **仍停留在文本编辑器**」：工作区不能因为没有标签
 * 就自动跳走（v1.6.0 的浮动层在无窗格时是"什么都不显示"，语义完全不同）。
 * 因此空态不是"没有内容"，而是一个**明确的界面状态**：仍然在 `/editor` 路由，
 * 左侧菜单仍选中「文本编辑器」，并提供两个入口 —— 新建 / 打开。
 *
 * ## 交互
 *
 * - 「新建标签页」= `createTab()`（新建即激活，位于工作区主体）；
 * - 「打开本地文件」= 原生对话框 → `openPathsAndReveal()`（复用**唯一**的唤起链路：
 *   类型校验先行 → `detectAndRead` → `openOrFocus`，AC-65 的同路径去重同样生效）。
 */

import { useCallback, useState } from 'react';
import { scratchService } from '../../services/scratch-service';
import { useScratchStore } from '../../stores/scratch-store';
import { openPathsAndReveal } from '../../utils/scratch/editor-open-paths';

export function ScratchWorkspaceEmpty() {
  /** 打开对话框进行中（防重复点击；原生对话框是模态的，但按钮仍可能被连点） */
  const [opening, setOpening] = useState(false);

  const handleCreate = useCallback(() => {
    useScratchStore.getState().createTab();
  }, []);

  const handleOpen = useCallback(async () => {
    if (opening) return;
    setOpening(true);
    try {
      const paths = await scratchService.pickFiles();
      // 取消不是错误：`pickFiles` 返回空数组时什么都不做（AC-36 的同款口径）
      if (paths.length > 0) await openPathsAndReveal(paths);
    } catch (error) {
      useScratchStore
        .getState()
        .setError(error instanceof Error && error.message ? error.message : '打开文件失败');
    } finally {
      setOpening(false);
    }
  }, [opening]);

  return (
    <div className="scratch-workspace-empty" data-scratch-workspace-empty="true">
      <p className="scratch-workspace-empty-title">没有打开的标签页</p>
      <p className="scratch-workspace-empty-hint">
        新建一个空白标签页，或者打开本地文件开始编辑。
      </p>
      <div className="scratch-workspace-empty-actions">
        <button type="button" className="btn btn-primary" onClick={handleCreate}>
          新建标签页
        </button>
        <button
          type="button"
          className="btn btn-ghost"
          onClick={() => void handleOpen()}
          disabled={opening}
        >
          打开文件
        </button>
      </div>
    </div>
  );
}
