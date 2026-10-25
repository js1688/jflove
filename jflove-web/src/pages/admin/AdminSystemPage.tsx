import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { PageHeader } from '../../components/PageHeader';
import { LoadingSpinner } from '../../components/LoadingSpinner';
import { Switch } from '../../components/ui';
import {
  configService,
  MEDIA_REPAIR_KEYS,
  OFFLINE_DOWNLOAD_KEYS,
} from '../../services/config-service';

/**
 * 管理员 - 系统设置
 *
 * 提供服务端共享配置（存服务端 config 表，三端共享）：
 *  - 离线媒体修复并发数（1~8，留空 = 按服务器 CPU 核数自动推导）
 *  - 离线下载全局策略（总开关 / 深度分析 / 并发 / 体积 / 项数 / 图片数 / 超时）
 *
 * 配置写入后立即生效、无需重启任何端。仅管理员可见（路由层 RequireAdmin 守卫）。
 * v1.8.0 UAT 裁决：离线下载默认开启，管理员可在此应急关闭。
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
        </>
      )}
    </div>
  );
}
