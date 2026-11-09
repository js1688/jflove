import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { PageHeader } from '../../components/PageHeader';
import { Button, Card, ErrorState, Icon, Input, Switch, toast } from '../../components/ui';
import {
  configService,
  MEDIA_REPAIR_KEYS,
  OFFLINE_DOWNLOAD_KEYS,
  STORAGE_CLEANUP_DEFAULTS,
  STORAGE_CLEANUP_KEYS,
  STORAGE_CLIENT_TYPES,
  STORAGE_CLIENT_TYPE_LABELS,
  joinClientTypes,
  parseClientTypes,
  type CleanupResult,
  type StorageClientType,
} from '../../services/config-service';

/**
 * 管理员 - 系统设置（媒体修复 + 离线下载 + 存储与临时文件）
 *
 * 提供服务端共享配置（存服务端 config 表，三端共享）：
 *  - 修复并发数（1~8，留空 = 按服务器 CPU 核数自动推导）
 *  - 离线下载总开关 / 深度分析 / 并发 / 体积 / 项数 / 图片数
 *  - v1.9.0「存储与临时文件」：断点续传保障时长、自动清理间隔、客户端类型白名单、
 *    孤儿保留期、每用户会话上限、合并/分片并发、弱服务端模式预设、立即清理
 *
 * 配置写入后立即生效、无需重启任何端。仅管理员可见（路由层 RequireAdmin 守卫）。
 *
 * v1.5.0：本页自建的 Toggle 组件改为统一 `Switch`；手写提示条改为统一 Toast；
 * 卡片/输入框/按钮全部套设计令牌（原先 `bg-indigo-500`/`bg-gray-300`/`bg-white` 硬编码）。
 * v1.5.1：移除失效的重编码降级开关 —— 服务端自 v1.4.2 hotfix 起不再读取
 * `media_repair_allow_transcode`（契约见 media_repair_service.py:11-13），
 * 本页只剩「修复并发数」，与 Web 端保持一致。
 * v1.8.0 UAT：新增「离线下载」卡片 —— 默认开启，管理员可在此应急熔断。
 * v1.8.2（F37.6）：新增 3 个超时项（解析总超时 / 连接超时 / 读取超时），
 * 范围与默认值和服务端 `validate_config_value` 同源（30–180 / 5–30 / 15–120）。
 * v1.9.0（F51/F52/F56）：新增「存储与临时文件」卡片（TTL 单一来源、客户端类型多选、
 * 弱服务端模式一键预设、立即清理统计）；全部走 config-service 的加密 HTTP 封装。
 */

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
  // v1.8.2（F37.6）：超时三件套 —— 与后端 settings.py 的默认值/边界一致
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

/** v1.9.0 存储与临时文件数字项：前端只做前置提示，最终以后端 400 中文错误为准 */
const STORAGE_NUMBER_FIELDS = [
  {
    key: STORAGE_CLEANUP_KEYS.resumeTtlHours,
    label: '断点续传保障时长（小时）',
    hint: '1~168',
    min: 1,
    max: 168,
  },
  {
    key: STORAGE_CLEANUP_KEYS.cleanupIntervalMinutes,
    label: '自动清理间隔（分钟）',
    hint: '5~1440',
    min: 5,
    max: 1440,
  },
  {
    key: STORAGE_CLEANUP_KEYS.orphanTtlHours,
    label: '孤儿临时文件保留期（小时）',
    hint: '1~168',
    min: 1,
    max: 168,
  },
  {
    key: STORAGE_CLEANUP_KEYS.maxSessionsPerUser,
    label: '每用户未完成上传会话上限',
    hint: '1~64',
    min: 1,
    max: 64,
  },
  {
    key: STORAGE_CLEANUP_KEYS.mergeConcurrency,
    label: '合并并发数',
    hint: '0~4',
    min: 0,
    max: 4,
  },
  {
    key: STORAGE_CLEANUP_KEYS.chunkConcurrency,
    label: '上传分片并发数',
    hint: '1~8',
    min: 1,
    max: 8,
  },
] as const;

