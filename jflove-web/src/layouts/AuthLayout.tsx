import { Outlet } from 'react-router';

/**
 * 登录页布局：居中卡片
 *
 * v1.5.0：背景由 `from-indigo-50 via-white to-cyan-50` 渐变改为**品牌网格光晕**
 * （多层径向渐变，取令牌色），居中卡片宽度 420px 保持不变。
 */
export function AuthLayout() {
  return (
    <div
      className="flex min-h-screen items-center justify-center bg-canvas p-4"
      style={{
        backgroundImage:
          'radial-gradient(900px 500px at 12% -10%, rgb(99 102 241 / 0.16), transparent 60%),' +
          'radial-gradient(700px 420px at 100% 0%, rgb(168 85 247 / 0.13), transparent 55%),' +
          'radial-gradient(600px 380px at 50% 110%, rgb(14 165 233 / 0.08), transparent 60%)',
      }}
    >
      <div className="w-full max-w-[420px]">
        <Outlet />
      </div>
    </div>
  );
}
