import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { PageHeader } from '../../components/PageHeader';
import { LoadingSpinner } from '../../components/LoadingSpinner';
import { configService, MEDIA_REPAIR_KEYS } from '../../services/config-service';

/**
 * 管理员 - 系统设置（v1.4.0 媒体修复开关）
 *
 * 提供服务端媒体修复配置（存服务端 config 表，三端共享）：
 *  - 媒体修复总开关（默认关闭，弱 CPU 设备建议保持关闭）
 *  - 重编码子开关（默认关闭，-c copy 失败时的极端兜底）
 *  - 修复并发数（1~8，留空 = 按服务器 CPU 核数自动推导）
 *
 * 开关即点即存；并发数输入后点「保存」提交。配置写入后立即生效、无需重启任何端。
 * 仅管理员可见（路由层 RequireAdmin 守卫）。
 */

/** 简易开关组件（无第三方依赖，tailwind 实现） */
function Toggle({
  checked,
  disabled,
  onChange,
  label,
}: {
  checked: boolean;
  disabled?: boolean;
  onChange: (next: boolean) => void;
  label: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors
        ${checked ? 'bg-indigo-500' : 'bg-gray-300'}
        ${disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}`}
    >
      <span
        className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform
          ${checked ? 'translate-x-6' : 'translate-x-1'}`}
      />
      <span className="sr-only">{label}</span>
    </button>
  );
}

export function AdminSystemPage() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  // 离线媒体修复配置状态（v1.4.2：无实时修复总开关）
  const [allowTranscode, setAllowTranscode] = useState(false);
  const [maxConcurrent, setMaxConcurrent] = useState(''); // 空 = 自动基线

  const load = async () => {
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
      setNotice('并发数需为 1~8 的整数，或留空使用自动基线');
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
              v1.4.2 起损坏媒体经「修复中心」手动离线修复（文件管理右键
              「修复损坏媒体」发起）。以下为修复队列配置：并发数 1~8 或留空
              按服务器 CPU 核数自动推导；重编码为无损修复失败时的降级手段。
              配置为服务端配置，三端共享，修改后立即生效。
            </p>

            <div className="divide-y divide-gray-100">
              {/* 重编码子开关 */}
              <div className="flex items-center justify-between py-3">
                <div>
                  <p className="text-sm font-medium text-gray-700">允许重编码降级</p>
                  <p className="text-xs text-gray-400">
                    默认关闭；-c copy 失败时的极端兜底（CPU 开销大，不限制分辨率）
                  </p>
                </div>
                <Toggle
                  checked={allowTranscode}
                  disabled={saving}
                  onChange={toggleTranscode}
                  label="允许重编码降级"
                />
              </div>

              {/* 并发数 */}
              <div className="flex items-center justify-between py-3">
                <div>
                  <p className="text-sm font-medium text-gray-700">修复并发数</p>
                  <p className="text-xs text-gray-400">1~8；留空按服务器 CPU 核数自动推导</p>
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
        </>
      )}
    </div>
  );
}
