/**
 * 离线下载纯函数测试（v1.8.0）
 *
 * 重点锁死**最容易做错的规则**（需求 F24 / AC-210）：
 *   - 「只要最高清」= 恰好 1 项且是最大分辨率项；
 *   - 「只要音频」「只要图片」= 对应类型全选；
 *   - 轮询停止条件（全部终态）、轮询间隔兜底；
 *   - 四态 `unusable_reason` 的中文文案。
 */

import { describe, expect, it } from 'vitest';
import {
  DEFAULT_POLL_INTERVAL_MS,
  allTasksTerminal,
  canAnalyze,
  canCancelTask,
  canDeleteRecord,
  canOperateTask,
  canRetryTask,
  formatDurationMs,
  formatIsoTime,
  formatSpeed,
  hasItemKind,
  isReadOnlyTask,
  itemMetaText,
  itemSizeText,
  pickBestItem,
  pickByQuickSelect,
  pickNearestSelection,
  resolvePollInterval,
  summarizeSelection,
  taskProgress,
  taskProgressText,
  taskStatusTone,
  unusableNotice,
} from '../../src/utils/offline-download';
import type { OfflineItem, OfflineMeta, OfflineTask } from '../../src/types/models';

/** 构造清单项（未给的字段用 0 / 空） */
function item(partial: Partial<OfflineItem> & { id: number }): OfflineItem {
  return {
    kind: 'video',
    display_name: `item-${partial.id}`,
    ext: 'mp4',
    width: 0,
    height: 0,
    duration_ms: 0,
    est_size: 0,
    is_estimated: false,
    is_selected: false,
    status: 'pending',
    error_message: '',
    ...partial,
  };
}

/** 构造任务行 */
function task(partial: Partial<OfflineTask> & { id: number }): OfflineTask {
  return {
    source_domain: 'example.com',
    page_title: '示例',
    status: 'downloading',
    status_text: '下载中',
    item_total: 0,
    item_done: 0,
    bytes_total: 0,
    bytes_done: 0,
    speed_bps: 0,
    target_disk_name: '主盘',
    target_rel_path: 'downloads',
    error_code: '',
    error_message: '',
    created_at: '2026-10-20T10:00:00+08:00',
    started_at: null,
    finished_at: null,
    can_operate: true,
    ...partial,
  };
}

/** 构造 meta（可写磁盘 + 已配置的默认目录） */
function meta(partial: Partial<OfflineMeta> = {}): OfflineMeta {
  return {
    enabled: true,
    deep_analyze_enabled: true,
    deep_analyze_available: true,
    can_use: true,
    configured: true,
    unusable_reason: '',
    config: { disk_id: 1, rel_path: 'downloads', valid: true },
    writable_disks: [{ disk_id: 1, name: '主盘' }],
    max_items: 50,
    max_file_size: 4 * 1024 ** 3,
    max_images: 200,
    poll_interval_ms: 2500,
    ...partial,
  };
}

describe('resolvePollInterval 轮询间隔兜底', () => {
  it('缺字段 / 0 / 负数 / 非数字 一律回落 2500ms（设计 §3.2）', () => {
    expect(resolvePollInterval(undefined)).toBe(DEFAULT_POLL_INTERVAL_MS);
    expect(resolvePollInterval(null)).toBe(DEFAULT_POLL_INTERVAL_MS);
    expect(resolvePollInterval(0)).toBe(DEFAULT_POLL_INTERVAL_MS);
    expect(resolvePollInterval(-1)).toBe(DEFAULT_POLL_INTERVAL_MS);
    expect(resolvePollInterval('abc')).toBe(DEFAULT_POLL_INTERVAL_MS);
  });

  it('正常值原样保留，并夹到 [100ms, 60s]', () => {
    expect(resolvePollInterval(2500)).toBe(2500);
    expect(resolvePollInterval(20)).toBe(100);
    expect(resolvePollInterval(10 * 60_000)).toBe(60_000);
  });
});

describe('快捷选择（F24 / AC-210）', () => {
  const items: OfflineItem[] = [
    item({ id: 1, kind: 'video', width: 1920, height: 1080, est_size: 300 }),
    item({ id: 2, kind: 'video', width: 640, height: 360, est_size: 60 }),
    item({ id: 3, kind: 'audio', est_size: 20 }),
    item({ id: 4, kind: 'audio', est_size: 8 }),
    item({ id: 5, kind: 'image', width: 800, height: 600 }),
    item({ id: 6, kind: 'image', width: 400, height: 300 }),
  ];

  it('「只要最高清」恰好 1 项，且是最大分辨率项', () => {
    expect(pickByQuickSelect(items, 'best')).toEqual([1]);
    expect(pickNearestSelection(items)).toEqual([1]);
  });

  it('「只要音频」= 全部音频项', () => {
    expect(pickByQuickSelect(items, 'audio')).toEqual([3, 4]);
  });

  it('「只要图片」= 全部图片项', () => {
    expect(pickByQuickSelect(items, 'image')).toEqual([5, 6]);
  });

  it('没有视频时「最高清」退回体积最大的音频', () => {
    const audioOnly = [item({ id: 1, kind: 'audio', est_size: 5 }), item({ id: 2, kind: 'audio', est_size: 9 })];
    expect(pickByQuickSelect(audioOnly, 'best')).toEqual([2]);
  });

  it('只有图片时「最高清」退回第一项（图片无分辨率合并规则）', () => {
    const imageOnly = [item({ id: 7, kind: 'image', width: 100, height: 100 })];
    expect(pickByQuickSelect(imageOnly, 'best')).toEqual([7]);
  });

  it('空清单返回空选择 / null', () => {
    expect(pickByQuickSelect([], 'best')).toEqual([]);
    expect(pickBestItem([])).toBeNull();
  });

  it('hasItemKind 用于禁用「只要音频 / 只要图片」按钮', () => {
    expect(hasItemKind(items, 'audio')).toBe(true);
    expect(hasItemKind(items, 'subtitle')).toBe(false);
  });
});

