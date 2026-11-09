import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { PageHeader } from '../../components/PageHeader';
import { LoadingSpinner } from '../../components/LoadingSpinner';
import { Switch } from '../../components/ui';
import {
  configService,
  MEDIA_REPAIR_KEYS,
  OFFLINE_DOWNLOAD_KEYS,
  UPLOAD_CLEANUP_KEYS,
  UPLOAD_CLEANUP_RANGES,
  UPLOAD_CLEANUP_DEFAULTS,
  CLEANUP_CLIENT_TYPES,
  WEAK_SERVER_PRESET,
  parseCleanupClientTypes,
  type CleanupRunResult,
} from '../../services/config-service';

/**
 * 管理员 - 系统设置
 *
 * 提供服务端共享配置（存服务端 config 表，三端共享）：
 *  - 离线媒体修复并发数（1~8，留空 = 按服务器 CPU 核数自动推导）
 *  - 离线下载全局策略（总开关 / 深度分析 / 并发 / 体积 / 项数 / 图片数 / 超时）
 *  - **v1.9.0 存储与临时文件**（上传续传 TTL / 自动清理 / 客户端类型 / 孤儿保留期 /
 *    每用户会话上限 / 合并并发 / 上传分片并发 / 弱服务端模式 / 立即清理）
 *
 * 配置写入后立即生效、无需重启任何端。仅管理员可见（路由层 RequireAdmin 守卫）。
 */

/** 「存储与临时文件」数字项（范围与服务端 upload_cleanup_service 校验一致） */
const STORAGE_NUMBER_FIELDS = [
  {
    key: UPLOAD_CLEANUP_KEYS.resumeTtlHours,
    label: '断点续传保障时长（小时）',
    hint: `${UPLOAD_CLEANUP_RANGES.resumeTtlHours.min}~${UPLOAD_CLEANUP_RANGES.resumeTtlHours.max}`,
    ...UPLOAD_CLEANUP_RANGES.resumeTtlHours,
  },
  {
    key: UPLOAD_CLEANUP_KEYS.cleanupIntervalMinutes,
    label: '自动清理间隔（分钟）',
    hint: `${UPLOAD_CLEANUP_RANGES.cleanupIntervalMinutes.min}~${UPLOAD_CLEANUP_RANGES.cleanupIntervalMinutes.max}`,
    ...UPLOAD_CLEANUP_RANGES.cleanupIntervalMinutes,
  },
  {
    key: UPLOAD_CLEANUP_KEYS.orphanTtlHours,
    label: '孤儿临时文件保留期（小时）',
    hint: `${UPLOAD_CLEANUP_RANGES.orphanTtlHours.min}~${UPLOAD_CLEANUP_RANGES.orphanTtlHours.max}`,
    ...UPLOAD_CLEANUP_RANGES.orphanTtlHours,
  },
  {
    key: UPLOAD_CLEANUP_KEYS.maxSessionsPerUser,
    label: '每用户未完成会话上限',
    hint: `${UPLOAD_CLEANUP_RANGES.maxSessionsPerUser.min}~${UPLOAD_CLEANUP_RANGES.maxSessionsPerUser.max}`,
    ...UPLOAD_CLEANUP_RANGES.maxSessionsPerUser,
  },
  {
    key: UPLOAD_CLEANUP_KEYS.mergeConcurrency,
    label: '合并并发',
    hint: `${UPLOAD_CLEANUP_RANGES.mergeConcurrency.min}~${UPLOAD_CLEANUP_RANGES.mergeConcurrency.max}（0 = 仅排队）`,
    ...UPLOAD_CLEANUP_RANGES.mergeConcurrency,
  },
  {
    key: UPLOAD_CLEANUP_KEYS.chunkConcurrency,
    label: '上传分片并发',
    hint: `${UPLOAD_CLEANUP_RANGES.chunkConcurrency.min}~${UPLOAD_CLEANUP_RANGES.chunkConcurrency.max}`,
    ...UPLOAD_CLEANUP_RANGES.chunkConcurrency,
  },
] as const;

/** 客户端类型中文名（清理名单多选展示） */
const CLIENT_TYPE_LABELS: Record<string, string> = {
  app: 'App',
  desktop: '桌面端',
  web: 'Web 端',
  unknown: '未知',
};

