/**
 * 磁盘容量信息展示（v1.10.26，R1）
 *
 * 需求 R1：文件管理（磁盘列表）与磁盘设置（管理 → 磁盘管理）都要展示
 * **最大空间 / 已用空间 / 可用空间 / 磁盘类型** 四项。
 *
 * 数据来源：服务端 `files/disks` 与 `virtual-disks` 两个接口下发
 * `total_size` / `used_size` / `free_size` / `disk_type`（实时探测，不落库）。
 * 路径不可读时字段为 `null` → 一律渲染 `—`，**不报错、不崩**。
 *
 * ⚠ 本文件同时存在于 `jflove-web/src/components/` 与 `jflove-desktop/ui/src/components/`，
 * 两端必须**逐字节一致**（`scripts/check_web_desktop_parity.py` 的门禁清单）。
 * 改动时两处同步改，不要只改一边。
 */

import { formatSize } from '../utils/format';

/** 服务端下发的磁盘容量四项（全部可为 null：路径不可读） */
export interface DiskCapacityFields {
  total_size?: number | null;
  used_size?: number | null;
  free_size?: number | null;
  disk_type?: string | null;
}

/** 空值统一渲染成 `—`（R1 明确要求：不可读兜底 `—`，不崩） */
function orDash(value: string | null | undefined): string {
  const s = (value ?? '').trim();
  return s ? s : '—';
}

function formatCapacity(bytes: number | null | undefined): string {
  // null / undefined / 负数 / NaN 都渲染 `—`，避免出现 "0 B" 这种误导值
  if (bytes === null || bytes === undefined) return '—';
  if (!Number.isFinite(bytes) || bytes < 0) return '—';
  return formatSize(bytes);
}

/** 已用百分比（用于进度条）；总容量缺失或为 0 时返回 null（不画进度条） */
function usedPercent(total?: number | null, used?: number | null): number | null {
  if (!total || total <= 0 || used === null || used === undefined) return null;
  if (!Number.isFinite(used) || used < 0) return null;
  return Math.min(100, Math.max(0, (used / total) * 100));
}

/**
 * 单行容量摘要：`最大 952.0 GB · 已用 243.0 GB · 可用 709.0 GB · btrfs`
 *
 * 用于磁盘卡片这类空间紧张的位置。
 */
export function DiskCapacityLine({ disk }: { disk: DiskCapacityFields }) {
  const percent = usedPercent(disk.total_size, disk.used_size);
  return (
    <span className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[11px] text-subtle">
      <span>最大 {formatCapacity(disk.total_size)}</span>
      <span aria-hidden="true">·</span>
      <span>已用 {formatCapacity(disk.used_size)}</span>
      <span aria-hidden="true">·</span>
      <span>可用 {formatCapacity(disk.free_size)}</span>
      <span aria-hidden="true">·</span>
      <span>{orDash(disk.disk_type)}</span>
      {percent !== null && (
        <span
          className="ml-1 inline-block h-1 w-16 overflow-hidden rounded-full bg-[var(--border)] align-middle"
          role="progressbar"
          aria-valuenow={Math.round(percent)}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="磁盘已用空间占比"
        >
          <span
            className="block h-full rounded-full bg-[var(--brand)]"
            style={{ width: `${percent}%` }}
          />
        </span>
      )}
    </span>
  );
}

/** 单个字段（管理表格用）：`最大空间 / 已用 / 可用 / 类型` */
export function DiskCapacityCells({ disk }: { disk: DiskCapacityFields }) {
  return (
    <>
      <td className="whitespace-nowrap px-3 py-2.5 text-[12.5px]">
        {formatCapacity(disk.total_size)}
      </td>
      <td className="whitespace-nowrap px-3 py-2.5 text-[12.5px]">
        {formatCapacity(disk.used_size)}
      </td>
      <td className="whitespace-nowrap px-3 py-2.5 text-[12.5px]">
        {formatCapacity(disk.free_size)}
      </td>
      <td className="whitespace-nowrap px-3 py-2.5 text-[12.5px]">{orDash(disk.disk_type)}</td>
    </>
  );
}

/** 表格表头（与 `DiskCapacityCells` 顺序严格对应） */
export function DiskCapacityHeaders() {
  return (
    <>
      <th className="whitespace-nowrap px-3 py-2 text-left text-[11.5px] font-medium text-subtle">
        最大空间
      </th>
      <th className="whitespace-nowrap px-3 py-2 text-left text-[11.5px] font-medium text-subtle">
        已用空间
      </th>
      <th className="whitespace-nowrap px-3 py-2 text-left text-[11.5px] font-medium text-subtle">
        可用空间
      </th>
      <th className="whitespace-nowrap px-3 py-2 text-left text-[11.5px] font-medium text-subtle">
        磁盘类型
      </th>
    </>
  );
}
