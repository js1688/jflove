/**
 * 文件关联勾选（设计 v1.6.0 §4.3 子块 ② / §6.7.4 / §7.1 #14~#16）
 *
 * ## 语义边界（U-13b 裁决 = C 方案，必须让用户看见，避免误期望）
 *
 * - 勾选 = **只声明 JFLove 自己支持这些扩展名**（对应 Windows 的 `SupportedTypes`、Linux 的 MimeType 行）
 *   + **应用内偏好**（Python 侧写 `USER_DATA_DIR/storage/file_assoc.json`），
 *   **不修改、不抢占系统默认程序**（D18 / AC-43 ②）。
 * - 文件被系统唤起时（双击 / 命令行 / 右键「打开方式」）**无论是否勾选都会用 JFLove 打开**（F7.3）。
 * - 想真正改默认程序：Windows 10/11 的 `UserChoice` 有哈希保护，程序无法静默改默认，
 *   因此提供「设为默认」按钮跳系统设置页（`file_assoc.openSystemDefaults`，§7.1 #16）。
 *
 * ## 其它硬口径
 *
 * - 勾选清单**由 `config/scratch-types.ts` 派生**（`EXTENSIONS` ↔ `ASSOC_EXTENSIONS` 同源，AC-44 / AC-48a），
 *   本组件**不手写**任何扩展名。
 * - 保存含不支持的扩展名时，Python 侧会**抛错**（§7.1 #15，**不静默过滤**）——
 *   这里把后端的错误文案**原样展示**，并保留用户的勾选状态（不回滚、不吞掉）。
 * - 默认全不勾选（D22）：`file_assoc.get` 返回 `selected: []` 时界面全不勾。
 * - 渲染层不碰文件系统、不用 `localStorage`（R-1）、不发任何网络请求（§7.3 零远端）。
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { EXTENSIONS } from '../../config/scratch-types';
import { scratchService } from '../../services/scratch-service';
import type { FileAssocState } from '../../types/scratch';
import { Button, toast } from '../ui';

/** 平台显示名（只读展示用） */
const PLATFORM_LABEL: Record<FileAssocState['platform'], string> = {
  win32: 'Windows',
  linux: 'Linux',
  other: '未知平台',
};

/** 把异常转成可展示文案（后端错误文案优先；不含路径与内容，§7.4） */
function describeError(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return '文件关联操作失败';
}

/** 两个扩展名集合是否等价（顺序无关） */
function sameSelection(a: readonly string[], b: readonly string[]): boolean {
  if (a.length !== b.length) return false;
  const set = new Set(b);
  return a.every((ext) => set.has(ext));
}

