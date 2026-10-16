/**
 * 编辑器**顶部标签条**（设计 v1.6.1 §4.6 交互 / §4.2 DOM 契约）
 *
 * v1.6.1 把编辑器从"浮动窗格"改成"整页工作区 + 顶部固定标签页（VSCode 式）"，
 * 本文件就是那条**固定单行**的标签条：横向排布全部标签，放不下时横向滚动（AC-67 ①）。
 *
 * ## 与 store 的关系
 *
 * 契约规定本组件**自读 store、不接 store 相关的 props**：顺序、激活项、增删都直接对
 * `useScratchStore` 说话。**不改 store**（数组顺序即标签条顺序，DS-18）。
 * 唯一的外部输入是 `onRequestClose` —— 关闭脏标签要先过 AC-59 的「保存 / 放弃 / 取消」
 * 确认弹窗，那条链路由 workspace 层接线，标签条**不自己造对话框**、也不直接调 `closeTab`。
 *
 * ## 六件事（逐条对应验收判据）
 *
 * | # | 行为 | 依据 |
 * |---|------|------|
 * | ① | 标签 = 原生 `<button>`（`Tab` 可聚焦、`Enter`/`Space` 激活），带 `data-scratch-tab` | §4.2 DOM 契约 / AC-66 |
 * | ② | 激活项额外带 `data-scratch-tab-active="true"` + `.scratch-tab-active` | §4.2 DOM 契约 |
 * | ③ | 标题省略号 + `dirty` 圆点 `.scratch-tab-dirty` + `×` 关闭 `.scratch-tab-close` | §4.6 |
 * | ④ | 激活变化时 `scrollIntoView({ inline: 'nearest' })` | AC-67 ③「不允许激活了却看不见」 |
 * | ⑤ | **Pointer Events 自绘拖拽排序**（4 px 阈值 / 中点判定 / `Escape` 取消），禁用 HTML5 `draggable` | §4.6 / AC-68 |
 * | ⑥ | 右侧常驻溢出按钮 → 下拉全量标签；最右端常驻"新建标签页" | §4.6 / AC-67 ① |
 *
 * ## 布局说明（对契约的一处**已知偏差**，理由在 CSS 里有同款注释）
 *
 * 契约要求「根元素 `.scratch-tab-bar`：`flex-shrink: 0` + `overflow-x: auto`」。
 * 但 `overflow-x: auto` 会让该元素成为**滚动容器**，而滚动容器会**裁剪**其内所有
 * 绝对定位后代（CSS 2.1 §11.1.1）—— 下拉菜单 `.scratch-tab-overflow-menu` 恰好是这种后代，
 * 放进去就会被裁掉、并在标签条上多出一条竖向滚动条。
 * 因此这里把滚动交给内层 `.scratch-tab-strip`（`overflow-x: auto`），
 * 根元素 `.scratch-tab-bar` 保留 `flex-shrink: 0`（仍是"固定单行、不随内容滚动"的那一条），
 * 下拉菜单与两个常驻按钮都在滚动容器**之外**，永不被裁剪。
 * 结构：`.scratch-tab-bar` > [`.scratch-tab-strip`(滚动) 、 `.scratch-tab-new` 、
 * `.scratch-tab-overflow`(按钮 + 菜单)]。
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent } from 'react';
import { useScratchStore } from '../../stores/scratch-store';
import { Icon } from '../ui';
import { ScratchTabOverflowMenu } from './ScratchTabOverflowMenu';

/**
 * 进入拖拽态的位移阈值（px，设计 §4.6 原文「位移 > 4 px」）。
 *
 * 为什么是 4：鼠标按下时手会有 1–2 px 的抖动，阈值太小会把"想点击激活"误判成"想排序"，
 * 标签条顺序被莫名其妙改掉；阈值太大（如 10 px）又会让短距离换位变得迟钝。
 * 4 px 与系统"拖拽起始容差"（Windows 为 4 px）一致，是点击与拖拽的分界点。
 */
const DRAG_THRESHOLD_PX = 4;

