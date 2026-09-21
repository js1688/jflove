import { useState } from 'react';
import { useNavigate, useLocation } from 'react-router';
import { PageHeader } from '../components/PageHeader';
import { Segmented } from '../components/ui';
import { TransferTasksView } from './TransferPage';
import { RepairCenterContent } from './RepairCenterPage';

type Tab = 'transfer' | 'repair';

/**
 * 传输 / 修复合并页（v1.5.0 信息架构调整）
 *
 * 需求 §2.5 / AC-18：底部导航由 6 项收敛为 5 项，**「修复中心」并入传输页**。
 * 这样做的理由：两者都是「异步任务的进度管理」，同族功能合并后底部导航不再拥挤
 * （390px 宽下 6 项会导致图标+文字挤压）。
 *
 * 兼容性：
 *   - `/repair` 路由仍然保留（深链与从文件管理页「查看修复进度」的跳转可用），
 *     会以「修复」标签激活的状态打开本页；
 *   - 修复中心的**全部功能**（列表/取消/验证播放/覆盖/删除产物/重试/删除记录、
 *     自动轮询、只读账号禁用操作）都通过 `RepairTasksView` 完整提供。
 */
export function TransferTabsPage() {
  const navigate = useNavigate();
  const location = useLocation();
  // 由路由决定初始标签：/repair 直接落在「修复」
  const [tab, setTab] = useState<Tab>(location.pathname.startsWith('/repair') ? 'repair' : 'transfer');

  const onChange = (next: Tab) => {
    setTab(next);
    // 保持 URL 与标签一致，便于刷新与分享
    const target = next === 'repair' ? '/repair' : '/transfer';
    if (location.pathname !== target) navigate(target, { replace: true });
  };

  return (
    <div>
      <PageHeader
        title={tab === 'repair' ? '修复中心' : '传输任务'}
        subtitle={tab === 'repair' ? '共享修复任务与产物管理' : '上传与下载进度'}
        actions={
          <Segmented
            value={tab}
            onChange={onChange}
            options={[
              { value: 'transfer', label: '传输', icon: 'transfer' },
              { value: 'repair', label: '修复', icon: 'repair' },
            ]}
          />
        }
      />
      {tab === 'repair' ? <RepairCenterContent /> : <TransferTasksView />}
    </div>
  );
}