export function AdminSystemPage() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // 离线媒体修复配置状态（v1.4.2：无实时修复总开关；v1.5.1：无重编码降级子开关）
  const [maxConcurrent, setMaxConcurrent] = useState(''); // 空 = 自动基线

  // 离线下载全局策略状态（后端未落库时由 GET /config 补默认值）
  const [offlineEnabled, setOfflineEnabled] = useState(true);
  const [deepAnalyze, setDeepAnalyze] = useState(true);
  const [offlineNumbers, setOfflineNumbers] = useState<Record<string, string>>({});

  // v1.9.0「存储与临时文件」状态
  const [cleanupEnabled, setCleanupEnabled] = useState(true);
  const [storageNumbers, setStorageNumbers] = useState<Record<string, string>>({});
  const [cleanupClientTypes, setCleanupClientTypes] = useState<StorageClientType[]>([]);
  const [cleaning, setCleaning] = useState(false);
  const [cleanupResult, setCleanupResult] = useState<CleanupResult | null>(null);

  const load = useCallback(async () => {
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
        // v1.8.2（F37.6）：超时默认值与后端 OFFLINE_CONFIG_DEFAULTS 一致
        [OFFLINE_DOWNLOAD_KEYS.analyzeTimeout]:
          c[OFFLINE_DOWNLOAD_KEYS.analyzeTimeout] ?? '90',
        [OFFLINE_DOWNLOAD_KEYS.connectTimeout]:
          c[OFFLINE_DOWNLOAD_KEYS.connectTimeout] ?? '15',
        [OFFLINE_DOWNLOAD_KEYS.readTimeout]:
          c[OFFLINE_DOWNLOAD_KEYS.readTimeout] ?? '60',
      });
      // v1.9.0：存储与临时文件（总开关 / 数字项 / 客户端类型多选）
      setCleanupEnabled(
        (c[STORAGE_CLEANUP_KEYS.enabled] ??
          STORAGE_CLEANUP_DEFAULTS[STORAGE_CLEANUP_KEYS.enabled]) === '1',
      );
      const storageDefaults: Record<string, string> = {};
      for (const field of STORAGE_NUMBER_FIELDS) {
        storageDefaults[field.key] =
          c[field.key] ?? STORAGE_CLEANUP_DEFAULTS[field.key];
      }
      setStorageNumbers(storageDefaults);
      setCleanupClientTypes(
        parseClientTypes(
          c[STORAGE_CLEANUP_KEYS.cleanupClientTypes] ??
            STORAGE_CLEANUP_DEFAULTS[STORAGE_CLEANUP_KEYS.cleanupClientTypes],
        ),
      );
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : '加载系统配置失败');
    }
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async (key: string, value: string) => {
    setSaving(true);
    try {
      await configService.updateConfig(key, value);
      toast.success('配置已保存', '三端共享，立即生效');
    } catch (e) {
      toast.error('保存失败', e instanceof Error ? e.message : undefined);
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
    try {
      await configService.updateConfig(key, next ? '1' : '0');
      toast.success(`${label}已${next ? '开启' : '关闭'}`, '三端共享，立即生效');
    } catch (e) {
      setValue(!next);
      toast.error('保存失败', e instanceof Error ? e.message : undefined);
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
      toast.error('并发数不合法', '需为 1~8 的整数，或留空使用自动基线');
    }
  };

  /** 保存离线下载的数字项（逐项 PUT /config，任一项失败即停止并提示） */
  const saveOfflineNumbers = async () => {
    const payload: Array<[string, string]> = [];
    for (const field of OFFLINE_NUMBER_FIELDS) {
      const raw = (offlineNumbers[field.key] ?? '').trim();
      const n = Number(raw);
      if (!Number.isInteger(n) || n < field.min || n > field.max) {
        toast.error(
          '配置值不合法',
          `${field.label}需为 ${field.min}~${field.max} 的整数`,
        );
        return;
      }
      payload.push([field.key, String(n)]);
    }
    setSaving(true);
    try {
      for (const [key, value] of payload) {
        await configService.updateConfig(key, value);
      }
      toast.success('离线下载配置已保存', '三端共享，立即生效');
    } catch (e) {
      toast.error('保存失败', e instanceof Error ? e.message : undefined);
    }
    setSaving(false);
  };

  /** 保存存储配置（逐项 PUT，任一失败即停止；成功后重新拉取 = 保存立即刷新） */
  const saveStorageConfig = async () => {
    const payload: Array<[string, string]> = [];
    for (const field of STORAGE_NUMBER_FIELDS) {
      const raw = (storageNumbers[field.key] ?? '').trim();
      const n = Number(raw);
      if (!Number.isInteger(n) || n < field.min || n > field.max) {
        toast.error(
          '配置值不合法',
          `${field.label}需为 ${field.min}~${field.max} 的整数`,
        );
        return;
      }
      payload.push([field.key, String(n)]);
    }
    // 空数组 = 全部类型（后端约定：空集合不按客户端类型过滤）
    payload.push([
      STORAGE_CLEANUP_KEYS.cleanupClientTypes,
      joinClientTypes(cleanupClientTypes),
    ]);
    setSaving(true);
    try {
      for (const [key, value] of payload) {
        await configService.updateConfig(key, value);
      }
      await load();
      toast.success('存储与临时文件配置已保存', '三端共享，立即生效');
    } catch (e) {
      toast.error('保存失败', e instanceof Error ? e.message : undefined);
    }
    setSaving(false);
  };

  /** 弱服务端模式一键预设（F56.5）：合并并发 1 / 上传并发 2 / 每用户会话上限 4 */
  const applyWeakServerPreset = async () => {
    setSaving(true);
    try {
      await configService.updateConfig(STORAGE_CLEANUP_KEYS.mergeConcurrency, '1');
      await configService.updateConfig(STORAGE_CLEANUP_KEYS.chunkConcurrency, '2');
      await configService.updateConfig(
        STORAGE_CLEANUP_KEYS.maxSessionsPerUser, '4',
      );
      await load();
      toast.success(
        '已启用弱服务端模式',
        '合并并发 1 / 上传并发 2 / 每用户会话上限 4；桌面端分片固定 512KB',
      );
    } catch (e) {
      toast.error('启用弱服务端模式失败', e instanceof Error ? e.message : undefined);
    }
    setSaving(false);
  };

  /** 立即清理（F51.6）：与定时任务同规则的安全清理，结果展示数量 */
  const runCleanupNow = async () => {
    setCleaning(true);
    try {
      const result = await configService.cleanupNow();
      setCleanupResult(result);
      toast.success(
        '清理完成',
        `删除会话 ${result.removed_sessions} / 目录 ${result.removed_dirs} / ` +
          `残留 ${result.removed_parts}，跳过 ${result.skipped}`,
      );
    } catch (e) {
      toast.error('清理失败', e instanceof Error ? e.message : undefined);
    }
    setCleaning(false);
  };

  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader
        title="系统设置"
        subtitle="服务端配置（三端共享）"
        onBack={() => navigate(-1)}
      />

      {loading && (
        <div className="flex items-center justify-center gap-2 py-12 text-subtle">
          <Icon name="loading" className="animate-spin" />
          <span className="text-[13px]">加载配置…</span>
        </div>
      )}

      {!loading && loadError && (
        <ErrorState message={loadError} onRetry={() => void load()} />
      )}

      {!loading && !loadError && (
        <div className="p-4 sm:p-6">
          <Card>
            <div className="mb-1 flex items-center gap-2">
              <Icon name="repair" className="text-brand-500" />
              <h2 className="text-[15px] font-semibold">离线媒体修复</h2>
            </div>
            <p className="mb-4 text-[12.5px] leading-relaxed text-muted">
              损坏媒体经「修复中心」手动离线修复（在文件管理中右键文件选「修复损坏媒体」发起）。
              并发数 1~8，或留空按服务器 CPU 核数自动推导。
              配置存于服务端、三端共享，修改后立即生效。
            </p>

            <div className="flex flex-col divide-y divide-line-subtle">
              {/* 并发数 */}
              <div className="flex items-center justify-between gap-4 py-3.5">
                <div className="min-w-0">
                  <p className="text-[13px] font-medium">修复并发数</p>
                  <p className="mt-0.5 text-[11.5px] text-subtle">
                    1~8；留空按服务器 CPU 核数自动推导
                  </p>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Input
                    type="number"
                    min={1}
                    max={8}
                    value={maxConcurrent}
                    disabled={saving}
                    onChange={(e) => setMaxConcurrent(e.target.value)}
                    placeholder="自动"
                    className="!w-20"
                  />
                  <Button variant="primary" size="sm" icon="save" loading={saving} onClick={saveConcurrent}>
                    保存
                  </Button>
                </div>
              </div>
            </div>
          </Card>

          <Card className="mt-6">
            <div className="mb-1 flex items-center gap-2">
              <Icon name="download" className="text-brand-500" />
              <h2 className="text-[15px] font-semibold">离线下载</h2>
            </div>
            <p className="mb-4 text-[12.5px] leading-relaxed text-muted">
              网址分析 / 媒体抓取的全局策略，配置存于服务端、三端共享，修改后立即生效。
              总开关默认开启；关闭后三端入口置灰且不产生任何出站请求（应急熔断）。
              三个超时项限制「解析总预算 / 出站连接 / 出站读取」，站点响应慢时可适当放宽。
            </p>

            <div className="flex flex-col divide-y divide-line-subtle">
              {/* 总开关 */}
              <div className="flex items-center justify-between gap-4 py-3.5">
                <div className="min-w-0">
                  <p className="text-[13px] font-medium">离线下载总开关</p>
                  <p className="mt-0.5 text-[11.5px] text-subtle">
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
              <div className="flex items-center justify-between gap-4 py-3.5">
                <div className="min-w-0">
                  <p className="text-[13px] font-medium">深度分析（无头浏览器）</p>
                  <p className="mt-0.5 text-[11.5px] text-subtle">
                    仅作兜底；关闭后只能靠前两级解析
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
                  className="flex items-center justify-between gap-4 py-3.5"
                >
                  <div className="min-w-0">
                    <p className="text-[13px] font-medium">{field.label}</p>
                    <p className="mt-0.5 text-[11.5px] text-subtle">
                      允许范围 {field.hint}
                    </p>
                  </div>
                  <Input
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
                    className="!w-28"
                  />
                </div>
              ))}

              <div className="flex justify-end py-3.5">
                <Button
                  variant="primary"
                  size="sm"
                  icon="save"
                  loading={saving}
                  onClick={() => void saveOfflineNumbers()}
                >
                  保存数值配置
                </Button>
              </div>
            </div>
          </Card>

          {/* v1.9.0（F51/F52/F56）：存储与临时文件 */}
          <Card className="mt-6">
            <div className="mb-1 flex items-center gap-2">
              <Icon name="database" className="text-brand-500" />
              <h2 className="text-[15px] font-semibold">存储与临时文件</h2>
            </div>
            <p className="mb-2 text-[12.5px] leading-relaxed text-muted">
              服务端上传分片 / 合并临时文件的自动清理策略（配置存服务端、三端共享，
              修改后立即生效、无需重启）。清理只删程序自产、固定命名的分片与残留，
              不会删除用户正常文件。
            </p>
            <p className="mb-4 text-[11.5px] leading-relaxed text-fg-brand">
              「断点续传保障时长」同时决定断点续传有效期和临时文件清理保护期；
              清理阈值不可单独配置，避免双方不一致。
            </p>

            <div className="flex flex-col divide-y divide-line-subtle">
              {/* 自动清理总开关 */}
              <div className="flex items-center justify-between gap-4 py-3.5">
                <div className="min-w-0">
                  <p className="text-[13px] font-medium">自动清理总开关</p>
                  <p className="mt-0.5 text-[11.5px] text-subtle">
                    关闭后不自动删除任何临时文件（立即清理仍按同一规则执行）
                  </p>
                </div>
                <Switch
                  checked={cleanupEnabled}
                  disabled={saving || cleaning}
                  label="自动清理总开关"
                  onChange={(next) =>
                    void saveToggle(
                      STORAGE_CLEANUP_KEYS.enabled,
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
                  className="flex items-center justify-between gap-4 py-3.5"
                >
                  <div className="min-w-0">
                    <p className="text-[13px] font-medium">{field.label}</p>
                    <p className="mt-0.5 text-[11.5px] text-subtle">
                      允许范围 {field.hint}
                    </p>
                  </div>
                  <Input
                    type="number"
                    min={field.min}
                    max={field.max}
                    aria-label={field.label}
                    value={storageNumbers[field.key] ?? ''}
                    disabled={saving || cleaning}
                    onChange={(e) =>
                      setStorageNumbers((prev) => ({
                        ...prev,
                        [field.key]: e.target.value,
                      }))
                    }
                    className="!w-28"
                  />
                </div>
              ))}

              {/* 允许清理的客户端类型（空 = 全部） */}
              <div className="py-3.5">
                <p className="text-[13px] font-medium">允许自动清理的客户端类型</p>
                <p className="mb-2 mt-0.5 text-[11.5px] text-subtle">
                  只清理名单内客户端创建的上传会话；全部不勾选 = 所有类型
                </p>
                <div className="flex flex-wrap gap-x-4 gap-y-2">
                  {STORAGE_CLIENT_TYPES.map((type) => (
                    <label
                      key={type}
                      className="flex items-center gap-1.5 text-[12.5px] text-muted"
                    >
                      <input
                        type="checkbox"
                        aria-label={`允许清理客户端：${STORAGE_CLIENT_TYPE_LABELS[type]}`}
                        checked={cleanupClientTypes.includes(type)}
                        disabled={saving || cleaning}
                        onChange={(e) =>
                          setCleanupClientTypes((prev) =>
                            e.target.checked
                              ? prev.includes(type)
                                ? prev
                                : [...prev, type]
                              : prev.filter((item) => item !== type),
                          )
                        }
                      />
                      {STORAGE_CLIENT_TYPE_LABELS[type]}
                    </label>
                  ))}
                </div>
              </div>

              {/* 弱服务端模式一键预设 */}
              <div className="flex flex-wrap items-center justify-between gap-3 py-3.5">
                <div className="min-w-0">
                  <p className="text-[13px] font-medium">弱服务端模式</p>
                  <p className="mt-0.5 text-[11.5px] leading-relaxed text-subtle">
                    一键设为合并并发 1 / 上传并发 2 / 每用户会话上限 4；
                    降低峰值资源占用，但上传 / 合并总耗时会变长
                    （针对 8GB / N2840 级别服务器）。桌面端分片固定 512KB。
                  </p>
                </div>
                <Button
                  size="sm"
                  icon="zap"
                  loading={saving}
                  disabled={cleaning}
                  onClick={() => void applyWeakServerPreset()}
                >
                  启用弱服务端模式
                </Button>
              </div>

              {/* 立即清理 */}
              <div className="flex flex-wrap items-center justify-between gap-3 py-3.5">
                <div className="min-w-0">
                  <p className="text-[13px] font-medium">立即清理</p>
                  <p className="mt-0.5 text-[11.5px] leading-relaxed text-subtle">
                    触发一次与定时任务同规则的安全清理（跳过无法确认归属的条目），
                    并显示删除与跳过数量。
                  </p>
                  {cleanupResult && (
                    <p className="mt-1.5 text-[11.5px] text-fg-brand">
                      最近一次：删除会话 {cleanupResult.removed_sessions} 个 / 目录{' '}
                      {cleanupResult.removed_dirs} 个 / 合并残留{' '}
                      {cleanupResult.removed_parts} 个，跳过 {cleanupResult.skipped} 个
                    </p>
                  )}
                </div>
                <Button
                  size="sm"
                  icon="delete"
                  loading={cleaning}
                  disabled={saving}
                  onClick={() => void runCleanupNow()}
                >
                  立即清理
                </Button>
              </div>

              <div className="flex justify-end py-3.5">
                <Button
                  variant="primary"
                  size="sm"
                  icon="save"
                  loading={saving}
                  disabled={cleaning}
                  onClick={() => void saveStorageConfig()}
                >
                  保存存储配置
                </Button>
              </div>
            </div>
          </Card>
        </div>
      )}
    </div>
  );
}
