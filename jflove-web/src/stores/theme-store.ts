/**
 * 主题状态管理（亮 / 暗 / 跟随系统）
 *
 * 设计文档 §2.3：三端统一支持「跟随系统 / 亮 / 暗」三态，切换即时生效、无闪烁。
 *
 * 关键实现点：
 *   - 主题类名挂在 `<html>`（`documentElement`）上，而不是 `<body>`；
 *   - 首屏防闪烁由 `index.html` 的内联同步脚本负责（在 React 挂载前就设好类名），
 *     本模块只负责后续切换与状态同步；
 *   - 切换只改类名 + 一个 CSS 变量都没动 —— 所有颜色由 tokens.css 的语义变量驱动。
 */
import { create } from 'zustand';

export type ThemeMode = 'system' | 'light' | 'dark';

/** localStorage 键名（与 index.html 内联脚本必须保持一致） */
export const THEME_STORAGE_KEY = 'jflove.theme';

const MEDIA_QUERY = '(prefers-color-scheme: dark)';

/** 从 localStorage 读取已保存的主题模式（非法值回落 system） */
export function readStoredThemeMode(): ThemeMode {
  try {
    const raw = localStorage.getItem(THEME_STORAGE_KEY);
    if (raw === 'light' || raw === 'dark' || raw === 'system') return raw;
  } catch {
    // 隐私模式等场景下 localStorage 可能不可用
  }
  return 'system';
}

/** 系统当前是否为暗色（jsdom 等环境没有 matchMedia，做能力守卫） */
function systemPrefersDark(): boolean {
  if (typeof window === 'undefined') return false;
  if (typeof window.matchMedia !== 'function') return false;
  return window.matchMedia(MEDIA_QUERY).matches;
}

/** 把模式解析成最终生效的主题 */
export function resolveTheme(mode: ThemeMode): 'light' | 'dark' {
  if (mode === 'system') return systemPrefersDark() ? 'dark' : 'light';
  return mode;
}

/** 把主题写到 <html> 上（组件的 `dark:` 变体与 tokens.css 的 `.dark` 都依赖它） */
function applyThemeToDom(resolved: 'light' | 'dark'): void {
  const root = document.documentElement;
  root.classList.toggle('dark', resolved === 'dark');
  // 让浏览器原生控件（滚动条、表单）也跟随
  root.style.colorScheme = resolved;
  // 广播给图表等「需要重新渲染而非仅换色」的订阅者（设计文档 §4.1）
  window.dispatchEvent(new Event('jflove:theme-changed'));
}

interface ThemeState {
  /** 用户选择的模式 */
  mode: ThemeMode;
  /** 当前实际生效的主题（system 模式下由系统决定） */
  resolved: 'light' | 'dark';
  /** 用户切换模式 */
  setMode: (mode: ThemeMode) => void;
  /** 亮暗互切（顶栏按钮用：在 system 下切到与当前相反的那个） */
  toggle: () => void;
  /** 订阅系统主题变化；返回取消订阅函数。由 App 挂载时调用一次。 */
  watchSystem: () => () => void;
}

export const useThemeStore = create<ThemeState>((set, get) => ({
  mode: readStoredThemeMode(),
  resolved: resolveTheme(readStoredThemeMode()),

  setMode: (mode) => {
    const resolved = resolveTheme(mode);
    applyThemeToDom(resolved);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, mode);
    } catch {
      // 存不进也不影响本次会话生效
    }
    set({ mode, resolved });
  },

  toggle: () => {
    const next = get().resolved === 'dark' ? 'light' : 'dark';
    get().setMode(next);
  },

  watchSystem: () => {
    // jsdom 等环境没有 matchMedia：只对齐一次状态，不做订阅
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return () => {};
    }
    const mq = window.matchMedia(MEDIA_QUERY);
    const onChange = () => {
      // 只有 system 模式才跟随系统
      if (get().mode !== 'system') return;
      const resolved = resolveTheme('system');
      applyThemeToDom(resolved);
      set({ resolved });
    };
    mq.addEventListener('change', onChange);
    // 挂载时对齐一次（React 挂载前可能已由内联脚本设过，这里只保证状态一致）
    const resolved = resolveTheme(get().mode);
    applyThemeToDom(resolved);
    set({ resolved });
    return () => mq.removeEventListener('change', onChange);
  },
}));
