import { Icon, IconButton } from './ui';

interface PathBreadcrumbProps {
  diskName: string;
  path: string;
  onNavigate: (path: string) => void;
  onBackToDisks: () => void;
}

/**
 * 文件路径面包屑导航
 *
 * v1.5.0：`←` 字符换矢量图标（需求 AC-5）；配色走设计令牌，
 * 吸顶偏移改用令牌 `--header-h`（原先硬编码 `top-14`）。
 */
export function PathBreadcrumb({ diskName, path, onNavigate, onBackToDisks }: PathBreadcrumbProps) {
  const segments = path.split('/').filter(Boolean);
  // 上级目录路径（去掉最后一段），供"返回上级"使用
  const parentPath = segments.length > 0 ? '/' + segments.slice(0, -1).join('/') : '';

  return (
    <div
      className="sticky z-10 flex items-center gap-1 overflow-x-auto border-b border-line-subtle bg-surface px-4 py-1.5 text-[13px] whitespace-nowrap"
      style={{ top: 'var(--header-h)' }}
    >
      {/* 返回上级目录：非根目录时显示，对齐移动端 Flutter 与桌面端「返回上级」 */}
      {parentPath && (
        <IconButton
          icon="back"
          label="返回上级目录"
          size="sm"
          className="-ml-1"
          onClick={() => onNavigate(parentPath)}
        />
      )}

      <button
        type="button"
        onClick={onBackToDisks}
        className="rounded px-1 font-medium text-fg-brand transition-colors hover:bg-hover"
      >
        {diskName}
      </button>

      {segments.length > 0 && <Icon name="next" size="sm" className="shrink-0 text-subtle" />}

      {segments.map((seg, i) => {
        const fullPath = '/' + segments.slice(0, i + 1).join('/');
        const isLast = i === segments.length - 1;

        return (
          <span key={fullPath} className="flex items-center gap-1">
            {isLast ? (
              <span className="font-medium text-fg">{seg}</span>
            ) : (
              <button
                type="button"
                onClick={() => onNavigate(fullPath)}
                className="rounded px-1 text-fg-brand transition-colors hover:bg-hover"
              >
                {seg}
              </button>
            )}
            {!isLast && <Icon name="next" size="sm" className="shrink-0 text-subtle" />}
          </span>
        );
      })}
    </div>
  );
}
