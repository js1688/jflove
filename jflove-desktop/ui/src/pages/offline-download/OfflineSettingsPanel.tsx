/**
 * 离线下载 · 设置面板（v1.8.0）
 *
 * 对应需求 F25 / 设计 §3.5：
 *   - 只能从 `/meta.writable_disks`（服务端已按 write 权限过滤）里选磁盘；
 *   - 保存 / 清除**自己的**默认目录（`POST /offline/settings`，`disk_id=0` 表示清除）；
 *   - 账号没有可写磁盘时，整个面板只读并给出中文原因（不提交任何请求）；
 *   - 默认目录按账号存在服务端，桌面端与 Web 端共用；任务内临时改目录**不走这里**。
 */

import { useEffect, useState } from 'react';
import {
  Button,
  ConfirmDialog,
  Icon,
  Input,
  Modal,
  Switch,
  toast,
} from '../../components/ui';
import { DirTreeModal } from '../../components/DirTreeModal';
import { offlineDownloadService, type OfflineMeta } from '../../services/offline-download-service';
import { offlineErrorText } from './offline-utils';

interface OfflineSettingsPanelProps {
  meta: OfflineMeta;
  open: boolean;
  onClose: () => void;
  /** 保存/清除成功后刷新 `/meta`（父组件负责） */
  onSaved: () => void | Promise<void>;
}

