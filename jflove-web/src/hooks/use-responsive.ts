/**
 * 响应式断点检测 Hook
 */

import { useState, useEffect } from 'react';

/** 是否为 PC 端（≥1024px） */
export function useIsPC(): boolean {
  const [isPC, setIsPC] = useState(() =>
    typeof window !== 'undefined' ? window.innerWidth >= 1024 : true,
  );

  useEffect(() => {
    const mql = window.matchMedia('(min-width: 1024px)');
    const handler = (e: MediaQueryListEvent) => setIsPC(e.matches);
    mql.addEventListener('change', handler);
    return () => mql.removeEventListener('change', handler);
  }, []);

  return isPC;
}

/** 当前断点 */
export type Breakpoint = 'mobile' | 'pc';

export function useBreakpoint(): Breakpoint {
  const isPC = useIsPC();
  return isPC ? 'pc' : 'mobile';
}
