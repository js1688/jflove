import { useNavigate } from 'react-router';
import { useAuthStore } from '../stores/auth-store';
import { PageHeader } from '../components/PageHeader';

/** 首页仪表盘 */
export function HomePage() {
  const navigate = useNavigate();
  const { username, role } = useAuthStore();

  const cards = [
    { title: '文件管理', icon: '📁', desc: '浏览和管理虚拟磁盘文件', path: '/files' },
    { title: '笔记管理', icon: '📝', desc: 'Markdown 笔记编辑与预览', path: '/notes' },
    { title: '同步管理', icon: '🔄', desc: '查看同步配置与状态', path: '/sync' },
    { title: '设置', icon: '⚙️', desc: '安全状态与偏好设置', path: '/settings' },
  ];

  return (
    <div>
      <PageHeader title="JFLove" />
      <div className="p-4">
        {/* 用户信息 */}
        <div className="bg-white rounded-xl p-4 mb-4 shadow-sm border border-gray-100">
          <div className="flex items-center gap-3">
            <span className="text-2xl">👤</span>
            <div>
              <div className="font-semibold text-gray-800">{username || '用户'}</div>
              <div className="text-xs text-gray-400">
                {role === 'admin' ? '管理员' : '普通用户'}
              </div>
            </div>
          </div>
        </div>

        {/* 快捷入口 */}
        <div className="grid grid-cols-2 gap-3">
          {cards.map(card => (
            <button
              key={card.path}
              onClick={() => navigate(card.path)}
              className="bg-white rounded-xl p-4 text-left shadow-sm border border-gray-100 hover:shadow-md hover:border-indigo-200 transition-all"
            >
              <span className="text-2xl">{card.icon}</span>
              <h3 className="font-medium text-gray-800 mt-2 text-sm">{card.title}</h3>
              <p className="text-xs text-gray-400 mt-1">{card.desc}</p>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
