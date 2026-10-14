import { afterEach } from 'vitest';
import { cleanup } from '@testing-library/react';

/**
 * vitest 全局前置 —— Phase 6（v1.6.0）
 *
 * 只做一件事：每个用例结束后卸载 Testing Library 渲染出来的 DOM，
 * 防止用例之间互相污染（`globals: false` 下 Testing Library 不会自动清理）。
 *
 * **刻意不在这里替身化任何宿主能力**（`window.__JFLOVE_BRIDGE__` / `window.__JFLOVE_CM__`）：
 * 需要离线包或桥的用例各自显式准备替身，避免"测试通过但依赖了错误前提"。
 */
afterEach(() => {
  cleanup();
});
