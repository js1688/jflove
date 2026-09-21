/**
 * 服务端地址输入框（桌面端自绘下拉）
 *
 * ## 为什么不用原生 `<datalist>`
 *
 * 原生 datalist 的候选项弹层由浏览器绘制在 **shadow DOM** 里，CSS 无法触达 ——
 * 实测观感与设计系统严重不符（用户反馈"下拉框不好看有点原始"）。
 * 因此这里自绘下拉面板：令牌配色 + 圆角 + `--e3` 阴影，并顺带补上键盘操作
 * 与"删除某条历史"（原生 datalist 都做不到）。
 *
 * ## 行为
 *
 * - 聚焦/输入 → 展开面板；输入内容按子串过滤（内容为空时显示全部）
 * - ↑/↓ 移动高亮，Enter 选中高亮项（无高亮时把 Enter 交给 `onEnter`），Esc 关闭
 * - 点击面板外、失焦 → 关闭
 * - 每行右侧的 × 可删除该条历史（仅当传入 `onDeleteSuggestion`）
 */

import { useEffect, useMemo, useRef, useState } from 'react';
import { Icon } from '../ui';

interface ServerUrlInputProps {
  value: string;
  onChange: (value: string) => void;
  /** 历史地址候选（最近使用的在前） */
  suggestions: string[];
  /** 传入后每行显示删除按钮 */
  onDeleteSuggestion?: (url: string) => void;
  /** 无高亮项时按 Enter 触发（例如直接提交连接） */
  onEnter?: () => void;
  placeholder?: string;
  /** 作用于外层容器（如 `flex-1`） */
  className?: string;
}

export function ServerUrlInput({
  value,
  onChange,
  suggestions,
  onDeleteSuggestion,
  onEnter,
  placeholder,
  className,
}: ServerUrlInputProps) {
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const wrapRef = useRef<HTMLDivElement | null>(null);

  // 输入内容按子串过滤；内容为空时展示全部历史
  const items = useMemo(() => {
    const keyword = value.trim().toLowerCase();
    if (!keyword) return suggestions;
    return suggestions.filter((url) => url.toLowerCase().includes(keyword));
  }, [suggestions, value]);

  // 点击面板外关闭
  useEffect(() => {
    if (!open) return;
    const onDocMouseDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener('mousedown', onDocMouseDown);
    return () => document.removeEventListener('mousedown', onDocMouseDown);
  }, [open]);

  // 候选变化时重置高亮
  useEffect(() => {
    setActiveIndex(-1);
  }, [value, open]);

  const select = (url: string) => {
    onChange(url);
    setOpen(false);
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      if (!open) {
        setOpen(true);
        return;
      }
      if (items.length === 0) return;
      e.preventDefault();
      const next =
        e.key === 'ArrowDown'
          ? (activeIndex + 1) % items.length
          : (activeIndex - 1 + items.length) % items.length;
      setActiveIndex(next);
      return;
    }
    if (e.key === 'Escape') {
      setOpen(false);
      setActiveIndex(-1);
      return;
    }
    if (e.key === 'Enter') {
      if (open && activeIndex >= 0 && items[activeIndex]) {
        e.preventDefault();
        select(items[activeIndex]);
        return;
      }
      setOpen(false);
      onEnter?.();
    }
  };

  return (
    <div ref={wrapRef} className={`relative ${className ?? ''}`}>
      <input
        type="text"
        value={value}
        placeholder={placeholder}
        spellCheck={false}
        autoComplete="off"
        onChange={(e) => {
          onChange(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={handleKeyDown}
        className="input w-full"
      />

      {open && (
        <div
          className="absolute inset-x-0 top-[calc(100%+5px)] z-50 overflow-hidden rounded-lg border border-line-subtle bg-surface"
          style={{ boxShadow: 'var(--e3)' }}
          role="listbox"
        >
          <div className="px-2.5 pt-2 pb-1 text-[10.5px] tracking-[0.02em] text-subtle">
            历史地址
          </div>

          <div className="max-h-56 overflow-y-auto p-1 pt-0">
            {items.length === 0 && (
              <div className="px-2.5 py-2 text-[12px] text-subtle">暂无匹配的历史地址</div>
            )}

            {items.map((url, index) => (
              <div
                key={url}
                role="option"
                aria-selected={index === activeIndex}
                onMouseEnter={() => setActiveIndex(index)}
                // 防止输入框先 blur 导致面板关闭、click 落空
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => select(url)}
                className={`group flex cursor-pointer items-center gap-2 rounded-md px-2.5 py-1.5 text-[12.5px] transition-colors ${
                  index === activeIndex ? 'bg-hover' : ''
                }`}
              >
                <Icon name="server" size="sm" className="shrink-0 text-subtle" />
                <span className="min-w-0 flex-1 truncate">{url}</span>
                {onDeleteSuggestion && (
                  <button
                    type="button"
                    aria-label={`删除历史地址 ${url}`}
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={(e) => {
                      e.stopPropagation();
                      onDeleteSuggestion(url);
                    }}
                    className="shrink-0 rounded-sm p-0.5 text-subtle opacity-0 transition-opacity group-hover:opacity-100 hover:bg-active hover:text-danger-500"
                  >
                    <Icon name="close" size="sm" />
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
