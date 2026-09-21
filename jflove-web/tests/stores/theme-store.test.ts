/**
 * 主题三态（跟随系统 / 亮 / 暗）与持久化用例（v1.5.0，Web 端）
 *
 * 覆盖 AC-2 与 AC-11 的 Web 侧前提：
 *   - 三态切换即时生效（`<html>` 上的 `.dark` 类与 `color-scheme` 同步变化，
 *     不触发页面重载、没有中间态）；
 *   - 选择持久化到 localStorage，刷新后由 `index.html` 内联脚本复用（键名必须一致）；
 *   - system 模式下跟随系统变化；一旦用户显式选了亮/暗，系统变化**不得**覆盖用户选择；
 *   - 切换时广播 `jflove:theme-changed`（图表订阅它重新渲染，而不是只换 CSS）。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  readStoredThemeMode,
  resolveTheme,
  THEME_STORAGE_KEY,
  useThemeStore,
} from '../../src/stores/theme-store';

const MEDIA_QUERY = '(prefers-color-scheme: dark)';

let systemDark = false;
let changeListeners: Array<() => void> = [];
let originalMatchMedia: typeof window.matchMedia;

function installMatchMedia(): void {
  originalMatchMedia = window.matchMedia;
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    configurable: true,
    value: (query: string) => ({
      get matches() {
        return query === MEDIA_QUERY ? systemDark : false;
      },
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: (_type: string, cb: () => void) => {
        changeListeners.push(cb);
      },
      removeEventListener: (_type: string, cb: () => void) => {
        changeListeners = changeListeners.filter((l) => l !== cb);
      },
      dispatchEvent: () => false,
    }),
  });
}

describe('主题三态与持久化', () => {
  beforeEach(() => {
    systemDark = false;
    changeListeners = [];
    installMatchMedia();
    localStorage.clear();
    document.documentElement.classList.remove('dark');
    document.documentElement.style.colorScheme = '';
    useThemeStore.setState({ mode: 'system', resolved: 'light' });
  });

  afterEach(() => {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      configurable: true,
      value: originalMatchMedia,
    });
    useThemeStore.setState({ mode: 'system', resolved: 'light' });
    document.documentElement.classList.remove('dark');
  });

  it('持久化键名与 index.html 内联脚本一致（否则刷新后会闪一下）', () => {
    expect(THEME_STORAGE_KEY).toBe('jflove.theme');
  });

  it('未保存过时默认跟随系统；非法值一律回退 system', () => {
    expect(readStoredThemeMode()).toBe('system');
    for (const bad of ['Dark', 'blue', '', '{}']) {
      localStorage.setItem(THEME_STORAGE_KEY, bad);
      expect(readStoredThemeMode()).toBe('system');
    }
    for (const good of ['light', 'dark', 'system'] as const) {
      localStorage.setItem(THEME_STORAGE_KEY, good);
      expect(readStoredThemeMode()).toBe(good);
    }
  });

  it('resolveTheme：system 由系统决定，显式选择优先', () => {
    expect(resolveTheme('light')).toBe('light');
    expect(resolveTheme('dark')).toBe('dark');
    systemDark = false;
    expect(resolveTheme('system')).toBe('light');
    systemDark = true;
    expect(resolveTheme('system')).toBe('dark');
  });

  it('setMode 即时生效：类名 + colorScheme + localStorage 三者同步', () => {
    const { setMode } = useThemeStore.getState();

    setMode('dark');
    expect(document.documentElement.classList.contains('dark')).toBe(true);
    expect(document.documentElement.style.colorScheme).toBe('dark');
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark');
    expect(useThemeStore.getState().resolved).toBe('dark');

    setMode('light');
    expect(document.documentElement.classList.contains('dark')).toBe(false);
    expect(document.documentElement.style.colorScheme).toBe('light');
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('light');
  });

  it('setMode 广播 jflove:theme-changed（图表据此重新渲染）', () => {
    const seen: string[] = [];
    const listener = () => seen.push(document.documentElement.className);
    window.addEventListener('jflove:theme-changed', listener);
    try {
      useThemeStore.getState().setMode('dark');
      useThemeStore.getState().setMode('light');
    } finally {
      window.removeEventListener('jflove:theme-changed', listener);
    }
    expect(seen).toHaveLength(2);
    expect(seen[0]).toContain('dark');
    expect(seen[1]).not.toContain('dark');
  });

  it('toggle 在亮暗之间互切（system 下切到与当前相反的那个）', () => {
    const { toggle } = useThemeStore.getState();
    systemDark = false;
    useThemeStore.getState().setMode('system');
    toggle();
    expect(useThemeStore.getState().mode).toBe('dark');

    toggle();
    expect(useThemeStore.getState().mode).toBe('light');
  });

  it('system 模式下跟随系统变化实时更新', () => {
    const unsubscribe = useThemeStore.getState().watchSystem();
    try {
      expect(changeListeners.length).toBeGreaterThan(0);

      useThemeStore.getState().setMode('system');
      systemDark = true;
      changeListeners.forEach((cb) => cb());

      expect(useThemeStore.getState().resolved).toBe('dark');
      expect(document.documentElement.classList.contains('dark')).toBe(true);
    } finally {
      unsubscribe();
    }
  });

  it('用户显式选择后，系统变化不得覆盖用户选择', () => {
    const unsubscribe = useThemeStore.getState().watchSystem();
    try {
      useThemeStore.getState().setMode('light');
      systemDark = true; // 系统切到暗色
      changeListeners.forEach((cb) => cb());

      expect(useThemeStore.getState().mode).toBe('light');
      expect(useThemeStore.getState().resolved).toBe('light');
      expect(document.documentElement.classList.contains('dark')).toBe(false);
    } finally {
      unsubscribe();
    }
  });

  it('watchSystem 返回的取消订阅函数能摘掉监听（StrictMode 双跑不泄漏）', () => {
    const unsubscribe = useThemeStore.getState().watchSystem();
    expect(changeListeners).toHaveLength(1);
    unsubscribe();
    expect(changeListeners).toHaveLength(0);
  });

  it('缺少 matchMedia 的环境（jsdom / 隐私模式）不抛异常', () => {
    Object.defineProperty(window, 'matchMedia', {
      writable: true,
      configurable: true,
      value: undefined,
    });
    expect(resolveTheme('system')).toBe('light');
    const unsubscribe = useThemeStore.getState().watchSystem();
    expect(typeof unsubscribe).toBe('function');
    unsubscribe();
  });

  it('localStorage 不可用时切换仍然生效（只是不持久化）', () => {
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError');
    });
    try {
      useThemeStore.getState().setMode('dark');
      expect(useThemeStore.getState().resolved).toBe('dark');
      expect(document.documentElement.classList.contains('dark')).toBe(true);
    } finally {
      setItem.mockRestore();
    }
  });
});