/** 一次拖拽会话的全部可变状态（全放 `ref`：`pointermove` 每秒几十次，进 `state` 只会白刷帧） */
interface DragSession {
  /** 被拖拽的标签 id */
  tabId: string;
  /** 触发本次会话的指针 id（多点触控时用来过滤别的指针） */
  pointerId: number;
  /** 按下时的横坐标（判 4 px 阈值用） */
  startX: number;
  /** 是否已经越过阈值进入拖拽态（`false` = 还只是"按下"） */
  dragging: boolean;
  /** 拖拽开始前的顺序快照（`Escape` / `pointercancel` 取消时按它还原） */
  originalOrder: string[];
}

export function ScratchTabBar({
  onRequestClose,
}: {
  onRequestClose: (tabId: string) => void;
}): JSX.Element {
  const tabs = useScratchStore((state) => state.tabs);
  const activeTabId = useScratchStore((state) => state.activeTabId);
  const setActiveTab = useScratchStore((state) => state.setActiveTab);
  const createTab = useScratchStore((state) => state.createTab);

  /** 正在被拖拽的标签 id（只为加 `.scratch-tab-dragging` 这一类名而存在） */
  const [dragTabId, setDragTabId] = useState<string | null>(null);
  /** 溢出下拉菜单开关（契约：由本组件 `useState` 管理） */
  const [overflowOpen, setOverflowOpen] = useState(false);

  /** 标签按钮元素表（key = 标签 id）：`scrollIntoView` 与拖拽中点判定都要按 id 取 DOM */
  const tabRefs = useRef(new Map<string, HTMLElement>());
  /** 当前拖拽会话；`null` = 没有指针按下 */
  const dragRef = useRef<DragSession | null>(null);
  /** 溢出按钮 + 菜单的外壳：点击"菜单外区域"判定用（两者同在壳内，故一个 ref 够） */
  const overflowRef = useRef<HTMLDivElement | null>(null);

  /**
   * 取"指针横坐标落在第几个标签的位置上"（拖拽目标索引）。
   *
   * 规则 = **中点判定**：目标 = 满足 `中点 <= clientX` 的**最大**索引（无一满足则 0）。
   * 它等价于"把被拖的标签插到该位置"，与 store 的 `reorderTab(from, to)`
   * （splice 抽出再插入到 `to`）语义严格对齐，且左右拖动对称：
   * `[A,B,C]` 把 A 拖到 B、C 之间 ⇒ 目标 1 ⇒ `[B,A,C]`；把 C 拖到最前 ⇒ 目标 0 ⇒ `[C,A,B]`。
   *
   * 每次判定都**重新读 store**（`getState()`）而不是闭包里的 `tabs`：拖拽期间顺序在实时变，
   * 闭包里的数组是上一帧的，按它算索引会错位。
   */
  const overIndexAt = useCallback((clientX: number): number => {
    const list = useScratchStore.getState().tabs;
    if (list.length === 0) return -1;
    let target = 0;
    for (let index = 0; index < list.length; index += 1) {
      const element = tabRefs.current.get(list[index].id);
      if (!element) continue;
      const rect = element.getBoundingClientRect();
      if (clientX >= rect.left + rect.width / 2) target = index;
    }
    return target;
  }, []);

  /**
   * 按快照还原标签顺序（`Escape` / `pointercancel` 的取消路径）。
   *
   * 逐位归位：第 `i` 个快照 id 若不在第 `i` 位，就把它 `reorderTab` 过去；
   * 每一位处理完都重新 `findIndex`（顺序在变，索引不能用缓存）。
   * 不走"先清空再重建"——store 没有那种 action，逐位归位的结果与快照严格一致。
   */
  const restoreOrder = useCallback((order: readonly string[]): void => {
    order.forEach((id, index) => {
      const store = useScratchStore.getState();
      const current = store.tabs.findIndex((tab) => tab.id === id);
      if (current >= 0 && current !== index) store.reorderTab(current, index);
    });
  }, []);

  /** 结束本次拖拽会话：释放指针捕获、清 `.scratch-tab-dragging`、丢掉会话 */
  const endDrag = useCallback((session: DragSession): void => {
    const element = tabRefs.current.get(session.tabId);
    if (element && element.hasPointerCapture(session.pointerId)) {
      element.releasePointerCapture(session.pointerId);
    }
    dragRef.current = null;
    setDragTabId(null);
  }, []);

  /** 取消拖拽（`Escape` / `pointercancel`）：恢复按下前的顺序，且**不**触发点击激活 */
  const cancelDrag = useCallback(() => {
    const session = dragRef.current;
    if (!session) return;
    endDrag(session);
    if (session.dragging) restoreOrder(session.originalOrder);
  }, [endDrag, restoreOrder]);

  // ── ④ 激活项必须看得见（AC-67 ③）：切标签 / 关标签后把它滚进可视区 ──
  useEffect(() => {
    if (!activeTabId) return;
    const element = tabRefs.current.get(activeTabId);
    if (!element) return;
    // `inline: 'nearest'` = 只在需要时横向微调，不做"每次切换都居中"的跳动；
    // `block: 'nearest'` 保证不牵连页面纵向滚动。
    // jsdom 等环境可能没有实现 `scrollIntoView`：缺失时静默跳过（不影响激活本身）。
    if (typeof element.scrollIntoView === 'function') {
      element.scrollIntoView({ inline: 'nearest', block: 'nearest' });
    }
  }, [activeTabId]);

  // ── 溢出菜单：点击菜单外区域关闭 ──
  useEffect(() => {
    if (!overflowOpen) return;
    const onPointerDown = (event: PointerEvent) => {
      const host = overflowRef.current;
      // 壳内（按钮或菜单本身）⇒ 不算"外面"：按钮的 onClick 自己负责开/关切换
      if (host && event.target instanceof Node && host.contains(event.target)) return;
      setOverflowOpen(false);
    };
    // 捕获阶段：不管点在哪一层，都在别的处理器之前先把菜单收掉
    document.addEventListener('pointerdown', onPointerDown, true);
    return () => document.removeEventListener('pointerdown', onPointerDown, true);
  }, [overflowOpen]);

  // ── `Escape`：拖拽中先取消拖拽（还原顺序），否则关溢出菜单 ──
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      if (dragRef.current?.dragging) {
        event.preventDefault();
        cancelDrag();
        return;
      }
      if (overflowOpen) setOverflowOpen(false);
    };
    window.addEventListener('keydown', onKeyDown, true);
    return () => window.removeEventListener('keydown', onKeyDown, true);
  }, [cancelDrag, overflowOpen]);

  // ── ⑤ 拖拽排序（Pointer Events 自绘；**刻意不用** HTML5 `draggable`/`dragstart`）──
  // 原因：HTML5 拖放只给"节点 id 字符串"，拿不到实时坐标（中点判定做不了），
  // 且拖动影像由浏览器接管、`Escape` 取消与 4 px 阈值都不可控。Pointer Events 全程可控。

  /** 按下：只记录会话，**还不**进入拖拽态（是否拖由 4 px 阈值决定） */
  const handlePointerDown = useCallback(
    (event: ReactPointerEvent<HTMLElement>, tabId: string) => {
      if (event.button !== 0) return; // 只响应主键（右键 / 中键不参与排序）
      if (event.target instanceof Element && event.target.closest('.scratch-tab-close')) {
        return; // 落在 `×` 上：那是关闭，不是拖拽 / 激活
      }
      dragRef.current = {
        tabId,
        pointerId: event.pointerId,
        startX: event.clientX,
        dragging: false,
        originalOrder: useScratchStore.getState().tabs.map((tab) => tab.id),
      };
    },
    [],
  );

  /** 移动：越过 4 px 阈值才算拖拽，之后按中点判定实时重排（预览即最终顺序） */
  const handlePointerMove = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => {
      const session = dragRef.current;
      if (!session || session.pointerId !== event.pointerId) return;

      if (!session.dragging) {
        // 阈值内 = 手抖，仍然按"点击"处理（松手时激活），这里什么都不做
        if (Math.abs(event.clientX - session.startX) <= DRAG_THRESHOLD_PX) return;
        session.dragging = true;
        // 指针捕获：指针移出标签（甚至移出窗口）后仍能收到 move / up，
        // 否则快速拖动会丢 `pointerup`，标签条卡在拖拽态。
        try {
          event.currentTarget.setPointerCapture(session.pointerId);
        } catch {
          // 捕获失败（指针已失效等）不致命：退化为"只在标签上方有效"的拖拽
        }
        setDragTabId(session.tabId);
      }

      const target = overIndexAt(event.clientX);
      if (target < 0) return;
      const store = useScratchStore.getState();
      const current = store.tabs.findIndex((tab) => tab.id === session.tabId);
      if (current < 0 || current === target) return;
      // 实时预览顺序即可（数组顺序即最终顺序，抬起时无需再提交一次）
      store.reorderTab(current, target);
    },
    [overIndexAt],
  );

  /** 抬起：拖拽过 ⇒ 顺序已生效；没拖过（≤4 px）⇒ 视为点击激活 */
  const handlePointerUp = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => {
      const session = dragRef.current;
      if (!session || session.pointerId !== event.pointerId) return;
      const wasDragging = session.dragging;
      endDrag(session);
      if (wasDragging) return; // 拖拽的"提交"就是实时重排后的当前顺序
      setActiveTab(session.tabId);
    },
    [endDrag, setActiveTab],
  );

  /**
   * 指针被系统取消（手势抢占 / 触屏滚动接管 / 窗口失焦）：
   * 与 `Escape` 同语义 —— 恢复按下前的顺序，不激活。
   * 理由：手势被抢走时用户意图不明，恢复原序比留下半截新序更可预期。
   */
  const handlePointerCancel = useCallback(() => {
    cancelDrag();
  }, [cancelDrag]);

  return (
    // 根元素：契约要求的「固定单行」——`flex-shrink: 0` 让它不被上下内容压扁
    <div className="scratch-tab-bar">
      {/* 标签本体：唯一的滚动容器（`overflow-x: auto`），拖拽 / 激活 / 关闭都在这里 */}
      <div className="scratch-tab-strip" role="tablist" aria-label="已打开的标签页">
        {tabs.map((tab) => {
          const active = tab.id === activeTabId;
          const dragging = tab.id === dragTabId;
          return (
            // 外层用 `div[role="tab"]` 而**不是** `<button>`（Phase 5 审查 M-2）：
            // 关闭控件必须是独立的 `<button>`，而 `<button>` 里再嵌 `<button>` 是非法内容模型
            // （React 会打印 `validateDOMNesting` 警告）。键盘语义在这里手写补回：
            // `tabIndex=0` 可聚焦 + `onKeyDown` 处理 Enter/Space（div 不会由浏览器合成 click）。
            <div
              key={tab.id}
              role="tab"
              aria-selected={active}
              tabIndex={0}
              title={tab.title}
              className={[
                'scratch-tab',
                active ? 'scratch-tab-active' : '',
                dragging ? 'scratch-tab-dragging' : '',
              ]
                .filter(Boolean)
                .join(' ')}
              // DOM 契约：`data-scratch-tab` 恒有，激活项额外带 `data-scratch-tab-active`
              data-scratch-tab={tab.id}
              data-scratch-tab-active={active ? 'true' : undefined}
              ref={(element) => {
                // 回调 ref：挂载时登记、卸载时摘除（`Map` 按 id 取，顺序永远跟随 store）
                if (element) tabRefs.current.set(tab.id, element);
                else tabRefs.current.delete(tab.id);
              }}
              onKeyDown={(event) => {
                // ── P1：标签条上**纯键盘可达**的关闭路径（焦点在标签本体上按 `Delete`）──
                // ① **组件内**键盘路径：`Delete` **不进** `SHORTCUT_DEFS`（`config/shortcuts.ts`）、
                //    不进设置页的快捷键列表 ⇒ **不可改键**。理由：它只在"焦点落在标签本体"这一
                //    极窄上下文里成立；登记成可配置项会给出"工作区任意位置都能按"的假象。
                // ② **不**给 `×` 去掉 `tabIndex={-1}`（那也能键盘关闭）：`×` 每标签一个，放开后会
                //    **给每个标签多一个 Tab 停靠点**，与标签本体的停靠点语义重复（越过一个标签要按
                //    两次 Tab），反而更难用；故 `×` 保持 `-1`，关闭键由标签本体承载（停靠点数量不变）。
                // ③ **不**把 `Ctrl+W`（`pane.close`）放大到整个工作区：用户裁决 V-1 —— 保持
                //    `pane.close` 的**面板作用域**（`utils/scratch/shortcut-runtime.ts:155-158`
                //    要求事件目标在 `[data-editor-panel]` 内，否则**刻意**放行给宿主）；
                //    在工作区任意位置吞掉 `Ctrl+W` 会与宿主窗口的关闭语义冲突。
                // ④ 与 `×` 走**同一个** `onRequestClose`：脏标签由 store 的 `requestCloseTab` 落到
                //    `pendingCloseId`、由工作区弹「保存 / 放弃 / 取消」⇒ 本组件**不自己写询问逻辑**。
                // ⑤ 只加 `Delete`，**不加** `Backspace`：后者在 CM6 / 宿主里另有语义，混进来会纠缠。
                if (event.key === 'Delete') {
                  event.preventDefault(); // 拦掉宿主 / 浏览器对 `Delete` 的默认动作
                  onRequestClose(tab.id);
                  return;
                }
                if (event.key !== 'Enter' && event.key !== ' ') return;
                event.preventDefault();
                setActiveTab(tab.id);
              }}
              onPointerDown={(event) => handlePointerDown(event, tab.id)}
              onPointerMove={handlePointerMove}
              onPointerUp={handlePointerUp}
              onPointerCancel={handlePointerCancel}
              onClick={(event) => {
                // 辅助技术 / 程序化 `click()`（`detail === 0`）时补一次激活；
                // 鼠标点击的激活已在 `pointerup` 里做过 ⇒ 这里跳过，避免重复 `setActiveTab`。
                if (event.detail === 0) setActiveTab(tab.id);
              }}
            >
              <span className="scratch-tab-name">{tab.title}</span>
              {/* 未保存标记：`dirty === true` 才出现 */}
              {tab.dirty && <span className="scratch-tab-dirty" aria-hidden="true" />}
              {/* 关闭：**不冒泡**（否则会先激活再关闭，激活动作是多余的副作用）。
               * 现在外层是 `div[role="tab"]`，这里是一枚独立按钮 ⇒ 内容模型合法（M-2 已闭环）。 */}
              <button
                type="button"
                className="scratch-tab-close"
                aria-label={`关闭 ${tab.title}`}
                tabIndex={-1}
                onPointerDown={(event) => event.stopPropagation()}
                onClick={(event) => {
                  event.stopPropagation();
                  onRequestClose(tab.id);
                }}
              >
                <Icon name="close" size="sm" />
              </button>
            </div>
          );
        })}
      </div>

      {/* 新建标签页：放在最右端（与"溢出"按钮相邻），永远可见（在滚动容器之外） */}
      <button
        type="button"
        className="scratch-tab-new"
        data-scratch-tab-new="true"
        aria-label="新建标签页"
        title="新建标签页"
        onClick={() => createTab()}
      >
        <Icon name="plus" size="sm" />
      </button>

      {/* 溢出入口：常驻右侧；点开 = 全量标签下拉（放不下时仍能切换） */}
      <div className="scratch-tab-overflow" ref={overflowRef}>
        <button
          type="button"
          className="scratch-tab-overflow-button"
          data-scratch-tab-overflow="true"
          aria-label="全部标签页"
          aria-haspopup="menu"
          aria-expanded={overflowOpen}
          title="全部标签页"
          onClick={() => setOverflowOpen((open) => !open)}
        >
          <Icon name="more" size="sm" />
        </button>
        {overflowOpen && <ScratchTabOverflowMenu onClose={() => setOverflowOpen(false)} />}
      </div>
    </div>
  );
}
