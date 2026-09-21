import { PageHeader } from '../components/PageHeader';
import { Card, Icon, type IconName } from '../components/ui';

interface PlaceholderPageProps {
  /** 页面名（与 Web 端菜单一致） */
  title: string;
  /** 交付节点编号，例如 N7 */
  node: string;
  /** 菜单图标 */
  icon: IconName;
  /** 是否仅管理员可见 */
  adminOnly?: boolean;
}

/**
 * 业务页面占位
 *
 * 桌面端 UI 按「一个菜单一个节点」逐个交付（见计划 §3 Phase 2）。
 * 在某个菜单的专属节点完成前，点进来会看到本页 —— 明确标注它属于哪个节点、
 * 以及当前已经具备什么能力，避免"看起来能用其实没接"。
 */
export function PlaceholderPage({ title, node, icon, adminOnly }: PlaceholderPageProps) {
  return (
    <div className="flex h-full flex-col">
      <PageHeader title={title} subtitle={`交付节点 ${node} · 尚未接入`} />
      <div className="flex-1 overflow-auto p-4">
        <Card className="max-w-[720px]">
          <div className="flex items-start gap-3">
            <span className="stat-ico stat-ico-sky shrink-0">
              <Icon name={icon} size="lg" />
            </span>
            <div className="min-w-0">
              <div className="text-[13.5px] font-semibold">
                本页属于节点 {node}，当前为占位
              </div>
              <p className="mt-1 text-[12.5px] leading-relaxed text-muted">
                桌面端 UI 按菜单逐个交付：每完成一个菜单就单独打包给你体验验收。
                该页面的内容会与 Web 端保持一致（同一套设计令牌与组件）。
              </p>
              {adminOnly && (
                <p className="mt-2 text-[11.5px] text-subtle">
                  该页仅管理员可见（路由层已做角色守卫）。
                </p>
              )}
              <div className="mt-3 flex flex-col gap-1.5 text-[11.5px] text-subtle">
                <div>已具备的能力：</div>
                <div>· 无边框/原生窗口外壳（P0-1 已验证）</div>
                <div>· JS ↔ Python 桥：数据经既有加密通道取回（P0-2 已验证）</div>
                <div>· 登录页 + 业务面板骨架（本次交付）</div>
              </div>
            </div>
          </div>
        </Card>
      </div>
    </div>
  );
}