describe('勾选汇总', () => {
  const items = [
    item({ id: 1, est_size: 100, is_estimated: false }),
    item({ id: 2, est_size: 50, is_estimated: true }),
  ];

  it('统计已选数量 / 总字节 / 是否含估算值', () => {
    expect(summarizeSelection(items, new Set([1]))).toEqual({
      count: 1,
      bytes: 100,
      estimated: false,
    });
    expect(summarizeSelection(items, new Set([1, 2]))).toEqual({
      count: 2,
      bytes: 150,
      estimated: true,
    });
    expect(summarizeSelection(items, new Set())).toEqual({
      count: 0,
      bytes: 0,
      estimated: false,
    });
  });
});

describe('任务终态与进度', () => {
  it('终态 = success / failed / canceled', () => {
    expect(allTasksTerminal([])).toBe(true);
    expect(
      allTasksTerminal([task({ id: 1, status: 'success' }), task({ id: 2, status: 'canceled' })]),
    ).toBe(true);
    expect(allTasksTerminal([task({ id: 1, status: 'downloading' })])).toBe(false);
    expect(allTasksTerminal([task({ id: 1, status: 'ready' })])).toBe(false);
    expect(allTasksTerminal([task({ id: 1, status: 'queued' })])).toBe(false);
  });

  it('进度：优先按字节，其次按项数', () => {
    expect(taskProgress(task({ id: 1, bytes_total: 200, bytes_done: 50 }))).toBe(25);
    expect(
      taskProgress(task({ id: 1, bytes_total: 0, item_total: 4, item_done: 1 })),
    ).toBe(25);
    expect(taskProgress(task({ id: 1, status: 'success' }))).toBe(100);
    expect(taskProgress(task({ id: 1, item_total: 0 }))).toBe(0);
  });

  it('成功任务无条件 100%（v1.8.1 / AC-256：不按字节估算值回退）', () => {
    expect(
      taskProgress(task({ id: 1, status: 'success', bytes_total: 100, bytes_done: 99 })),
    ).toBe(100);
    expect(
      taskProgress(task({ id: 1, status: 'success', bytes_total: 0, item_total: 4, item_done: 1 })),
    ).toBe(100);
    // 页面文案同口径带 100%（AC-256「页面成功任务显示 100%」）
    expect(
      taskProgressText(task({ id: 1, status: 'success', bytes_total: 100, bytes_done: 99 })),
    ).toContain('100%');
  });

  it('任务状态徽章配色', () => {
    expect(taskStatusTone('success')).toBe('success');
    expect(taskStatusTone('failed')).toBe('danger');
    expect(taskStatusTone('canceled')).toBe('neutral');
    expect(taskStatusTone('ready')).toBe('warning');
    expect(taskStatusTone('downloading')).toBe('brand');
  });

  it('进度文案按状态分支', () => {
    expect(taskProgressText(task({ id: 1, status: 'ready', item_total: 7 }))).toContain('7');
    expect(
      taskProgressText(
        task({ id: 1, status: 'downloading', bytes_total: 100, bytes_done: 50, speed_bps: 1024 }),
      ),
    ).toContain('50%');
    expect(taskProgressText(task({ id: 1, status: 'canceled' }))).toBe('任务已取消');
    expect(taskProgressText(task({ id: 1, status: 'queued' }))).toContain('排队');
  });
});

describe('展示格式化', () => {
  it('时长：3:34 / 1:02:03；未知返回空串', () => {
    expect(formatDurationMs(214_000)).toBe('3:34');
    expect(formatDurationMs(3_723_000)).toBe('1:02:03');
    expect(formatDurationMs(0)).toBe('');
    expect(formatDurationMs(-1)).toBe('');
  });

  it('速度：仅在已知时给文案', () => {
    expect(formatSpeed(0)).toBe('');
    expect(formatSpeed(1024)).toBe('1.0 KB/s');
  });

  it('ISO 时间转本地文案；空/非法返回 —', () => {
    expect(formatIsoTime(null)).toBe('—');
    expect(formatIsoTime('not-a-date')).toBe('—');
    expect(formatIsoTime('2026-10-20T21:43:05+08:00')).toMatch(
      /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$/,
    );
  });

  it('清单项副标题 / 大小文案（估算要标「约」，F23.2）', () => {
    const video = item({ id: 1, width: 1920, height: 1080, ext: 'mp4', duration_ms: 214_000 });
    expect(itemMetaText(video)).toBe('1920×1080 · MP4 · 3:34');
    expect(itemMetaText(item({ id: 2, width: 0, height: 0, ext: '' }))).toBe('');

    expect(itemSizeText(item({ id: 3, est_size: 2048 }))).toBe('2.0 KB');
    expect(itemSizeText(item({ id: 4, est_size: 2048, is_estimated: true }))).toBe('约 2.0 KB');
    expect(itemSizeText(item({ id: 5, est_size: 0 }))).toBe('');
  });
});

