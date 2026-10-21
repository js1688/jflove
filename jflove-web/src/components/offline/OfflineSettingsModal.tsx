/**
 * 离线下载 · 「我的默认目录」设置弹窗（v1.8.0）
 *
 * 对应需求 F25 / 设计文档 §3.5：
 *   - 默认目录 = 「虚拟磁盘 + 相对路径」，**按账号隔离**存在服务端（`/offline/settings`）；
 *   - 磁盘列表只来自 `/offline/meta` 的 `writable_disks`（服务端已按 write 权限过滤），
 *     客户端**不自行判断**权限；
 *   - 任何登录账号（含普通账号）都能配自己的目录；
 *   - 无可写磁盘时全部控件禁用 + 中文说明（F25.3）。
 *
 * 这里**不写 localStorage / sessionStorage**：默认值每次进入页面都从服务端取，
 * 保证与桌面端 / 移动端看到同一份（D9 / AC-213）。
 */

import { useState } from 'react';
import { DirTreeModal } from '../DirTreeModal';
import { Button, Icon, Modal, toast } from '../ui';
import { offlineDownloadService } from '../../services/offline-download-service';
import type { OfflineMeta } from '../../types/models';

export interface OfflineSettingsModalProps {
  /** `/offline/meta` 的最新快照（只含自己的配置与可写磁盘） */
  meta: OfflineMeta;
  onClose: () => void;
  /** 保存/清除成功后回调（父级负责重新拉 `/offline/meta`） */
  onSaved: (message: string) => void;
}

/** 从异常里取可读消息（避免把 Error 对象直接拼进文案） */
function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function OfflineSettingsModal({ meta, onClose, onSaved }: OfflineSettingsModalProps) {
  const hasDisk = meta.writable_disks.length > 0;
  const [diskId, setDiskId] = useState<number>(() => {
    if (meta.config.disk_id > 0) return meta.config.disk_id;
    return meta.writable_disks[0]?.disk_id ?? 0;
  });
  const [relPath, setRelPath] = useState(meta.config.rel_path ?? '');
  /** 目录不存在时是否在**该磁盘内**创建（对应接口 `create`） */
  const [create, setCreate] = useState(!meta.configured);
  const [saving, setSaving] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [browsing, setBrowsing] = useState(false);

  const diskName = meta.writable_disks.find((d) => d.disk_id === diskId)?.name ?? '';

  const handleSave = async () => {
    if (!hasDisk || diskId <= 0) {
      toast.error('无法保存', '请先选择一个可写磁盘');
      return;
    }
    setSaving(true);
    try {
      await offlineDownloadService.saveSettings(diskId, relPath.trim(), create);
      toast.success('默认目录已保存', `${diskName} / ${relPath.trim() || '根目录'}`);
      onSaved('默认下载目录已更新');
    } catch (e) {
      // 失败**不关闭**弹窗，保留已填内容便于修正重试（SKILL.md 硬约束 1）
      toast.error('保存失败', messageOf(e));
    } finally {
      setSaving(false);
    }
  };

  const handleClear = async () => {
    setClearing(true);
    try {
      await offlineDownloadService.saveSettings(0, '');
      toast.success('已清除默认目录', '下次创建任务前需要重新配置');
      onSaved('默认下载目录已清除');
    } catch (e) {
      toast.error('清除失败', messageOf(e));
    } finally {
      setClearing(false);
    }
  };

  return (
    <Modal
      open
      title="我的默认下载目录"
      icon="folder"
      width={560}
      onClose={onClose}
      footer={
        <>
          <Button
            variant="ghost"
            icon="delete"
            disabled={saving || clearing || meta.config.disk_id <= 0}
            loading={clearing}
            onClick={handleClear}
          >
            清除目录
          </Button>
          <div className="grow" />
          <Button variant="ghost" onClick={onClose}>
            关闭
          </Button>
          <Button
            variant="primary"
            icon="save"
            disabled={!hasDisk}
            loading={saving}
            onClick={handleSave}
          >
            保存
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        <p className="m-0 text-[12.5px] leading-relaxed text-muted">
          默认目录用于「新建任务」的预填，按账号保存在服务端 —— 桌面端 / 移动端 / Web 端看到的是
          <span className="font-medium">同一份</span>。此处保存只影响你自己的账号。
        </p>

        {!hasDisk && (
          <div className="flex items-start gap-2 rounded-lg border border-warning-500/30 bg-warning-50 p-3 text-[12.5px] text-warning-700">
            <Icon name="warning" size="sm" className="mt-px shrink-0" />
            <span>你没有可写入的磁盘，无法保存目录配置，也无法使用离线下载。</span>
          </div>
        )}

        {meta.config.disk_id > 0 && !meta.config.valid && (
          <div className="flex items-start gap-2 rounded-lg border border-danger-500/25 bg-danger-50 p-3 text-[12.5px] text-danger-700">
            <Icon name="error" size="sm" className="mt-px shrink-0" />
            <span>当前保存的目录已失效（磁盘被删除或写权限被收回），请重新选择后保存。</span>
          </div>
        )}

        <div>
          <label
            className="mb-1 block text-[12.5px] font-medium text-muted"
            htmlFor="offline-settings-disk"
          >
            磁盘
          </label>
          <select
            id="offline-settings-disk"
            className="input bg-surface"
            value={diskId}
            disabled={!hasDisk || saving}
            onChange={(e) => setDiskId(Number(e.target.value))}
          >
            {meta.writable_disks.map((disk) => (
              <option key={disk.disk_id} value={disk.disk_id}>
                {disk.name}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label
            className="mb-1 block text-[12.5px] font-medium text-muted"
            htmlFor="offline-settings-path"
          >
            磁盘内相对路径
          </label>
          <div className="flex gap-2">
            <input
              id="offline-settings-path"
              className="input flex-1"
              placeholder="例如 downloads/media（留空表示磁盘根目录）"
              value={relPath}
              disabled={!hasDisk || saving}
              onChange={(e) => setRelPath(e.target.value)}
            />
            <Button
              icon="folder"
              disabled={!hasDisk || diskId <= 0 || saving}
              onClick={() => setBrowsing(true)}
            >
              浏览
            </Button>
          </div>
          <p className="mt-1 text-[11.5px] text-subtle">
            只接受磁盘内的相对路径；拒绝绝对路径、`..` 越界与 `.jflove-*` 隐藏目录。
          </p>
        </div>

        <label className="flex items-center gap-2 text-[12.5px] text-muted">
          <input
            type="checkbox"
            className="h-4 w-4 accent-brand-500"
            checked={create}
            disabled={!hasDisk || saving}
            onChange={(e) => setCreate(e.target.checked)}
          />
          目录不存在时自动创建（只会在所选磁盘内创建）
        </label>
      </div>

      {browsing && hasDisk && diskId > 0 && (
        <DirTreeModal
          diskId={diskId}
          diskName={diskName}
          onSelect={(path) => {
            setRelPath(path);
            setBrowsing(false);
          }}
          onClose={() => setBrowsing(false)}
        />
      )}
    </Modal>
  );
}