/** 离线下载数字项：前端只做前置提示，最终以后端 400 中文错误为准 */
const OFFLINE_NUMBER_FIELDS = [
  {
    key: OFFLINE_DOWNLOAD_KEYS.maxConcurrent,
    label: '下载并发数',
    hint: '1~4',
    min: 1,
    max: 4,
  },
  {
    key: OFFLINE_DOWNLOAD_KEYS.maxFileSize,
    label: '单文件体积上限（MB）',
    hint: '1~102400',
    min: 1,
    max: 102400,
  },
  {
    key: OFFLINE_DOWNLOAD_KEYS.maxItems,
    label: '单次清单项数上限',
    hint: '1~200',
    min: 1,
    max: 200,
  },
  {
    key: OFFLINE_DOWNLOAD_KEYS.maxImages,
    label: '单页图片张数上限',
    hint: '1~1000',
    min: 1,
    max: 1000,
  },
  // v1.8.2（F37.6）：三个超时项，默认与范围同服务端
  // `offline_download_service.validate_config_value`（90/15/60，30~180 / 5~30 / 15~120）
  {
    key: OFFLINE_DOWNLOAD_KEYS.analyzeTimeout,
    label: '解析总超时（秒）',
    hint: '30~180',
    min: 30,
    max: 180,
  },
  {
    key: OFFLINE_DOWNLOAD_KEYS.connectTimeout,
    label: '连接超时（秒）',
    hint: '5~30',
    min: 5,
    max: 30,
  },
  {
    key: OFFLINE_DOWNLOAD_KEYS.readTimeout,
    label: '读取超时（秒）',
    hint: '15~120',
    min: 15,
    max: 120,
  },
] as const;

