import '@testing-library/jest-dom/vitest';

/**
 * jsdom 未实现 `window.matchMedia`，而主题层（`stores/theme-store.ts`）与
 * `components/ui/Overlay.tsx` 的 ThemeToggle 都依赖它。
 * 这里补一个最小可用的 polyfill，避免组件测试因环境缺失而失败。
 *
 * 说明：theme-store 自身也有能力守卫（无 matchMedia 时退化为「不跟随系统」），
 * 这里补 polyfill 是为了让「跟随系统」这条路径在测试中也能被覆盖。
 */
if (typeof window !== 'undefined' && typeof window.matchMedia !== 'function') {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: (query: string) => ({
      matches: false, // 测试环境默认按亮色
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}
