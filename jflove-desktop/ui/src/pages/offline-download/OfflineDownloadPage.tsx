/**
 * 离线下载页（v1.8.0）
 *
 * 容器页：与「传输 / 修复」同款交互 —— 页头右侧用 `Segmented` 切换
 * 「任务中心」/「新建任务」，右上角进入账号级「设置」面板（需求 F30 / 设计 §3.1）。
 *
 * 职责边界：
 *   - 只负责加载 `/offline/meta`（页面可用性 + 可写磁盘 + 默认目录 + 轮询间隔）；
 *   - 任务列表与轮询在 `TaskList`，新建流程在 `NewTaskDialog`，目录设置在 `OfflineSettingsPanel`；
 *   - 所有请求经 `offline-download-service` → `httpClient` → Python 桥（渲染层无网络出口）。
 */

import { useCallback, useEffect, useState } from 'react';
import { PageHeader } from '../../components/PageHeader';
import { LoadingSpinner } from '../../components/LoadingSpinner';
import { Button, Icon, Segmented } from '../../components/ui';
import { offlineDownloadService, type OfflineMeta } from '../../services/offline-download-service';
import { NewTaskDialog } from './NewTaskDialog';
import { OfflineSettingsPanel } from './OfflineSettingsPanel';
import { TaskList } from './TaskList';
import { offlineErrorText } from './offline-utils';

type Tab = 'tasks' | 'new';

/** 轮询间隔兜底（设计 §3.2：默认 2500ms，以 `/meta.poll_interval_ms` 为准） */
const DEFAULT_POLL_INTERVAL_MS = 2500;

/** 页面是否处于「硬不可用」状态（总开关关闭 / 没有任何可写磁盘） */
function hardDisabledReason(meta: OfflineMeta): string {
  if (!meta.enabled) return '离线下载功能已关闭，请联系管理员开启。';
  if (meta.writable_disks.length === 0) {
    return '你没有可写入的磁盘，无法使用离线下载。请联系管理员为你的账号分配可写磁盘。';
  }
  return '';
}