describe('unusable_reason 四态文案（AC-214 / AC-214b / AC-214c）', () => {
  it('disabled', () => {
    const notice = unusableNotice('disabled');
    expect(notice?.title).toContain('已关闭');
    expect(notice?.needsConfigure).toBe(false);
  });

  it('no_writable_disk 使用需求原文（F25.3）', () => {
    const notice = unusableNotice('no_writable_disk');
    expect(notice?.title).toBe('你没有可写入的磁盘，无法使用离线下载');
  });

  it('not_configured 引导去配置', () => {
    const notice = unusableNotice('not_configured');
    expect(notice?.title).toBe('请先设置离线下载目录');
    expect(notice?.needsConfigure).toBe(true);
  });

  it('config_invalid 提示重新配置', () => {
    const notice = unusableNotice('config_invalid');
    expect(notice?.title).toBe('离线下载目录已失效，请重新配置');
    expect(notice?.needsConfigure).toBe(true);
  });

  it('空串（可用）与未知原因都不产生提示', () => {
    expect(unusableNotice('')).toBeNull();
    expect(unusableNotice('something_new')).toBeNull();
  });
});

describe('操作可用性判定', () => {
  it('canAnalyze：总开关 + 可写磁盘 + 已配置有效目录 三者缺一不可', () => {
    expect(canAnalyze(meta())).toBe(true);
    expect(canAnalyze(null)).toBe(false);
    expect(canAnalyze(meta({ enabled: false }))).toBe(false);
    expect(canAnalyze(meta({ writable_disks: [] }))).toBe(false);
    expect(canAnalyze(meta({ configured: false }))).toBe(false);
  });

  it('canRetryTask：仅 failed / canceled，且排除不可重试类', () => {
    expect(canRetryTask(task({ id: 1, status: 'failed', error_code: 'net_timeout' }))).toBe(true);
    expect(canRetryTask(task({ id: 1, status: 'canceled' }))).toBe(true);
    expect(canRetryTask(task({ id: 1, status: 'failed', error_code: 'drm_protected' }))).toBe(
      false,
    );
    expect(canRetryTask(task({ id: 1, status: 'failed', error_code: 'login_required' }))).toBe(
      false,
    );
    expect(canRetryTask(task({ id: 1, status: 'downloading' }))).toBe(false);
  });

  it('canDeleteRecord：终态与待选择可删记录，进行中先取消', () => {
    expect(canDeleteRecord(task({ id: 1, status: 'ready' }))).toBe(true);
    expect(canDeleteRecord(task({ id: 1, status: 'success' }))).toBe(true);
    expect(canDeleteRecord(task({ id: 1, status: 'downloading' }))).toBe(false);
  });

  it('canCancelTask：进行中 + 待选择可取消，终态不可', () => {
    for (const status of ['queued', 'analyzing', 'downloading', 'merging', 'ready'] as const) {
      expect(canCancelTask(task({ id: 1, status }))).toBe(true);
    }
    for (const status of ['success', 'failed', 'canceled'] as const) {
      expect(canCancelTask(task({ id: 1, status }))).toBe(false);
    }
  });

  it('canOperateTask：未绑定目标磁盘时只看归属，绑定后必须 can_operate', () => {
    // 分析中 / 待选择：服务端 can_operate 必然是 false（target_disk_id=0），但不能因此禁用操作
    expect(
      canOperateTask(
        task({ id: 1, status: 'analyzing', can_operate: false, target_disk_name: '' }),
      ),
    ).toBe(true);
    // 已绑定目标磁盘且写权限被收回：不可操作（AC-229 / AC-242）
    expect(canOperateTask(task({ id: 1, status: 'failed', can_operate: false }))).toBe(false);
    // 正常有权限
    expect(canOperateTask(task({ id: 1, status: 'downloading', can_operate: true }))).toBe(true);
  });

  it('isReadOnlyTask：只有「已绑定目标磁盘且无写权限」才标只读', () => {
    expect(
      isReadOnlyTask(task({ id: 1, status: 'analyzing', can_operate: false, target_disk_name: '' })),
    ).toBe(false);
    expect(isReadOnlyTask(task({ id: 1, status: 'success', can_operate: false }))).toBe(true);
    expect(isReadOnlyTask(task({ id: 1, status: 'success', can_operate: true }))).toBe(false);
  });
});
