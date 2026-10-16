/**
 * 标签条**溢出下拉菜单**（设计 v1.6.1 §4.6：标签条放不下时用下拉列表兜底）
 *
 * ## 职责
 *
 * 把 store 里的**全量**标签按**标签条顺序**（`tabs` 数组顺序即标签条顺序，DS-18）列成一屏：
 * 每一项是一个原生 `<button>`，点击 = `setActiveTab(id)` 然后 `onClose()`。
 * 列表可竖向滚动（`max-height` + `overflow-y: auto`，见 `styles/scratch-editor.css`
 * 的 `.scratch-tab-overflow-menu`）—— 标签再多也点得到。
 *
 * ## 边界（刻意不做的事）
 *
 * - **不自己管开关**：开 / 关（含 `Escape` 与点击菜单外区域）由父组件 `ScratchTabBar` 用
 *   `useState` 持有，本组件只收 `onClose` 回调（契约如此，也让"点击后关闭"有唯一出口）。
 * - **不提供关闭（×）**：溢出菜单只负责**切换激活**；关闭脏标签要过 AC-59 的确认弹窗，
 *   那条链路在 workspace 层，标签条侧统一走 `onRequestClose`（本菜单不掺和）。
 * - **不渲染路径**：`title` 只有文件名（§9.4 不暴露完整路径），`title` 属性同样只用它。
 *
 * ## DOM 契约（§4.2）
 *
 * | 属性 | 含义 |
 * | --- | --- |
 * | `data-scratch-overflow-item={tab.id}` | 每项的稳定选择器（测试 / 自动化按它取项） |
 * | `.scratch-tab-overflow-item-active` | 当前激活项额外带这一类名 |
 */

import { useScratchStore } from '../../stores/scratch-store';
import { Icon } from '../ui';

/**
 * 标签溢出下拉菜单。
 *
 * 签名按契约逐字固定：`{ onClose: () => void }`（不额外导出 props 类型，避免与父 agent
 * 在 `components/scratch/index.ts` 的再导出约定打架）。
 *
 * @example
 * {open && <ScratchTabOverflowMenu onClose={() => setOpen(false)} />}
 */
export function ScratchTabOverflowMenu({ onClose }: { onClose: () => void }): JSX.Element {
  // 直接订阅 store：标签在菜单打开期间被新建 / 关闭 / 改名时列表会跟着变（顺序也一致）
  const tabs = useScratchStore((state) => state.tabs);
  const activeTabId = useScratchStore((state) => state.activeTabId);

  return (
    <div className="scratch-tab-overflow-menu" role="menu" aria-label="全部标签页">
      {tabs.map((tab) => {
        const active = tab.id === activeTabId;
        return (
          <button
            key={tab.id}
            type="button"
            role="menuitemradio"
            // 单选语义：这一项是不是"当前激活"的那一个
            aria-checked={active}
            aria-label={tab.title}
            title={tab.title}
            className={
              active
                ? 'scratch-tab-overflow-item scratch-tab-overflow-item-active'
                : 'scratch-tab-overflow-item'
            }
            data-scratch-overflow-item={tab.id}
            onClick={() => {
              // 契约顺序：先切换激活，再关菜单（关菜单由父组件执行）
              useScratchStore.getState().setActiveTab(tab.id);
              onClose();
            }}
          >
            <Icon name="file" size="sm" className="scratch-tab-overflow-icon" />
            <span className="scratch-tab-overflow-name">{tab.title}</span>
            {/* 未保存标记：与标签条上的圆点同一语义、同一类名（复用点样式） */}
            {tab.dirty && <span className="scratch-tab-dirty" aria-hidden="true" />}
          </button>
        );
      })}
    </div>
  );
}
