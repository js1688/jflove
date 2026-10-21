/**
 * 离线下载纯函数判据（v1.8.0）
 *
 * 覆盖 AC-210（快捷选择：最高清只选 1 项）与 F27.1（来源域名脱敏）、F29（中文错误文案）：
 * 这些规则一旦被改坏，界面会"看起来正常但业务判据错了"，因此用纯函数单测直接锁死。
 */

import { describe, expect, it } from 'vitest';
import { ApiError } from '../../../types/models';
import type { OfflineItem } from '../../../services/offline-download-service';
import {
  canOperateTask,
  canRetryTask,
  errorSuggestion,
  formatDurationMs,
  formatIsoTime,
  formatSpeed,
  isTerminalStatus,
  itemSizeLabel,
  maskSourceDomain,
  offlineErrorText,
  pickAllIds,
  pickByKind,
  pickHighestQuality,
  selectedSummary,
  selectedSummaryLabel,
  taskPercent,
} from '../offline-utils';

/** 构造一个字段完整的清单项（只覆盖用例关心的字段） */
function makeItem(overrides: Partial<OfflineItem> & { id: number }): OfflineItem {
  return {
    kind: 'video',
    display_name: `item-${overrides.id}`,
    ext: 'mp4',
    width: 0,
    height: 0,
    duration_ms: 0,
    est_size: 0,
    is_estimated: false,
    is_selected: false,
    status: 'pending',
    error_message: '',
    ...overrides,
  };
}

const SAMPLE_ITEMS: OfflineItem[] = [
  makeItem({ id: 1, width: 1920, height: 1080, est_size: 2_000, display_name: '1080P' }),
  makeItem({ id: 2, width: 1280, height: 720, est_size: 1_000, display_name: '720P' }),
  makeItem({ id: 3, kind: 'audio', est_size: 300, display_name: '仅音频' }),
  makeItem({ id: 4, kind: 'image', width: 800, height: 600, est_size: 100, display_name: '图片' }),
];

describe('来源域名脱敏（F27.1：不展示完整 URL）', () => {
  it('保留首段首字符，其余隐去', () => {
    expect(maskSourceDomain('youtube.com')).toBe('y***.com');
    expect(maskSourceDomain('video.example.com')).toBe('v***.example.com');
  });

  it('空值 / 单段 / 超长域名都有稳定输出', () => {
    expect(maskSourceDomain('')).toBe('—');
    expect(maskSourceDomain('localhost')).toBe('l***');
    const long = maskSourceDomain(`a.${'b'.repeat(80)}.com`);
    expect(long.length).toBeLessThanOrEqual(64);
    expect(long.endsWith('…')).toBe(true);
  });
});

describe('快捷选择（AC-210）', () => {
  it('「只要最高清」只选 1 项，且是分辨率最高的视频', () => {
    const ids = pickHighestQuality(SAMPLE_ITEMS);
    expect(ids).toEqual([1]);
    expect(ids).toHaveLength(1);
  });

  it('「只要最高清」在没有视频项时按预估大小回退，仍只选 1 项', () => {
    const items = [
      makeItem({ id: 10, kind: 'audio', est_size: 100 }),
      makeItem({ id: 11, kind: 'image', est_size: 900 }),
    ];
    expect(pickHighestQuality(items)).toEqual([11]);
  });

  it('「只要音频」「只要图片」只返回对应类型', () => {
    expect(pickByKind(SAMPLE_ITEMS, 'audio')).toEqual([3]);
    expect(pickByKind(SAMPLE_ITEMS, 'image')).toEqual([4]);
    expect(pickByKind(SAMPLE_ITEMS, 'video')).toEqual([1, 2]);
  });

  it('全选返回所有 ID', () => {
    expect(pickAllIds(SAMPLE_ITEMS)).toEqual([1, 2, 3, 4]);
  });
});