export function FileAssocSettings() {
  /** 后端返回的状态（`null` = 尚未读取成功，含桥未就绪） */
  const [state, setState] = useState<FileAssocState | null>(null);
  /** 界面上的勾选（真源在 Python 侧；这里只是待保存的编辑态） */
  const [selected, setSelected] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hint, setHint] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await scratchService.fileAssocGet();
      setState(resp);
      setSelected(resp.selected);
    } catch (e) {
      setState(null);
      setError(describeError(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const selectedSet = useMemo(() => new Set(selected), [selected]);
  const dirty = state !== null && !sameSelection(selected, state.selected);
  const supportedSet = useMemo(() => new Set(state?.supported ?? []), [state]);
  /** 系统清单为空 = manifest 缺失（§7.1 #14），此时保存会被后端拒绝，提前说清楚 */
  const manifestMissing = state !== null && state.supported.length === 0;

  const toggle = useCallback((ext: string) => {
    setError(null);
    setHint(null);
    setSelected((prev) => (prev.includes(ext) ? prev.filter((item) => item !== ext) : [...prev, ext]));
  }, []);

  const handleSave = useCallback(async () => {
    setSaving(true);
    setError(null);
    setHint(null);
    try {
      // 含不支持的扩展名 ⇒ 后端抛错；这里不预过滤、不静默丢弃
      const resp = await scratchService.fileAssocSet(selected);
      setSelected(resp.selected);
      setState((prev) => (prev ? { ...prev, selected: resp.selected } : prev));
      toast.success('文件关联偏好已保存', '仅记录 JFLove 自己支持的扩展名，未修改系统默认程序');
    } catch (e) {
      setError(describeError(e));
    } finally {
      setSaving(false);
    }
  }, [selected]);

  const handleOpenSystemDefaults = useCallback(async () => {
    setError(null);
    setHint(null);
    try {
      const resp = await scratchService.fileAssocOpenSystemDefaults();
      setHint(
        resp.hint ||
          (resp.ok
            ? '已打开系统的「默认应用」设置页，请在那里为扩展名选择 JFLove'
            : '当前平台不支持从应用内打开系统设置页，请手动打开系统「默认应用」设置'),
      );
    } catch (e) {
      setError(describeError(e));
    }
  }, []);

  return (
    <div className="flex flex-col gap-3">
      {/* 语义说明（C-3：UI 文案必须写明不会修改系统默认程序 / 被唤起时一律打开） */}
      <div className="rounded-lg border border-line-subtle bg-sunken px-3 py-2 text-[11.5px] leading-relaxed text-muted">
        <div>
          勾选只表示「JFLove 自己支持这些扩展名」，作为应用内偏好保存，
          <span className="font-semibold">不会修改、也不会抢占系统的默认程序</span>。
        </div>
        <div className="mt-0.5">
          文件被系统唤起时（双击 / 命令行 / 右键「打开方式」），
          <span className="font-semibold">无论是否勾选都会用 JFLove 打开</span>；
          勾选只影响应用内的候选过滤与提示。
        </div>
        <div className="mt-0.5">
          想让某类文件默认由 JFLove 打开，请点「设为默认」，在系统设置里手动指定
          （Windows 10/11 不允许程序静默改默认程序）。
        </div>
      </div>

      {/* 系统层只读状态（不提供任何"写入系统"的开关） */}
      <div className="text-[11.5px] text-subtle">
        {state
          ? `平台：${PLATFORM_LABEL[state.platform]}｜系统注册项：${
              state.osRegistered ? '已登记（「打开方式」里可看到 JFLove）' : '未检测到'
            }｜系统清单：${state.supported.length} 项`
          : loading
            ? '正在读取文件关联状态…'
            : '未读取到文件关联状态（桥不可用）'}
      </div>

      {manifestMissing && (
        <div className="rounded-lg border border-warning-500/25 bg-warning-50 px-3 py-2 text-[11.5px] leading-relaxed text-warning-700">
          {state?.note || '未读取到系统支持的扩展名清单（file-assoc.manifest.json 缺失）。'}
          {' 此时保存勾选会被后端拒绝并给出具体原因，不会被静默忽略。'}
        </div>
      )}

      {error && (
        <div className="rounded-lg border border-danger-500/25 bg-danger-50 px-3 py-2 text-[11.5px] leading-relaxed text-danger-700">
          {error}
        </div>
      )}

      {hint && (
        <div className="rounded-lg border border-line-subtle bg-sunken px-3 py-2 text-[11.5px] leading-relaxed text-fg-brand">
          {hint}
        </div>
      )}

      {/* 勾选清单：逐项来自 config/scratch-types.ts 的 EXTENSIONS（唯一手写真相） */}
      <div className="grid max-h-56 grid-cols-1 gap-x-3 gap-y-0.5 overflow-y-auto rounded-lg border border-line-subtle p-2 sm:grid-cols-2 lg:grid-cols-3">
        {EXTENSIONS.map((info) => {
          const checked = selectedSet.has(info.ext);
          const notInManifest = !manifestMissing && !supportedSet.has(info.ext);
          return (
            <label
              key={info.ext}
              className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-[12.5px] transition-colors hover:bg-hover"
            >
              <input
                type="checkbox"
                className="accent-brand-500"
                checked={checked}
                disabled={saving}
                onChange={() => toggle(info.ext)}
              />
              <span className="tabular shrink-0 text-muted">.{info.ext}</span>
              <span className="truncate text-fg" title={`${info.label}（${info.description}）`}>
                {info.label}
              </span>
              {notInManifest && <span className="shrink-0 text-[10.5px] text-warning-700">清单未含</span>}
            </label>
          );
        })}
      </div>

      {/* 操作区 */}
      <div className="flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="primary"
          icon="save"
          loading={saving}
          disabled={!dirty}
          onClick={() => void handleSave()}
        >
          保存勾选
        </Button>
        <Button
          size="sm"
          variant="secondary"
          icon="link"
          disabled={saving}
          onClick={() => void handleOpenSystemDefaults()}
        >
          设为默认
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={saving}
          onClick={() => {
            setError(null);
            setHint(null);
            setSelected(EXTENSIONS.map((info) => info.ext));
          }}
        >
          全选
        </Button>
        <Button
          size="sm"
          variant="ghost"
          disabled={saving}
          onClick={() => {
            setError(null);
            setHint(null);
            setSelected([]);
          }}
        >
          全不选
        </Button>
        <Button
          size="sm"
          variant="ghost"
          icon="refresh"
          loading={loading}
          onClick={() => void load()}
        >
          刷新
        </Button>
        <span className="text-[11.5px] text-subtle">
          {selected.length > 0 ? `已勾选 ${selected.length} 项` : '默认全部不勾选'}
          {dirty && '（有未保存的修改）'}
        </span>
      </div>
    </div>
  );
}
