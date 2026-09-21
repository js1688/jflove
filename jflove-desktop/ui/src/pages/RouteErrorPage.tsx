/**
 * 路由错误兜底页
 *
 * react-router 在**没有 `errorElement`** 时会渲染它自带的开发错误页：
 *
 * ```
 * Unexpected Application Error!
 * 404 Not Found
 * 💿 Hey developer 👋 …
 * ```
 *
 * 那是给开发者看的，终端用户看了只会一头雾水。本组件作为兜底：
 * 给出中文说明 + 「返回」与「回到文件管理」两个出口。
 *
 * 触发场景举例（真实发生过）：点了一个**不支持在线预览**的文件，
 * 而预览路由当时还没接上 → 路由 404 → 用户看到上面那段英文。
 * 现在：预览页自己会给出「不支持预览此文件类型」的友好提示；
 * 即使将来又出现未接线的路由，也只会看到本页。
 */
import { useNavigate, useRouteError } from 'react-router';
import { Button, Icon } from '../components/ui';

export function RouteErrorPage() {
  const navigate = useNavigate();
  const error = useRouteError() as { status?: number; statusText?: string; message?: string } | null;

  const status = error?.status;
  const detail = error?.statusText || error?.message || '';

  return (
    <div className="grid h-full place-items-center bg-canvas px-6">
      <div className="flex max-w-[440px] flex-col items-center gap-3 text-center">
        <div className="grid h-14 w-14 place-items-center rounded-2xl bg-sunken text-subtle">
          <Icon name="close" size="lg" />
        </div>
        <div className="text-[15px] font-semibold text-fg">
          {status === 404 ? '页面不存在' : '页面出错了'}
        </div>
        <div className="text-[12.5px] leading-relaxed text-subtle">
          {status === 404
            ? '该页面可能尚未开放，或链接已失效。'
            : '页面加载过程中出现问题，可以返回重试。'}
          {detail ? <span className="mt-1 block text-[11.5px] opacity-70">{detail}</span> : null}
        </div>
        <div className="mt-1 flex items-center gap-2">
          <Button size="sm" onClick={() => navigate(-1)}>
            返回上一页
          </Button>
          <Button variant="primary" size="sm" onClick={() => navigate('/files')}>
            回到文件管理
          </Button>
        </div>
      </div>
    </div>
  );
}
