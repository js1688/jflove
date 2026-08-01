import { PageHeader } from '../components/PageHeader';

/** 同步管理页（降级） */
export function SyncPage() {
  return (
    <div>
      <PageHeader title="同步管理" />
      <div className="p-4 space-y-4">
        {/* 功能说明 */}
        <div className="bg-amber-50 border border-amber-200 rounded-xl p-5">
          <div className="flex items-start gap-3">
            <span className="text-2xl">⚠️</span>
            <div>
              <h3 className="font-semibold text-amber-800 text-sm">浏览器端不支持本地文件同步</h3>
              <p className="text-sm text-amber-600 mt-1">
                由于浏览器安全沙箱限制，无法访问本地文件系统进行双向增量同步。
              </p>
            </div>
          </div>
        </div>

        {/* 引导 */}
        <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100">
          <h3 className="font-semibold text-gray-800 text-sm mb-3">推荐使用以下客户端进行文件同步</h3>
          <div className="space-y-3">
            <div className="flex items-center gap-3 p-3 bg-gray-50 rounded-lg">
              <span className="text-2xl">🖥️</span>
              <div>
                <div className="text-sm font-medium text-gray-700">桌面端</div>
                <div className="text-xs text-gray-400">支持 Windows / Linux / macOS，本地文件系统双向同步</div>
              </div>
            </div>
            <div className="flex items-center gap-3 p-3 bg-gray-50 rounded-lg">
              <span className="text-2xl">📱</span>
              <div>
                <div className="text-sm font-medium text-gray-700">移动端</div>
                <div className="text-xs text-gray-400">支持 Android，App 内部存储双向同步</div>
              </div>
            </div>
          </div>
        </div>

        {/* 同步原理说明 */}
        <div className="bg-white rounded-xl p-5 shadow-sm border border-gray-100">
          <h3 className="font-semibold text-gray-800 text-sm mb-2">同步规则</h3>
          <ul className="text-xs text-gray-500 space-y-1 list-disc list-inside">
            <li>本地有远端无 → 上传</li>
            <li>本地无远端有 → 下载</li>
            <li>两边都有 → 按修改时间取新者</li>
            <li>任何方向都不主动删除文件</li>
          </ul>
        </div>
      </div>
    </div>
  );
}