export function OfflineDownloadPage() {
  const [tab, setTab] = useState<Tab>('tasks');
  const [meta, setMeta] = useState<OfflineMeta | null>(null);
  const [metaError, setMetaError] = useState<string | null>(null);
  const [loadingMeta, setLoadingMeta] = useState(true);
  const [settingsOpen, setSettingsOpen] = useState(false);
  /** 从任务中心「去选择」带入新建任务面板的 task_id */
  const [presetTaskId, setPresetTaskId] = useState<number | null>(null);

  const loadMeta = useCallback(async () => {
    try {
      const data = await offlineDownloadService.getMeta();
      setMeta(data);
      setMetaError(null);
    } catch (e) {
      setMetaError(offlineErrorText(e, '加载离线下载配置失败'));
    } finally {
      setLoadingMeta(false);
    }
  }, []);

  useEffect(() => {
    void loadMeta();
  }, [loadMeta]);

  const handleTabChange = (next: Tab) => {
    setTab(next);
    if (next === 'tasks') setPresetTaskId(null);
  };

  const handlePresetConsumed = useCallback(() => {
    setPresetTaskId(null);
  }, []);

  const handleSelectTask = useCallback((taskId: number) => {
    setPresetTaskId(taskId);
    setTab('new');
  }, []);

  const handleStarted = useCallback((_taskId: number) => {
    setPresetTaskId(null);
    setTab('tasks');
  }, []);

  const reason = meta ? hardDisabledReason(meta) : '';
  const pollIntervalMs =
    meta && Number(meta.poll_interval_ms) > 0
      ? Number(meta.poll_interval_ms)
      : DEFAULT_POLL_INTERVAL_MS;

  return (
    <div>
      <PageHeader
        title="离线下载"
        subtitle="服务端分析网页并下载视频 / 音频 / 图片到磁盘"
        actions={
          <>
            {meta && (
              <Button
                size="sm"
                icon="settings"
                onClick={() => setSettingsOpen(true)}
                title="离线下载设置（账号级默认目录）"
              >
                设置
              </Button>
            )}
            <Segmented
              value={tab}
              onChange={handleTabChange}
              options={[
                { value: 'tasks', label: '任务中心', icon: 'list' },
                { value: 'new', label: '新建任务', icon: 'plus' },
              ]}
            />
          </>
        }
      />

      {loadingMeta && !meta && <LoadingSpinner text="加载离线下载配置…" />}

      {!meta && !loadingMeta && (
        <div className="mx-auto max-w-4xl p-4 sm:p-6">
          <div className="flex flex-wrap items-start gap-2 rounded-lg border border-danger-500/25 bg-danger-50 p-3.5 text-[13px] text-danger-700">
            <Icon name="error" size="sm" className="mt-px shrink-0" />
            <span className="min-w-0 flex-1 break-words">
              {metaError || '加载离线下载配置失败'}
            </span>
            <Button size="sm" icon="refresh" onClick={() => void loadMeta()}>
              重试
            </Button>
          </div>
        </div>
      )}

      {meta && (
        <>
          {/* 硬不可用：整页提示 + 中文原因；任务列表仍可查看历史记录 */}
          {reason && (
            <div className="mx-auto max-w-4xl px-4 pt-4 sm:px-6">
              <div className="flex flex-wrap items-start gap-2 rounded-lg border border-warning-500/25 bg-warning-50 p-3.5 text-[13px] text-warning-700">
                <Icon name="warning" size="sm" className="mt-px shrink-0" />
                <span className="min-w-0 flex-1 break-words">{reason}</span>
                <Button size="sm" icon="settings" onClick={() => setSettingsOpen(true)}>
                  查看设置
                </Button>
              </div>
            </div>
          )}

          {/* 配置已失效（磁盘被删 / 权限被收回 / 目录不存在） */}
          {!reason && meta.config.disk_id > 0 && !meta.config.valid && (
            <div className="mx-auto max-w-4xl px-4 pt-4 sm:px-6">
              <div className="flex flex-wrap items-start gap-2 rounded-lg border border-warning-500/25 bg-warning-50 p-3.5 text-[13px] text-warning-700">
                <Icon name="warning" size="sm" className="mt-px shrink-0" />
                <span className="min-w-0 flex-1">
                  离线下载目录已失效，请重新配置；失效期间不能发起新的分析。
                </span>
                <Button size="sm" icon="settings" onClick={() => setSettingsOpen(true)}>
                  去设置
                </Button>
              </div>
            </div>
          )}

          {metaError && (
            <div className="mx-auto max-w-4xl px-4 pt-4 sm:px-6">
              <div className="flex flex-wrap items-start gap-2 rounded-lg border border-danger-500/25 bg-danger-50 p-3.5 text-[13px] text-danger-700">
                <Icon name="error" size="sm" className="mt-px shrink-0" />
                <span className="min-w-0 flex-1 break-words">{metaError}</span>
                <Button size="sm" icon="refresh" onClick={() => void loadMeta()}>
                  重试
                </Button>
              </div>
            </div>
          )}

          {tab === 'tasks' ? (
            <TaskList
              pollIntervalMs={pollIntervalMs}
              writableDisks={meta.writable_disks}
              onSelectTask={handleSelectTask}
            />
          ) : (
            <NewTaskDialog
              meta={meta}
              pollIntervalMs={pollIntervalMs}
              presetTaskId={presetTaskId}
              onPresetConsumed={handlePresetConsumed}
              onStarted={handleStarted}
              onOpenSettings={() => setSettingsOpen(true)}
            />
          )}

          <OfflineSettingsPanel
            meta={meta}
            open={settingsOpen}
            onClose={() => setSettingsOpen(false)}
            onSaved={loadMeta}
          />
        </>
      )}
    </div>
  );
}
