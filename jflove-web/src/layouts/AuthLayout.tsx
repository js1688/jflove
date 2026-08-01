import { Outlet } from 'react-router';

/** 登录页布局：居中卡片 */
export function AuthLayout() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-br from-indigo-50 via-white to-cyan-50 p-4">
      <div className="w-full max-w-[420px]">
        <Outlet />
      </div>
    </div>
  );
}