/** 设置面板（只管当前账号自己的目录） */
export function OfflineSettingsPanel({
  meta,
  open,
  onClose,
  onSaved,
}: OfflineSettingsPanelProps) {
  const [diskId, setDiskId] = useState(0);
  const [relPath, setRelPath] = useState('');
  /** 目录不存在时允许在目标磁盘内创建（`POST /settings` 的 `create`） */
  const [create, setCreate] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [browseOpen, setBrowseOpen] = useState(false);
  const [confirmClear, setConfirmClear] = useState(false);

  const noWritableDisk = meta.writable_disks.length === 0;
  const targetDisk = meta.writable_disks.find((disk) => disk.disk_id === diskId);
  const configDiskWritable = meta.writable_disks.some(
    (disk) => disk.disk_id === meta.config.disk_id,
  );
  const configDiskName =
    meta.writable_disks.find((disk) => disk.disk_id === meta.config.disk_id)?.name ||
    `磁盘 #${meta.config.disk_id}`;

  // 每次打开 / meta 刷新时，把表单对齐到服务端的当前配置
  useEffect(() => {
    if (!open) return;
    const nextDisk = configDiskWritable
      ? meta.config.disk_id
      : meta.writable_disks[0]?.disk_id ?? 0;
    setDiskId(nextDisk);
    setRelPath(configDiskWritable ? meta.config.rel_path : '');
    setCreate(true);
    setError(null);
    setBrowseOpen(false);
  }, [configDiskWritable, meta, open]);

  const handleSave = async () => {
    if (diskId <= 0) {
      setError('请选择可写入的磁盘');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await offlineDownloadService.saveSettings(diskId, relPath.trim(), create);
      toast.success('默认目录已保存');
      await onSaved();
      onClose();
    } catch (e) {
      setError(offlineErrorText(e, '保存默认目录失败'));
    } finally {
      setSaving(false);
    }
  };

  const handleClear = async () => {
    setConfirmClear(false);
    setSaving(true);
    setError(null);
    try {
      await offlineDownloadService.saveSettings(0, '', false);
      toast.success('已清除默认目录');
      await onSaved();
    } catch (e) {
      setError(offlineErrorText(e, '清除默认目录失败'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <Modal
        open={open}
        title="离线下载设置"
        icon="settings"
        width={560}
        onClose={onClose}
        footer={
          <>
            <Button variant="ghost" onClick={onClose}>
              关闭
            </Button>
            <Button
              variant="ghost"
              icon="delete"
              disabled={saving || noWritableDisk || !meta.configured}
              onClick={() => setConfirmClear(true)}
            >
              清除默认目录
            </Button>
            <Button
              variant="primary"
              icon="save"
              loading={saving}
              disabled={saving || noWritableDisk || diskId <= 0}
              onClick={() => void handleSave()}
            >
              保存
            </Button>
          </>
        }
      >
        <p className="m-0 text-[12.5px] leading-relaxed text-muted">
          每个账号一份默认目录，保存在服务端；桌面端与 Web 端共用，且只影响你自己的任务。
          任务内临时改目录不会回写这里的默认值。
        </p>

        {noWritableDisk ? (
          <div className="mt-3 flex items-start gap-2 rounded-lg border border-warning-500/25 bg-warning-50 p-3 text-[12.5px] text-warning-700">
            <Icon name="warning" size="sm" className="mt-px shrink-0" />
            <span>
              你没有可写入的磁盘，无法配置离线下载目录。请联系管理员为你的账号分配可写磁盘。
            </span>
          </div>
        ) : (
          <>
            <div className="mt-3 rounded-lg border border-line-subtle bg-sunken px-3 py-2.5 text-[12.5px]">
              {meta.configured ? (
                <span className="text-success-700">
                  当前默认目录：{configDiskName} ·{' '}
                  {meta.config.rel_path ? `/${meta.config.rel_path}` : '磁盘根目录'}
                </span>
              ) : meta.config.disk_id > 0 && !meta.config.valid ? (
                <span className="text-warning-700">
                  当前配置已失效（磁盘被删除、写权限被收回或目录不存在），请重新选择后保存。
                </span>
              ) : (
                <span className="text-subtle">尚未配置默认目录。</span>
              )}
            </div>
          </>
        )}

        <div className="mt-3">
          <label className="block text-[12.5px]">
            <span className="mb-1 block font-medium text-muted">磁盘（仅列可写磁盘）</span>
            <select
              className="input bg-surface"
              value={noWritableDisk ? 0 : diskId}
              disabled={saving || noWritableDisk}
              onChange={(e) => setDiskId(Number(e.target.value))}
              aria-label="默认目录磁盘"
            >
              {noWritableDisk ? (
                <option value={0}>没有可写入的磁盘</option>
              ) : (
                meta.writable_disks.map((disk) => (
                  <option key={disk.disk_id} value={disk.disk_id}>
                    {disk.name}
                  </option>
                ))
              )}
            </select>
          </label>
        </div>

        <div className="mt-3 text-[12.5px]">
          <span className="mb-1 block font-medium text-muted">目录（相对路径）</span>
          <div className="flex gap-2">
            <Input
              className="flex-1"
              value={noWritableDisk ? '' : relPath}
              disabled={saving || noWritableDisk}
              placeholder="如 影视/离线下载（留空表示磁盘根目录）"
              onChange={(e) => setRelPath(e.target.value)}
              aria-label="默认目录相对路径"
            />
            <Button
              icon="folder"
              disabled={saving || noWritableDisk || diskId <= 0}
              onClick={() => setBrowseOpen(true)}
            >
              浏览…
            </Button>
          </div>
        </div>

        <div className="mt-3 flex items-center gap-2 text-[12.5px] text-muted">
          <Switch
            checked={noWritableDisk ? false : create}
            onChange={setCreate}
            label="目录不存在时自动创建"
            disabled={saving || noWritableDisk}
          />
          <span>目录不存在时自动创建（仅限该磁盘内）</span>
        </div>

        {error && (
          <div className="mt-3 flex items-start gap-2 rounded-lg border border-danger-500/25 bg-danger-50 p-3 text-[12.5px] text-danger-700">
            <Icon name="error" size="sm" className="mt-px shrink-0" />
            <span className="break-words">{error}</span>
          </div>
        )}
      </Modal>

      {browseOpen && targetDisk && (
        <DirTreeModal
          diskId={targetDisk.disk_id}
          diskName={targetDisk.name}
          onSelect={(path) => {
            setRelPath(path);
            setBrowseOpen(false);
          }}
          onClose={() => setBrowseOpen(false)}
        />
      )}

      {confirmClear && (
        <ConfirmDialog
          title="清除默认目录"
          message="将清除你自己的离线下载默认目录配置，不会删除磁盘上的文件。清除后再次分析前需要重新配置。"
          confirmLabel="确认清除"
          danger
          loading={saving}
          onCancel={() => setConfirmClear(false)}
          onConfirm={() => void handleClear()}
        />
      )}
    </>
  );
}
