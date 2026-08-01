/** 加载指示器 */
export function LoadingSpinner({ text = '加载中…' }: { text?: string }) {
  return (
    <div className="flex flex-col items-center justify-center py-12 text-gray-400">
      <div className="animate-spin w-8 h-8 border-3 border-indigo-500 border-t-transparent rounded-full mb-3" />
      <span className="text-sm">{text}</span>
    </div>
  );
}
