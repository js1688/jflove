import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { PageHeader } from '../../components/PageHeader';
import { Button, Card, ErrorState, Icon, Input, Switch, toast } from '../../components/ui';
import {
  configService,
  MEDIA_REPAIR_KEYS,
  OFFLINE_DOWNLOAD_KEYS,
} from '../../services/config-service';

/**
 * 管理员 - 系统设置（媒体修复 + 离线下载全局策略）
 *
 * 提供服务端共享配置（存服务端 config 表，三端共享）：
 *  - 修复并发数（1~8，留空 = 按服务器 CPU 核数自动推导）
 *  - 离线下载总开关 / 深度分析 / 并发 / 体积 / 项数 / 图片数
 *
 * 配置写入后立即生效、无需重启任何端。仅管理员可见（路由层 RequireAdmin 守卫）。
 *
 * v1.5.0：本页自建的 Toggle 组件改为统一 `Switch`；手写提示条改为统一 Toast；
 * 卡片/输入框/按钮全部套设计令牌（原先 `bg-indigo-500`/`bg-gray-300`/`bg-white` 硬编码）。
 * v1.5.1：移除失效的重编码降级开关 —— 服务端自 v1.4.2 hotfix 起不再读取
 * `media_repair_allow_transcode`（契约见 media_repair_service.py:11-13），
 * 本页只剩「修复并发数」，与 Web 端保持一致。
 * v1.8.0 UAT：新增「离线下载」卡片 —— 默认开启，管理员可在此应急熔断。
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
      });
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

  /** 保存离线下载的 4 个数字项（逐项 PUT /config，任一项失败即停止并提示） */
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
        </div>
      )}
    </div>
  );
}
