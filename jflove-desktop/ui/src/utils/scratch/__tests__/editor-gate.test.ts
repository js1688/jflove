/**
 * 准入单一真相 `utils/scratch/editor-gate.ts` 单测 —— Phase 6（v1.6.1 · T1a）
 *
 * ## 守的是哪几条 AC
 *
 * - **AC-47（反转改写）**：「编辑器**依赖登录**；**登录后**不依赖服务端可达性」——
 *   ①② 未登录不可进入由 `canUseEditor` 的布尔判定承接；②「不依赖服务端」要求
 *   **判定过程零网络**（本文件用 `authService.logout` 的调用计数证明"纯快照、无副作用"，
 *   另见 `tests/test_v160_phase6_offline.py` 的链路零网络静态守门）；
 * - **AC-63**（未登录零存在）：①导航项 / ②快捷键 / ③路由守卫 / ④草稿还原**四处消费共用**
 *   本模块的判据 —— 「一处写错，四处全错」，所以判据本身必须有独立单测（本条即该载体）；
 * - **AC-71**（登录后离线可用）：「已登录 + 服务端不可达 ⇒ 新增 / 打开 / 保存 / 切换 / 另存为
 *   全部成功」的前提是**准入判定不查服务端**；若这里退化成"逐次校验会话"，AC-71 必然失败；
 * - **AC-72**（登出 / 会话失效不丢内容）：「登出后入口消失」= `canUseEditor` 随
 *   `isLoggedIn` 翻假；本文件同时锁住"过期判定**不**顺带清理草稿 / 不触发登出清理"这条边界。
 *
 * ## 判据出处
 *
 * - 设计 v1.6.1 `文档记录/设计文档/v1.6.1.md:405-425`（§4.1 准入单一真相 + 四处消费 +
 *   「不新增定时器 / 不新增服务端校验」）、`:899-920`（§13.3 发布前判据）、
 *   `:971-983`（§14.3 负向对照：**AC-47 反向注入「未登录也能开编辑器」⇒ 本文件必须失败**）；
 * - 需求 v1.6.1 `文档记录/需求文档/v1.6.1.md:375`（AC-47 反转）、`:488`（AC-47 全文，
 *   含 `[负向对照]` 标注）、`:476`（AC-71）、`:469-470`（AC-64 / AC-65）；
 * - 实现：`utils/scratch/editor-gate.ts:47-49`（纯函数）、`:59-62`（无副作用过期判据）、
 *   `:70-73`（非 React 上下文版）、`:81-84`（React 订阅版）。
 *
 * ## 与 `auth-store.isTokenExpired()` 的**刻意差异**（本文件的判别力核心）
 *
 * | 函数 | 过期时是否顺带登出清理 | 为什么 |
 * |---|---|---|
 * | `auth-store.isTokenExpired()` | **会**（`set(LOGGED_OUT)` + `authService.logout()`） | 非渲染期调用，允许顺带完成既有清理 |
 * | `editor-gate.isSessionExpired()` | **不会**（纯快照） | 它是 `useSyncExternalStore` 的快照函数，**渲染期改状态会告警** |
 *
 * 因此本文件对"无副作用"的断言必须**成对出现**：同一份过期快照下
 * `isSessionExpired()` 不得调 `authService.logout`，而 `isTokenExpired()` **必须**调
 * —— 后者是前者的**阳性对照**，否则"未被调用"可能只是因为替身没生效（假绿）。
 *
 * ## 分层声明与安全约束
 *
 * - 只断言**渲染层判定**：不发真实网络请求（`authService.logout` 全替身化），
 *   不碰文件系统、不加载离线编辑器包；
 * - **fixture 与断言消息里不出现 token / session_key**（AGENTS.md §9.6 / §9.14）：
 *   替身返回值只用 `{}` 占位；安全边界用例断言的是"这些字段**不存在**"。
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { authService, type SessionView } from '../../../services/auth-service';
import { useAuthStore } from '../../../stores/auth-store';
import { getAuthSnapshot, getToken, setAuthSnapshot } from '../../session';
import { canUseEditor, currentEditorGate, isSessionExpired } from '../editor-gate';

/** 会话已过期的固定时间戳（Unix 秒）；`now` 一律显式传入，避免依赖真实时钟 */
const EXPIRED_AT = 1_000;
/** 与 `EXPIRED_AT` 对应的毫秒时刻（恰好到期） */
const AT_EXPIRY_MS = EXPIRED_AT * 1_000;

