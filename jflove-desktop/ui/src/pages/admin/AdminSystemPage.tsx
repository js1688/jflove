import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { PageHeader } from '../../components/PageHeader';
import { Button, Card, ErrorState, Icon, Input, Switch, toast } from '../../components/ui';
import { configService, MEDIA_REPAIR_KEYS } from '../../services/config-service';

/**
 * 管理员 - 系统设置（媒体修复配置）
 *
 * 提供服务端媒体修复配置（存服务端 config 表，三端共享）：
 *  - 重编码子开关（默认关闭，-c copy 失败时的极端兜底）
 *  - 修复并发数（1~8，留空 = 按服务器 CPU 核数自动推导）
 *
 * 开关即点即存；并发数输入后点「保存」提交。配置写入后立即生效、无需重启任何端。
 * 仅管理员可见（路由层 RequireAdmin 守卫）。
 *
 * v1.5.0：本页自建的 Toggle 组件改为统一 `Switch`；手写提示条改为统一 Toast；
 * 卡片/输入框/按钮全部套设计令牌（原先 `bg-indigo-500`/`bg-gray-300`/`bg-white` 硬编码）。
 */
export function AdminSystemPage() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // 离线媒体修复配置状态（v1.4.2：无实时修复总开关）
  const [allowTranscode, setAllowTranscode] = useState(false);
  const [maxConcurrent, setMaxConcurrent] = useState(''); // 空 = 自动基线

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const resp = await configService.getConfig();
      const c = resp.config || {};
      setAllowTranscode(c[MEDIA_REPAIR_KEYS.allowTranscode] === '1');
      setMaxConcurrent(c[MEDIA_REPAIR_KEYS.maxConcurrent] || '');
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

  const toggleTranscode = (next: boolean) => {
    setAllowTranscode(next);
    void save(MEDIA_REPAIR_KEYS.allowTranscode, next ? '1' : '0');
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
              并发数 1~8，或留空按服务器 CPU 核数自动推导；重编码为无损修复失败时的降级手段。
              配置存于服务端、三端共享，修改后立即生效。
            </p>

            <div className="flex flex-col divide-y divide-line-subtle">
              {/* 重编码子开关 */}
              <div className="flex items-center justify-between gap-4 py-3.5">
                <div className="min-w-0">
                  <p className="text-[13px] font-medium">允许重编码降级</p>
                  <p className="mt-0.5 text-[11.5px] text-subtle">
                    默认关闭；-c copy 失败时的极端兜底（CPU 开销大）
                  </p>
                </div>
                <Switch
                  checked={allowTranscode}
                  disabled={saving}
                  onChange={toggleTranscode}
                  label="允许重编码降级"
                />
              </div>

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
        </div>
      )}
    </div>
  );
}
