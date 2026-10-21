/**
 * 离线下载 · 可下载项清单（v1.8.0）
 *
 * 对应需求 F23.5 / F24 / AC-210：
 *   - 逐项展示类型、显示名、时长、预估大小（带「约」）、分辨率；
 *   - 勾选（全选 / 全不选 / 只要最高清 / 只要音频 / 只要图片）；
 *   - 图片页出现「继续抓下一页」，由用户主动追加（默认只抓当前页）。
 *
 * 组件只负责展示与转发选择结果，勾选集合由父组件持有（NewTaskDialog），
 * 这样「开始下载」提交前能统一做校验与汇总。
 */

import { Badge, Button, Icon, type IconName } from '../../components/ui';
import type { OfflineItem } from '../../services/offline-download-service';
import {
  formatDurationMs,
  itemSizeLabel,
  pickAllIds,
  pickByKind,
  pickHighestQuality,
} from './offline-utils';

/** 清单类型 → 中文名 / 图标 / 配色 */
const KIND_META: Record<string, { label: string; icon: IconName; cls: string }> = {
  video: { label: '视频', icon: 'video', cls: 'file-ico-video' },
  audio: { label: '音频', icon: 'audio', cls: 'file-ico-doc' },
  image: { label: '图片', icon: 'image', cls: 'file-ico-img' },
  subtitle: { label: '字幕', icon: 'doc', cls: 'file-ico-doc' },
};

interface ItemPickerProps {
  items: OfflineItem[];
  selectedIds: ReadonlySet<number>;
  /** 分析结果加载/下一页追加期间禁用选择 */
  disabled?: boolean;
  /** 服务端 `has_more_images`：还有下一页图片可追加 */
  hasMore: boolean;
  nextPageLoading: boolean;
  onToggle: (itemId: number) => void;
  /** 整体替换选择（快捷选择按钮使用） */
  onSelectionChange: (itemIds: number[]) => void;
  onNextPage: () => void;
}

/** 可下载项清单 */
export function ItemPicker({
  items,
  selectedIds,
  disabled = false,
  hasMore,
  nextPageLoading,
  onToggle,
  onSelectionChange,
  onNextPage,
}: ItemPickerProps) {
  const hasKind = (kind: 'audio' | 'image') => items.some((item) => item.kind === kind);

  return (
    <div className="card overflow-hidden" style={{ padding: 0 }}>
      <div className="border-b border-line-subtle px-4 py-3">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[13px] font-semibold">可下载项</span>
          <Badge tone="neutral">{items.length} 项</Badge>
          <div className="grow" />
          <Button
            size="sm"
            icon="checkAll"
            disabled={disabled || items.length === 0}
            onClick={() => onSelectionChange(pickAllIds(items))}
          >
            全选
          </Button>
          <Button
            size="sm"
            icon="close"
            disabled={disabled || selectedIds.size === 0}
            onClick={() => onSelectionChange([])}
          >
            全不选
          </Button>
          <Button
            size="sm"
            icon="zap"
            disabled={disabled || items.length === 0}
            title="只保留分辨率最高的一项"
            onClick={() => onSelectionChange(pickHighestQuality(items))}
          >
            只要最高清
          </Button>
          <Button
            size="sm"
            icon="audio"
            disabled={disabled || !hasKind('audio')}
            title={hasKind('audio') ? '只勾选音频项' : '本页没有音频项'}
            onClick={() => onSelectionChange(pickByKind(items, 'audio'))}
          >
            只要音频
          </Button>
          <Button
            size="sm"
            icon="image"
            disabled={disabled || !hasKind('image')}
            title={hasKind('image') ? '只勾选图片项' : '本页没有图片项'}
            onClick={() => onSelectionChange(pickByKind(items, 'image'))}
          >
            只要图片
          </Button>
        </div>
        <p className="mt-1 mb-0 text-[11.5px] leading-relaxed text-subtle">
          勾选要下载的内容；音视频分离的流已由服务端合并为一条，不会出现「视频轨 / 音频轨」重复项。
        </p>
      </div>

      {items.length === 0 ? (
        <p className="m-0 px-4 py-8 text-center text-[13px] text-subtle">
          没有可下载项
        </p>
      ) : (
        <div className="max-h-[380px] overflow-y-auto">
          {items.map((item) => {
            const meta = KIND_META[item.kind] ?? {
              label: item.kind || '其他',
              icon: 'file' as IconName,
              cls: '',
            };
            const checked = selectedIds.has(item.id);
            const resolution =
              item.width > 0 && item.height > 0 ? `${item.width}×${item.height}` : '';
            const duration = formatDurationMs(item.duration_ms);
            return (
              <label
                key={item.id}
                className={[
                  'flex cursor-pointer items-start gap-3 border-b border-line-subtle px-4 py-2.5 transition-colors last:border-b-0 hover:bg-hover',
                  disabled && 'cursor-not-allowed opacity-60',
                ]
                  .filter(Boolean)
                  .join(' ')}
              >
                <input
                  type="checkbox"
                  className="accent-brand-500 mt-1 shrink-0"
                  checked={checked}
                  disabled={disabled}
                  onChange={() => onToggle(item.id)}
                  aria-label={`${meta.label} ${item.display_name || item.id}`}
                />
                <span className={`file-ico ${meta.cls} shrink-0`}>
                  <Icon name={meta.icon} size="lg" />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="truncate text-[13px] font-medium">
                      {item.display_name || `${meta.label} #${item.id}`}
                    </span>
                    <Badge tone="neutral">{meta.label}</Badge>
                    {item.ext && (
                      <span className="text-[11px] text-subtle">.{item.ext}</span>
                    )}
                  </div>
                  <p className="tabular mt-0.5 mb-0 flex flex-wrap gap-x-3 gap-y-0.5 text-[11.5px] text-subtle">
                    {resolution && <span>{resolution}</span>}
                    {duration && <span>{duration}</span>}
                    <span>{itemSizeLabel(item)}</span>
                    {item.status !== 'pending' && <span>状态：{item.status}</span>}
                  </p>
                  {item.error_message && (
                    <p
                      className="mt-0.5 mb-0 text-[11.5px]"
                      style={{ color: 'var(--danger-700)' }}
                    >
                      {item.error_message}
                    </p>
                  )}
                </div>
              </label>
            );
          })}
        </div>
      )}

      {hasMore && (
        <div className="flex flex-wrap items-center gap-2 border-t border-line-subtle bg-sunken px-4 py-3">
          <Button
            size="sm"
            icon="next"
            loading={nextPageLoading}
            disabled={disabled}
            onClick={onNextPage}
          >
            继续抓下一页
          </Button>
          <span className="text-[11.5px] text-subtle">
            只追加图片，已抓到的项不会重复；页数与张数上限由服务端控制。
          </span>
        </div>
      )}
    </div>
  );
}
