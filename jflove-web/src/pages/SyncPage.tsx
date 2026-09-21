import { PageHeader } from '../components/PageHeader';
import { Card, Icon } from '../components/ui';

/**
 * 同步管理页（降级页）
 *
 * 浏览器端无法访问本地文件系统，因此本页只做说明与引导。
 * v1.5.0：emoji ⚠️/🖥️/📱 换矢量图标，卡片与配色走设计令牌（需求 AC-4/AC-5）。
 */
export function SyncPage() {
  return (
    <div>
      <PageHeader title="同步管理" subtitle="浏览器端能力说明" />
      <div className="flex flex-col gap-4 p-4">
        {/* 功能说明 */}
        <div
          className="rounded-xl border p-5"
          style={{
            backgroundColor: 'var(--warning-50)',
            borderColor: 'rgb(245 158 11 / 0.28)',
          }}
        >
          <div className="flex items-start gap-3">
            <Icon
              name="warning"
              size="xl"
              strokeWidth={1.6}
              className="mt-0.5 shrink-0"
              style={{ color: 'var(--warning-700)' }}
            />
            <div>
              <h3 className="text-[13.5px] font-semibold" style={{ color: 'var(--warning-700)' }}>
                浏览器端不支持本地文件同步
              </h3>
              <p className="mt-1 text-[12.5px] leading-relaxed" style={{ color: 'var(--warning-700)' }}>
                受浏览器安全沙箱限制，网页无法访问本地文件系统进行双向增量同步。
              </p>
            </div>
          </div>
        </div>

        {/* 引导 */}
        <Card>
          <h3 className="mb-3 text-[13.5px] font-semibold">推荐使用以下客户端进行文件同步</h3>
          <div className="flex flex-col gap-3">
            <div className="flex items-center gap-3 rounded-lg bg-sunken p-3">
              <span className="stat-ico stat-ico-sky">
                <Icon name="system" size="lg" />
              </span>
              <div>
                <div className="text-[13px] font-medium">桌面端</div>
                <div className="text-[11.5px] text-subtle">
                  支持 Windows / Linux / macOS，本地文件系统双向同步
                </div>
              </div>
            </div>
            <div className="flex items-center gap-3 rounded-lg bg-sunken p-3">
              <span className="stat-ico stat-ico-mint">
                <Icon name="transfer" size="lg" />
              </span>
              <div>
                <div className="text-[13px] font-medium">移动端</div>
                <div className="text-[11.5px] text-subtle">支持 Android，App 内部存储双向同步</div>
              </div>
            </div>
          </div>
        </Card>

        {/* 同步规则 */}
        <Card>
          <h3 className="mb-2 text-[13.5px] font-semibold">同步规则</h3>
          <ul className="flex list-inside list-disc flex-col gap-1 text-[12.5px] text-muted">
            <li>本地有、远端无 → 上传</li>
            <li>本地无、远端有 → 下载</li>
            <li>两边都有 → 按修改时间取新者</li>
            <li>任何方向都不主动删除文件</li>
          </ul>
        </Card>
      </div>
    </div>
  );
}