export function AdminSystemPage() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  // 离线媒体修复配置状态
  const [maxConcurrent, setMaxConcurrent] = useState(''); // 空 = 自动基线

  // 离线下载全局策略状态（后端未落库时由 GET /config 补默认值）
  const [offlineEnabled, setOfflineEnabled] = useState(true);
  const [deepAnalyze, setDeepAnalyze] = useState(true);
  const [offlineNumbers, setOfflineNumbers] = useState<Record<string, string>>({});

  // v1.9.0 存储与临时文件（上传续传 / 自动清理 / 资源隔离）
  const [cleanupEnabled, setCleanupEnabled] = useState(true);
  const [storageNumbers, setStorageNumbers] = useState<Record<string, string>>({});
  const [cleanupClientTypes, setCleanupClientTypes] = useState<string[]>([
    ...CLEANUP_CLIENT_TYPES,
  ]);
  const [cleanupRunning, setCleanupRunning] = useState(false);
  const [cleanupResult, setCleanupResult] = useState<CleanupRunResult | null>(null);

  const load = async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const resp = await configService.getConfig();
      const c = resp.config || {};
      setMaxConcurrent(c[MEDIA_REPAIR_KEYS.maxConcurrent] || '');
      setOfflineEnabled((c[OFFLINE_DOWNLOAD_KEYS.enabled] ?? '1') === '1');
      setDeepAnalyze((c[OFFLINE_DOWNLOAD_KEYS.deepAnalyze] ?? '1') === '1');
      setOfflineNumbers({
        [OFFLINE_DOWNLOAD_KEYS.maxConcurrent]:
          c[OFFLINE_DOWNLOAD_KEYS.maxConcurrent] ?? '2',
        [OFFLINE_DOWNLOAD_KEYS.maxFileSize]:
          c[OFFLINE_DOWNLOAD_KEYS.maxFileSize] ?? '4096',
        [OFFLINE_DOWNLOAD_KEYS.maxItems]:
          c[OFFLINE_DOWNLOAD_KEYS.maxItems] ?? '50',
        [OFFLINE_DOWNLOAD_KEYS.maxImages]:
          c[OFFLINE_DOWNLOAD_KEYS.maxImages] ?? '200',
        // v1.8.2 超时默认值（与后端 OFFLINE_DEFAULT_*_TIMEOUT_S 一致）
        [OFFLINE_DOWNLOAD_KEYS.analyzeTimeout]:
          c[OFFLINE_DOWNLOAD_KEYS.analyzeTimeout] ?? '90',
        [OFFLINE_DOWNLOAD_KEYS.connectTimeout]:
          c[OFFLINE_DOWNLOAD_KEYS.connectTimeout] ?? '15',
        [OFFLINE_DOWNLOAD_KEYS.readTimeout]:
          c[OFFLINE_DOWNLOAD_KEYS.readTimeout] ?? '60',
      });

      // v1.9.0 存储与临时文件：缺省项按服务端默认值展示
      setCleanupEnabled((c[UPLOAD_CLEANUP_KEYS.enabled] ?? '1') === '1');
      const storageDefaults: Record<string, string> = {};
      for (const field of STORAGE_NUMBER_FIELDS) {
        storageDefaults[field.key] =
          c[field.key] ?? UPLOAD_CLEANUP_DEFAULTS[field.key];
      }
      setStorageNumbers(storageDefaults);
      const savedTypes = parseCleanupClientTypes(c[UPLOAD_CLEANUP_KEYS.clientTypes]);
      // 空 = 全部（后端语义）；UI 上展示为四项全选，保存时显式写全名单
      setCleanupClientTypes(
        savedTypes.length > 0 ? savedTypes : [...CLEANUP_CLIENT_TYPES],
      );
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : '加载系统配置失败');
    }
    setLoading(false);
  };

  useEffect(() => { void load(); }, []);

  const save = async (key: string, value: string) => {
    setSaving(true);
    setNotice(null);
    try {
      await configService.updateConfig(key, value);
      setNotice('配置已保存，立即生效');
    } catch (e) {
      setNotice(e instanceof Error ? e.message : '保存失败');
    }
    setSaving(false);
  };

  /** 开关类配置：乐观更新，失败回滚并提示 */
  const saveToggle = async (
    key: string,
    next: boolean,
    setValue: (v: boolean) => void,
    label: string,
  ) => {
    setValue(next);
    setSaving(true);
    setNotice(null);
    try {
      await configService.updateConfig(key, next ? '1' : '0');
      setNotice(`${label}已${next ? '开启' : '关闭'}，立即生效`);
    } catch (e) {
      setValue(!next);
      setNotice(e instanceof Error ? e.message : '保存失败');
    }
    setSaving(false);
  };

  const saveConcurrent = () => {
    const trimmed = maxConcurrent.trim();
    if (trimmed === '') {
      void save(MEDIA_REPAIR_KEYS.maxConcurrent, '');
      return;
    }
    const n = Number(trimmed);
    if (Number.isInteger(n) && n >= 1 && n <= 8) {
      void save(MEDIA_REPAIR_KEYS.maxConcurrent, String(n));
    } else {
      setNotice('并发数需为 1~8 的整数，或留空使用自动基线');
    }
  };

  /** 保存离线下载的 7 个数字项（逐项 PUT /config，任一项失败即停止并提示） */
  const saveOfflineNumbers = async () => {
    const payload: Array<[string, string]> = [];
    for (const field of OFFLINE_NUMBER_FIELDS) {
      const raw = (offlineNumbers[field.key] ?? '').trim();
      const n = Number(raw);
      if (!Number.isInteger(n) || n < field.min || n > field.max) {
        setNotice(`${field.label}需为 ${field.min}~${field.max} 的整数`);
        return;
      }
      payload.push([field.key, String(n)]);
    }
    setSaving(true);
    setNotice(null);
    try {
      for (const [key, value] of payload) {
        await configService.updateConfig(key, value);
      }
      setNotice('离线下载配置已保存，立即生效');
    } catch (e) {
      setNotice(e instanceof Error ? e.message : '保存失败');
    }
    setSaving(false);
  };

  /** 勾选 / 取消「允许自动清理的客户端类型」 */
  const toggleClientType = (type: string, checked: boolean) => {
    setCleanupClientTypes((prev) => (
      checked
        ? [...new Set([...prev, type])]
        : prev.filter(item => item !== type)
    ));
  };

  /**
   * 保存「存储与临时文件」配置（数字项 + 客户端类型名单）。
   * 保存成功后立即重新 GET 一次，确保展示的是服务端真实值（三端同源、立即生效）。
   */
  const saveStorageConfig = async () => {
    const payload: Array<[string, string]> = [];
    for (const field of STORAGE_NUMBER_FIELDS) {
      const raw = (storageNumbers[field.key] ?? '').trim();
      const n = Number(raw);
      if (!Number.isInteger(n) || n < field.min || n > field.max) {
        setNotice(`${field.label}需为 ${field.min}~${field.max} 的整数`);
        return;
      }
      payload.push([field.key, String(n)]);
    }
    if (cleanupClientTypes.length === 0) {
      setNotice('请至少勾选一种允许自动清理的客户端类型（或关闭自动清理总开关）');
      return;
    }
    payload.push([UPLOAD_CLEANUP_KEYS.clientTypes, cleanupClientTypes.join(',')]);

    setSaving(true);
    setNotice(null);
    try {
      for (const [key, value] of payload) {
        await configService.updateConfig(key, value);
      }
      await load();
      setNotice('存储与临时文件配置已保存，立即生效');
    } catch (e) {
      setNotice(e instanceof Error ? e.message : '保存失败');
    } finally {
      setSaving(false);
    }
  };

  /** 弱服务端模式一键预设（F56.5：合并并发 1 / 上传并发 2 / 每用户会话上限 4） */
  const applyWeakServerPreset = async () => {
    setSaving(true);
    setNotice(null);
    try {
      for (const [key, value] of Object.entries(WEAK_SERVER_PRESET)) {
        await configService.updateConfig(key, value);
      }
      await load();
      setNotice(
        '已启用「弱服务端模式」：合并并发 1、上传并发 2、每用户未完成会话上限 4；'
        + '客户端分片固定 512KB。该模式降低峰值资源占用，但上传 / 合并总耗时会变长。',
      );
    } catch (e) {
      setNotice(e instanceof Error ? e.message : '启用弱服务端模式失败');
    } finally {
      setSaving(false);
    }
  };

  /** 立即清理（仅管理员；规则与定时任务一致，显示清理数量与跳过数量） */
  const handleRunCleanup = async () => {
    setCleanupRunning(true);
    setNotice(null);
    setCleanupResult(null);
    try {
      const result = await configService.runCleanup();
      setCleanupResult(result);
      setNotice('清理完成');
    } catch (e) {
      setNotice(e instanceof Error ? e.message : '清理失败');
    } finally {
      setCleanupRunning(false);
    }
  };

  if (loading) {
    return (
      <div className="p-6">
        <PageHeader title="系统设置" onBack={() => navigate(-1)} />
        <LoadingSpinner text="加载配置…" />
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto p-4 sm:p-6">
      <PageHeader title="系统设置" onBack={() => navigate(-1)} />

      {loadError && (
        <div className="my-4 rounded-lg bg-red-50 p-4 text-sm text-red-600">{loadError}</div>
      )}

      {!loadError && (
        <>
          {notice && (
            <div className="my-4 rounded-lg bg-green-50 p-3 text-sm text-green-700">{notice}</div>
          )}

          <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <h2 className="mb-1 text-base font-semibold text-gray-800">离线媒体修复</h2>
            <p className="mb-4 text-xs text-gray-500">
              损坏或无法在线播放的视频/音频，可在文件管理右键「修复损坏媒体」发起
              离线修复，修复为可边下边播的格式。下方配置修复队列的并发数。
              配置保存在服务端，三端共享，修改后立即生效。
            </p>

            <div className="divide-y divide-gray-100">
              {/* 并发数 */}
              <div className="flex items-center justify-between py-3">
                <div>
                  <p className="text-sm font-medium text-gray-700">修复并发数</p>
                  <p className="text-xs text-gray-400">
                    同时执行修复任务的上限（1~8）；留空则按服务器 CPU 核数自动推导
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    min={1}
                    max={8}
                    value={maxConcurrent}
                    disabled={saving}
                    onChange={(e) => setMaxConcurrent(e.target.value)}
                    placeholder="自动"
                    className="w-20 rounded-md border border-gray-300 px-2 py-1 text-sm"
                  />
                  <button
                    type="button"
                    disabled={saving}
                    onClick={saveConcurrent}
                    className="rounded-md bg-indigo-500 px-3 py-1 text-sm text-white
                      hover:bg-indigo-600 disabled:opacity-50"
                  >
                    保存
                  </button>
                </div>
              </div>
            </div>
          </div>

          <div className="mt-6 rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <h2 className="mb-1 text-base font-semibold text-gray-800">离线下载</h2>
            <p className="mb-4 text-xs text-gray-500">
              网址分析 / 媒体抓取的全局策略（含解析与出站超时），配置保存在服务端、三端共享，修改后立即生效。
              总开关默认开启；关闭后三端入口置灰且不产生任何出站请求（应急熔断）。
            </p>

            <div className="divide-y divide-gray-100">
              {/* 总开关 */}
              <div className="flex items-center justify-between gap-4 py-3">
                <div>
                  <p className="text-sm font-medium text-gray-700">离线下载总开关</p>
                  <p className="text-xs text-gray-400">
                    默认开启；关闭后三端入口置灰，不产生任何出站请求
                  </p>
                </div>
                <Switch
                  checked={offlineEnabled}
                  disabled={saving}
                  label="离线下载总开关"
                  onChange={(next) =>
                    void saveToggle(
                      OFFLINE_DOWNLOAD_KEYS.enabled,
                      next,
                      setOfflineEnabled,
                      '离线下载',
                    )
                  }
                />
              </div>

              {/* 深度分析 */}
              <div className="flex items-center justify-between gap-4 py-3">
                <div>
                  <p className="text-sm font-medium text-gray-700">深度分析（无头浏览器）</p>
                  <p className="text-xs text-gray-400">
                    仅作兜底；关闭后只能靠前两级解析，部分 JS 注入页面会分析失败
                  </p>
                </div>
                <Switch
                  checked={deepAnalyze}
                  disabled={saving}
                  label="深度分析"
                  onChange={(next) =>
                    void saveToggle(
                      OFFLINE_DOWNLOAD_KEYS.deepAnalyze,
                      next,
                      setDeepAnalyze,
                      '深度分析',
                    )
                  }
                />
              </div>

              {/* 数字项 */}
              {OFFLINE_NUMBER_FIELDS.map((field) => (
                <div
                  key={field.key}
                  className="flex items-center justify-between gap-4 py-3"
                >
                  <div>
                    <p className="text-sm font-medium text-gray-700">{field.label}</p>
                    <p className="text-xs text-gray-400">允许范围 {field.hint}</p>
                  </div>
                  <input
                    type="number"
                    min={field.min}
                    max={field.max}
                    aria-label={field.label}
                    value={offlineNumbers[field.key] ?? ''}
                    disabled={saving}
                    onChange={(e) =>
                      setOfflineNumbers((prev) => ({
                        ...prev,
                        [field.key]: e.target.value,
                      }))
                    }
                    className="w-28 rounded-md border border-gray-300 px-2 py-1 text-sm"
                  />
                </div>
              ))}

              <div className="flex justify-end py-3">
                <button
                  type="button"
                  disabled={saving}
                  onClick={() => void saveOfflineNumbers()}
                  className="rounded-md bg-indigo-500 px-3 py-1 text-sm text-white
                    hover:bg-indigo-600 disabled:opacity-50"
                >
                  保存数值配置
                </button>
              </div>
            </div>
          </div>

          {/* ── v1.9.0：存储与临时文件（上传断点续传 / 自动清理 / 弱服务端资源隔离） ── */}
          <div className="mt-6 rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
            <h2 className="mb-1 text-base font-semibold text-gray-800">存储与临时文件</h2>
            <p className="mb-4 text-xs text-gray-500">
              断点续传有效期、临时分片自动清理与合并资源上限。配置保存在服务端、三端共享，
              修改后立即生效。清理任务只会删除程序自产、固定命名的分片 / 合并残留，
              绝不删除用户正常文件。
            </p>

            <div className="divide-y divide-gray-100">
              {/* 自动清理总开关 */}
              <div className="flex items-center justify-between gap-4 py-3">
                <div>
                  <p className="text-sm font-medium text-gray-700">自动清理总开关</p>
                  <p className="text-xs text-gray-400">
                    关闭后不自动删除任何临时文件（立即清理按钮同样遵守此开关）
                  </p>
                </div>
                <Switch
                  checked={cleanupEnabled}
                  disabled={saving}
                  label="自动清理总开关"
                  onChange={(next) =>
                    void saveToggle(
                      UPLOAD_CLEANUP_KEYS.enabled,
                      next,
                      setCleanupEnabled,
                      '自动清理',
                    )
                  }
                />
              </div>

              {/* 数字项 */}
              {STORAGE_NUMBER_FIELDS.map((field) => (
                <div
                  key={field.key}
                  className="flex items-center justify-between gap-4 py-3"
                >
                  <div>
                    <p className="text-sm font-medium text-gray-700">{field.label}</p>
                    <p className="text-xs text-gray-400">
                      允许范围 {field.hint}
                      {field.key === UPLOAD_CLEANUP_KEYS.resumeTtlHours
                        && '；该值同时决定断点续传有效期和临时文件清理保护期'}
                    </p>
                  </div>
                  <input
                    type="number"
                    min={field.min}
                    max={field.max}
                    aria-label={field.label}
                    value={storageNumbers[field.key] ?? ''}
                    disabled={saving}
                    onChange={(e) =>
                      setStorageNumbers((prev) => ({
                        ...prev,
                        [field.key]: e.target.value,
                      }))
                    }
                    className="w-28 rounded-md border border-gray-300 px-2 py-1 text-sm"
                  />
                </div>
              ))}

              {/* 允许自动清理的客户端类型（多选） */}
              <div className="flex items-start justify-between gap-4 py-3">
                <div>
                  <p className="text-sm font-medium text-gray-700">
                    允许自动清理的客户端类型
                  </p>
                  <p className="text-xs text-gray-400">
                    只清理勾选类型的过期临时文件；名单外的类型永不自动清理
                  </p>
                </div>
                <div className="flex flex-wrap justify-end gap-x-3 gap-y-1">
                  {CLEANUP_CLIENT_TYPES.map((type) => (
                    <label
                      key={type}
                      className="flex items-center gap-1 text-xs text-gray-700"
                    >
                      <input
                        type="checkbox"
                        checked={cleanupClientTypes.includes(type)}
                        disabled={saving}
                        onChange={(e) => toggleClientType(type, e.target.checked)}
                      />
                      {CLIENT_TYPE_LABELS[type] ?? type}
                    </label>
                  ))}
                </div>
              </div>

              <div className="flex items-center justify-between gap-4 py-3">
                <div>
                  <p className="text-sm font-medium text-gray-700">弱服务端模式</p>
                  <p className="text-xs text-gray-400">
                    一键预设：合并并发 1、上传分片并发 2、每用户未完成会话上限 4
                    （分片 512KB 由客户端固定）。降低峰值资源占用，但上传 / 合并总耗时会变长；
                    适用于 8GB 内存 / N2840 级别的服务器。
                  </p>
                </div>
                <button
                  type="button"
                  disabled={saving}
                  onClick={() => void applyWeakServerPreset()}
                  className="shrink-0 rounded-md border border-gray-300 px-3 py-1 text-sm
                    text-gray-700 hover:bg-gray-50 disabled:opacity-50"
                >
                  启用弱服务端模式
                </button>
              </div>

              <div className="flex flex-wrap items-center justify-end gap-2 py-3">
                <button
                  type="button"
                  disabled={saving}
                  onClick={() => void saveStorageConfig()}
                  className="rounded-md bg-indigo-500 px-3 py-1 text-sm text-white
                    hover:bg-indigo-600 disabled:opacity-50"
                >
                  保存存储配置
                </button>
                <button
                  type="button"
                  disabled={cleanupRunning}
                  onClick={() => void handleRunCleanup()}
                  className="rounded-md border border-red-300 px-3 py-1 text-sm text-red-600
                    hover:bg-red-50 disabled:opacity-50"
                >
                  {cleanupRunning ? '清理中…' : '立即清理'}
                </button>
              </div>

              {cleanupResult && (
                <div className="py-3 text-xs text-gray-600">
                  最近清理（{new Date(cleanupResult.executed_at * 1000).toLocaleString('zh-CN')}）：
                  删除会话 {cleanupResult.removed_sessions} 个、目录 {cleanupResult.removed_dirs} 个、
                  合并残留 {cleanupResult.removed_parts} 个、跳过 {cleanupResult.skipped} 个（命名不符，安全跳过）
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
