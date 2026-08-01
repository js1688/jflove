import { RouterProvider } from 'react-router';
import { router } from './config/routes';

/** 根组件 */
export function App() {
  return <RouterProvider router={router} />;
}
