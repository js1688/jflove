interface PathBreadcrumbProps {
  diskName: string;
  path: string;
  onNavigate: (path: string) => void;
  onBackToDisks: () => void;
}

/** 文件路径面包屑导航 */
export function PathBreadcrumb({ diskName, path, onNavigate, onBackToDisks }: PathBreadcrumbProps) {
  const segments = path.split('/').filter(Boolean);
  // 上级目录路径（去掉最后一段），供"返回上级"使用
  const parentPath = segments.length > 0
    ? '/' + segments.slice(0, -1).join('/')
    : '';

  return (
    <div className="sticky top-14 z-10 flex items-center gap-1 px-4 py-2 text-sm text-gray-500 overflow-x-auto whitespace-nowrap border-b border-gray-100 bg-white">
      {/* 返回上级目录箭头：非根目录时显示，对齐移动端 Flutter（面包屑左侧小返回箭头）与桌面端「返回上级」 */}
      {parentPath && (
        <button
          onClick={() => onNavigate(parentPath)}
          title="返回上级目录"
          aria-label="返回上级目录"
          className="shrink-0 text-indigo-600 hover:text-indigo-800"
        >
          ←
        </button>
      )}

      <button
        onClick={onBackToDisks}
        className="text-indigo-600 hover:text-indigo-800 font-medium"
      >
        {diskName}
      </button>

      {segments.length > 0 && <span className="text-gray-300">/</span>}

      {segments.map((seg, i) => {
        const fullPath = '/' + segments.slice(0, i + 1).join('/');
        const isLast = i === segments.length - 1;

        return (
          <span key={fullPath} className="flex items-center gap-1">
            {isLast ? (
              <span className="text-gray-800 font-medium">{seg}</span>
            ) : (
              <button
                onClick={() => onNavigate(fullPath)}
                className="text-indigo-600 hover:text-indigo-800"
              >
                {seg}
              </button>
            )}
            {!isLast && <span className="text-gray-300">/</span>}
          </span>
        );
      })}
    </div>
  );
}