/**
 * 摆一份会话快照（渲染态缓存 + store 双写，与 `applySessionView` 的落点一致）。
 *
 * 不写任何凭据字段 —— 桌面端渲染层的快照里**本来就没有**令牌。
 */
function resetAuth(loggedIn: boolean, expiresAt: number | null): void {
  setAuthSnapshot({ loggedIn, expiresAt });
  useAuthStore.setState({
    isLoggedIn: loggedIn,
    userId: null,
    username: null,
    role: null,
    isAdmin: false,
    isEncrypted: false,
  });
}

beforeEach(() => {
  // `authService.logout` 是本文件的核心观测点：全替身化（**不会**真的走桥 / 网络）。
  // 替身返回值只用空对象占位：既不写 token，也不需要真实会话形状。
  vi.spyOn(authService, 'logout').mockResolvedValue({} as SessionView);
  resetAuth(false, null);
});

afterEach(() => {
  resetAuth(false, null);
});

describe('AC-47（反转）/ AC-63：canUseEditor 四种会话组合逐条断言', () => {
  it('AC-47 ① 已登录 + 未过期 ⇒ 可用（唯一的"真"组合）', () => {
    expect(canUseEditor({ isLoggedIn: true, tokenExpired: false })).toBe(true);
  });

  it('AC-47 ① 已登录 + 已过期 ⇒ 不可用（会话中途失效必须立即退回登录态）', () => {
    expect(canUseEditor({ isLoggedIn: true, tokenExpired: true })).toBe(false);
  });

  it('AC-47 ① 未登录 + 未过期 ⇒ 不可用（反转点：v1.6.0 的"未登录也能开编辑器"必须失败）', () => {
    expect(canUseEditor({ isLoggedIn: false, tokenExpired: false })).toBe(false);
  });

  it('AC-47 ① 未登录 + 已过期 ⇒ 不可用（四种组合里唯一的"双否"）', () => {
    expect(canUseEditor({ isLoggedIn: false, tokenExpired: true })).toBe(false);
  });

  it('AC-71 判定是纯函数：四种组合各调两次结果稳定，且全程零桥调用（登录后不依赖服务端）', () => {
    const combos = [
      { isLoggedIn: true, tokenExpired: false },
      { isLoggedIn: true, tokenExpired: true },
      { isLoggedIn: false, tokenExpired: false },
      { isLoggedIn: false, tokenExpired: true },
    ] as const;

    for (const snapshot of combos) {
      const first = canUseEditor(snapshot);
      const second = canUseEditor(snapshot);
      expect(second, `同一快照两次判定必须一致：${JSON.stringify(snapshot)}`).toBe(first);
    }
    // 纯函数不读 store、不改 store ⇒ store 仍是 beforeEach 摆的未登录态
    expect(useAuthStore.getState().isLoggedIn).toBe(false);
    expect(authService.logout, '准入判定不得触碰登出通路（否则 AC-71 的离线可用不成立）')
      .not.toHaveBeenCalled();
  });
});

describe('AC-47（反转）/ AC-71 / AC-72：isSessionExpired 无副作用（不触发登出清理）', () => {
  it('AC-47 已登录 + 已过期：isSessionExpired() 为真，但 authService.logout 零调用、渲染态与 store 都不动', () => {
    resetAuth(true, EXPIRED_AT);

    expect(isSessionExpired(AT_EXPIRY_MS)).toBe(true);

    // 核心断言：**渲染期快照函数不得触发登出清理**（auth-store.ts:143-148 那份副作用只能属于 isTokenExpired）
    expect(authService.logout, 'isSessionExpired 必须无副作用：不得调 authService.logout').not
      .toHaveBeenCalled();
    expect(useAuthStore.getState().isLoggedIn, 'store 不得被这次判定改写').toBe(true);
    expect(getAuthSnapshot().loggedIn, '渲染态快照不得被这次判定清空').toBe(true);
    expect(getAuthSnapshot().expiresAt, '失效时间戳不得被这次判定改写').toBe(EXPIRED_AT);
  });

  it('[阳性对照 / 判别力] 同一过期快照下 isTokenExpired() 会清登出并调 authService.logout —— 证明上面的"零调用"是真判别力而非替身失效', () => {
    resetAuth(true, EXPIRED_AT);

    // 与 isSessionExpired 同口径的过期判定，但**允许**顺带完成既有清理（非渲染期）
    expect(useAuthStore.getState().isTokenExpired()).toBe(true);

    expect(authService.logout, 'isTokenExpired 必须触发登出清理（对照项）').toHaveBeenCalledTimes(1);
    expect(useAuthStore.getState().isLoggedIn, 'isTokenExpired 会把渲染态退回未登录').toBe(false);
  });

  it('AC-47 无失效时间（expiresAt = null）⇒ 永不判过期，且零登出调用（桌面端 null = 无本地上限）', () => {
    resetAuth(true, null);

    expect(isSessionExpired(0)).toBe(false);
    expect(isSessionExpired(Number.MAX_SAFE_INTEGER)).toBe(false);
    expect(authService.logout).not.toHaveBeenCalled();
  });

  it('AC-47 边界：到期时刻**本身**即算过期（`>=` 口径与 auth-store.ts:141 一致，避免"差一毫秒仍可用"）', () => {
    resetAuth(true, EXPIRED_AT);

    expect(isSessionExpired(AT_EXPIRY_MS - 1), '到期前一毫秒仍可用').toBe(false);
    expect(isSessionExpired(AT_EXPIRY_MS), '到期时刻本身即过期').toBe(true);
    expect(authService.logout).not.toHaveBeenCalled();
  });
});