describe('勾选汇总', () => {
  it('统计选中项数与预估总字节，并标记估算值', () => {
    const selected = new Set([1, 3]);
    expect(selectedSummary(SAMPLE_ITEMS, selected)).toEqual({
      count: 2,
      bytes: 2300,
      estimated: false,
    });
    const estimated = new Set([1]);
    expect(selectedSummary([makeItem({ id: 1, est_size: 1024, is_estimated: true })], estimated)).toEqual({
      count: 1,
      bytes: 1024,
      estimated: true,
    });
  });

  it('汇总文案带「约」且格式稳定', () => {
    expect(selectedSummaryLabel(SAMPLE_ITEMS, new Set([1, 2]))).toBe('已选 2 项 · 2.9 KB');
    expect(
      selectedSummaryLabel(
        [makeItem({ id: 1, est_size: 1024, is_estimated: true })],
        new Set([1]),
      ),
    ).toBe('已选 1 项 · 约 1.0 KB');
    expect(selectedSummaryLabel(SAMPLE_ITEMS, new Set())).toBe('已选 0 项 · 0 B');
  });
});

describe('状态与展示格式化', () => {
  it('终态判定', () => {
    expect(isTerminalStatus('success')).toBe(true);
    expect(isTerminalStatus('failed')).toBe(true);
    expect(isTerminalStatus('canceled')).toBe(true);
    expect(isTerminalStatus('downloading')).toBe(false);
    expect(isTerminalStatus('ready')).toBe(false);
  });

  it('可重试判定排除服务端拒绝的失败类型', () => {
    expect(canRetryTask('failed', 'net_timeout')).toBe(true);
    expect(canRetryTask('canceled', '')).toBe(true);
    expect(canRetryTask('failed', 'drm_protected')).toBe(false);
    expect(canRetryTask('success', 'net_timeout')).toBe(false);
  });

  it('未绑定目标磁盘的任务仍允许取消/重试（分析中阶段 can_operate 必为 false）', () => {
    // 分析中：target_disk_name 为空，服务端 can_operate=false，但这是"还没选目录"
    expect(canOperateTask({ target_disk_name: '', can_operate: false })).toBe(true);
    // 已绑定目标磁盘：权限被收回 ⇒ 只读
    expect(canOperateTask({ target_disk_name: '磁盘 A', can_operate: false })).toBe(false);
    expect(canOperateTask({ target_disk_name: '磁盘 A', can_operate: true })).toBe(true);
  });

  it('进度优先按字节、无字节时按项目数', () => {
    expect(taskPercent({ bytes_done: 50, bytes_total: 200, item_done: 0, item_total: 0 })).toBe(25);
    expect(taskPercent({ bytes_done: 0, bytes_total: 0, item_done: 1, item_total: 4 })).toBe(25);
    expect(taskPercent({ bytes_done: 0, bytes_total: 0, item_done: 0, item_total: 0 })).toBe(0);
    expect(taskPercent({ bytes_done: 300, bytes_total: 200, item_done: 0, item_total: 0 })).toBe(100);
  });

  it('时长 / 大小 / 速度格式化', () => {
    expect(formatDurationMs(0)).toBe('');
    expect(formatDurationMs(214_000)).toBe('3:34');
    expect(formatDurationMs(3_723_000)).toBe('1:02:03');
    expect(itemSizeLabel({ est_size: 0, is_estimated: true })).toBe('大小未知');
    expect(itemSizeLabel({ est_size: 1024, is_estimated: true })).toBe('≈ 1.0 KB');
    expect(itemSizeLabel({ est_size: 1024, is_estimated: false })).toBe('1.0 KB');
    expect(formatSpeed(0)).toBe('—');
    expect(formatSpeed(2048)).toBe('2.0 KB/s');
  });

  it('ISO 时间转本地格式；非法值返回 —', () => {
    expect(formatIsoTime(null)).toBe('—');
    expect(formatIsoTime('not-a-date')).toBe('—');
    expect(formatIsoTime('2026-10-20T12:34:56+08:00')).toBe('2026-10-20 12:34');
  });
});

describe('中文错误文案（F29）', () => {
  it('ApiError 优先展示服务端 detail（不带 [status] 前缀）', () => {
    expect(offlineErrorText(new ApiError(403, '没有该磁盘的写入权限'))).toBe('没有该磁盘的写入权限');
  });

  it('普通 Error 透传 message，未知值用兜底', () => {
    expect(offlineErrorText(new Error('网络错误'))).toBe('网络错误');
    expect(offlineErrorText(undefined, '操作失败')).toBe('操作失败');
  });

  it('已知失败类型有下一步建议，未知类型为空', () => {
    expect(errorSuggestion('disk_full')).toContain('清理');
    expect(errorSuggestion('drm_protected')).toContain('版权');
    expect(errorSuggestion('unknown_code')).toBe('');
  });
});
