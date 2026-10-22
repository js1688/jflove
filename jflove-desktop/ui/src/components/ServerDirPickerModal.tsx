/**
 * 服务器目录选择弹窗（v1.8.1 / D14 / AC-257）
 *
 * 用于「添加 / 编辑虚拟磁盘」时选择服务端真实路径。对应后端
 * `GET /api/v1/virtual-disks/server-dirs`（仅管理员、只列目录）。
 *
 * 渲染层无网络出口：请求统一经 `diskService.browseServerDirs` → 加密桥。
 */

import { useCallback, useEffect, useState } from 'react';
import type { ServerDirListing } from '../types/models';
import { diskService } from '../services/disk-service';
import { Button, Icon, Modal } from './ui';

interface ServerDirPickerModalProps {
  /** 打开时定位的绝对路径；留空由服务端从 roots 第一项开始 */
  initialPath?: string;
  /** 选中当前目录后回填路径 */
  onSelect: (path: string) => void;
  onClose: () => void;
}

/**
 * 服务器目录选择器：roots 快捷入口 + 上级 / 根目录 + 逐级进入 + 选中当前目录。
 */
export function ServerDirPickerModal({
  initialPath = '',
  onSelect,
  onClose,
}: ServerDirPickerModalProps) {
  const [listing, setListing] = useState<ServerDirListing | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  /** 待加载路径；`''` 表示由服务端决定起点 */
  const [targetPath, setTargetPath] = useState(initialPath);
  /** 错误态「重试」：同路径时靠它重新触发加载 */
  const [reloadKey, setReloadKey] = useState(0);

  const load = useCallback((path: string) => {
    let cancelled = false;
    setLoading(true);
    setError(null);

    diskService
      .browseServerDirs(path || undefined)
      .then((data) => {
        if (!cancelled) setListing(data);
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setListing(null);
          setError(`加载失败：${e instanceof Error ? e.message : String(e)}`);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => load(targetPath), [load, targetPath, reloadKey]);

  /** 重新加载当前路径（错误态「重试」） */
  const retry = () => setReloadKey((key) => key + 1);

  const currentPath = listing?.path ?? '';
  const rootPath = listing?.roots[0]?.path ?? '';
  const roots = listing?.roots ?? [];

  return (
    <Modal
      open
      title="选择服务器目录"
      icon="folder"
      width={640}
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            取消
          </Button>
          <Button
            variant="primary"
            icon="checked"
            disabled={!listing || loading}
            onClick={() => listing && onSelect(listing.path)}
          >
            选择此目录
          </Button>
        </>
      }
    >
      {/* 当前路径 + 导航 */}
      <div className="mb-2 flex items-center gap-2 rounded-md bg-sunken px-3 py-2 text-[13px]">
        <span className="shrink-0 text-subtle">路径</span>
        <span className="mono truncate text-[12px] font-medium">
          {currentPath || '（加载中…）'}
        </span>
        <div className="ml-auto flex shrink-0 gap-1">
          <Button
            size="sm"
            icon="back"
            disabled={loading || !listing?.parent}
            onClick={() => listing?.parent && setTargetPath(listing.parent)}
          >
            上级
          </Button>
          <Button
            size="sm"
            disabled={loading || !rootPath || rootPath === currentPath}
            onClick={() => setTargetPath(rootPath)}
          >
            根目录
          </Button>
        </div>
      </div>

      {/* roots 快捷入口 */}
      {roots.length > 0 && (
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          <span className="text-[12px] text-subtle">快速定位</span>
          {roots.map((root) => (
            <Button
              key={root.path}
              size="sm"
              variant={root.path === currentPath ? 'primary' : 'ghost'}
              title={root.path}
              onClick={() => setTargetPath(root.path)}
            >
              {root.name}
            </Button>
          ))}
        </div>
      )}

      {listing?.truncated && (
        <div className="mb-2 rounded-md bg-warning-50 px-3 py-2 text-[12.5px] text-warning-700">
          目录过多，仅显示前 2000 项，请进入更具体的路径。
        </div>
      )}

      {/* 目录列表 */}
      <div className="min-h-[220px] max-h-[52vh] overflow-y-auto rounded-md border border-line-subtle">
        {loading && (
          <div className="flex items-center justify-center gap-2 py-10 text-subtle">
            <Icon name="loading" className="animate-spin" />
            加载中…
          </div>
        )}

        {!loading && error && (
          <div className="px-4 py-6 text-center text-[13px] text-danger-700">
            {error}
            <div className="mt-3">
              <Button size="sm" onClick={retry}>
                重试
              </Button>
            </div>
          </div>
        )}

        {!loading && !error && listing && listing.dirs.length === 0 && (
          <div className="px-4 py-10 text-center text-[13px] text-subtle">
            此目录下没有子文件夹，可直接选择当前目录
          </div>
        )}

        {!loading &&
          !error &&
          listing?.dirs.map((dir) => (
            <button
              key={dir.path}
              type="button"
              onClick={() => setTargetPath(dir.path)}
              className="flex w-full items-center gap-3 border-b border-line-subtle px-4 py-2.5 text-left transition-colors last:border-b-0 hover:bg-hover"
            >
              <Icon name="folder" size="lg" className="text-warning-500" />
              <span className="truncate text-[13px]">{dir.name}</span>
              <span className="ml-auto text-[11.5px] text-subtle">点击进入</span>
            </button>
          ))}
      </div>

      <p className="mt-2 text-[12px] text-subtle">
        只显示目录，不读取文件内容；服务器路径也可在表单中手动输入作为兜底。
      </p>
    </Modal>
  );
}
