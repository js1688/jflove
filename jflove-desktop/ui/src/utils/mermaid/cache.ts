/**
 * Mermaid SVG 缓存
 *
 * 两级缓存（设计文档 §4.7）：
 *   1. 内存 Map（带 LRU 上限）：同一会话内二次显示零延迟；
 *   2. sessionStorage：刷新页面后仍可命中，避免重复渲染。
 *
 * 缓存键 = `sha256(code + theme + MERMAID_VERSION)[:16]`（设计文档 §4.1）：
 * 源码、主题、引擎版本任一变化即自动失效。
 *
 * 注意：sessionStorage 有配额（一般 5MB）。SVG 单图约 15–25KB，上限 64 条时约 1.6MB，
 * 命中 `QuotaExceededError` 时静默放弃持久化（内存缓存仍生效），不影响渲染。
 */
import type { MermaidResult } from './types';

const MEM_LIMIT = 128;
const STORAGE_PREFIX = 'jflove.mmd.';
const STORAGE_LIMIT = 64;

/** 内存 LRU：Map 的插入顺序即访问顺序，命中时重新插入以挪到队尾 */
const mem = new Map<string, MermaidResult>();

/** sessionStorage 中已写入的键顺序（用于淘汰） */
let storageKeys: string[] = [];

function readStorageIndex(): string[] {
  if (storageKeys.length > 0) return storageKeys;
  try {
    const keys: string[] = [];
    for (let i = 0; i < sessionStorage.length; i++) {
      const k = sessionStorage.key(i);
      if (k?.startsWith(STORAGE_PREFIX)) keys.push(k.slice(STORAGE_PREFIX.length));
    }
    storageKeys = keys;
  } catch {
    storageKeys = [];
  }
  return storageKeys;
}

/** 读取缓存（内存优先） */
export function getCached(key: string): MermaidResult | null {
  const hit = mem.get(key);
  if (hit) {
    // 触达 LRU
    mem.delete(key);
    mem.set(key, hit);
    return hit;
  }

  try {
    const raw = sessionStorage.getItem(STORAGE_PREFIX + key);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as MermaidResult;
    if (parsed?.ok && typeof parsed.svg === 'string') {
      mem.set(key, parsed); // 回填内存
      return parsed;
    }
  } catch {
    // 解析失败视为未命中
  }
  return null;
}

/** 写入缓存（内存 + 尽力持久化） */
export function setCached(key: string, result: MermaidResult): void {
  if (!result.ok) return; // 失败结果不缓存，允许下次重试

  mem.set(key, result);
  // LRU 淘汰
  while (mem.size > MEM_LIMIT) {
    const oldest = mem.keys().next().value;
    if (oldest === undefined) break;
    mem.delete(oldest);
  }

  try {
    sessionStorage.setItem(STORAGE_PREFIX + key, JSON.stringify(result));
    const keys = readStorageIndex();
    if (!keys.includes(key)) keys.push(key);
    // 超出上限则淘汰最早的
    while (keys.length > STORAGE_LIMIT) {
      const victim = keys.shift();
      if (victim) sessionStorage.removeItem(STORAGE_PREFIX + victim);
    }
  } catch {
    // 配额不足等情况：放弃持久化，内存缓存仍有效
  }
}

/** 清空缓存（引擎升级或用户主动刷新时用） */
export function clearMermaidCache(): void {
  mem.clear();
  try {
    const keys = readStorageIndex();
    for (const k of keys) sessionStorage.removeItem(STORAGE_PREFIX + k);
  } catch {
    // 忽略
  }
  storageKeys = [];
}

/** 供测试观察缓存规模 */
export const __testing = { memSize: () => mem.size };