describe('AC-63 ①③：currentEditorGate() 与 store 状态一致（含过期态）', () => {
  it('AC-63 已登录 + 未过期 ⇒ true，且不改动 store、零登出调用', () => {
    resetAuth(true, null);

    expect(currentEditorGate()).toBe(true);
    expect(useAuthStore.getState().isLoggedIn).toBe(true);
    expect(authService.logout).not.toHaveBeenCalled();
  });

  it('AC-63 ①③ 未登录 ⇒ false（快捷键运行时与唤起队列门的公共入口）', () => {
    resetAuth(false, null);

    expect(currentEditorGate()).toBe(false);
    expect(authService.logout).not.toHaveBeenCalled();
  });

  it('AC-63 已登录但会话过期 ⇒ false，并顺带完成既有"过期即退回登录态"清理（与 RequireAuth 同口径）', () => {
    resetAuth(true, EXPIRED_AT);

    // 非渲染上下文允许顺带清理（editor-gate.ts:66-69 的设计选择）：判定必须与"退登"同向
    expect(currentEditorGate()).toBe(false);
    expect(useAuthStore.getState().isLoggedIn, '过期后渲染态必须已是未登录').toBe(false);
    expect(authService.logout, '该路径上的登出清理是既有行为，此处显式登记而非静默').toHaveBeenCalledTimes(1);
  });

  it('AC-63 三种会话组合下 currentEditorGate() 与 canUseEditor(store 快照) 逐一等价（防两处口径漂移）', () => {
    const cases: ReadonlyArray<{ loggedIn: boolean; expiresAt: number | null }> = [
      { loggedIn: true, expiresAt: null },
      { loggedIn: false, expiresAt: null },
      { loggedIn: false, expiresAt: EXPIRED_AT },
    ];

    for (const item of cases) {
      resetAuth(item.loggedIn, item.expiresAt);
      const expected = canUseEditor({
        isLoggedIn: useAuthStore.getState().isLoggedIn,
        tokenExpired: isSessionExpired(),
      });
      expect(
        currentEditorGate(),
        `gate 与快照判定不一致：${JSON.stringify(item)}`,
      ).toBe(expected);
    }
  });
});

describe('安全边界（AGENTS.md §9.6）：渲染层的会话快照不持有任何令牌字段', () => {
  it('auth store 快照里不存在 token / jwt / session_key 类字段（它们只活在 Python 侧）', () => {
    resetAuth(true, null);
    const snapshot = useAuthStore.getState() as unknown as Record<string, unknown>;

    // 注意用**精确键名**：`isTokenExpired` 是函数名，不是令牌字段，故不在本清单内
    for (const key of ['token', 'accessToken', 'refreshToken', 'jwt', 'sessionKey', 'session_key']) {
      expect(key in snapshot, `渲染层会话快照不得出现字段：${key}`).toBe(false);
    }
  });

  it('session 模块的 getToken() 恒为 null（桌面端 JWT 只在 Python，渲染层拿不到也不需要）', () => {
    resetAuth(true, null);

    expect(getToken()).toBeNull();
  });
});
