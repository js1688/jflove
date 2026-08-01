import { useEffect, useState } from 'react';
import { useSearchParams, useParams, useNavigate } from 'react-router';
import { fileService } from '../services/file-service';
import { PageHeader } from '../components/PageHeader';
import { LoadingSpinner } from '../components/LoadingSpinner';

/** 文件预览页 */
export function FilePreviewPage() {
  const { diskId } = useParams<{ diskId: string }>();
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();

  const path = searchParams.get('path') || '';
  const filename = searchParams.get('name') || '';

  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] = useState<{
    type: string;
    content?: string;
    base64?: string;
    content_type: string;
  } | null>(null);

  useEffect(() => {
    if (!diskId || !path) return;

    setLoading(true);
    fileService.getPreview(Number(diskId), path, filename)
      .then(setPreview)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [diskId, path, filename]);

  // 判断文件类型
  const ext = filename.split('.').pop()?.toLowerCase() || '';
  const isImage = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'svg'].includes(ext);
  const isVideo = ['mp4', 'webm', 'ogg', 'mkv'].includes(ext);
  const isAudio = ['mp3', 'wav', 'flac', 'aac', 'ogg'].includes(ext);
  const isPdf = ext === 'pdf';

  const renderPreview = () => {
    if (loading) return <LoadingSpinner text="加载预览…" />;
    if (error) return <div className="text-center py-12 text-red-500">{error}</div>;

    // Markdown 渲染（简化版，使用 marked）
    if (preview?.type === 'markdown' && preview.content) {
      return (
        <div
          className="markdown-body p-6 max-w-3xl mx-auto"
          dangerouslySetInnerHTML={{ __html: renderMarkdown(preview.content) }}
        />
      );
    }

    // 文本渲染
    if (preview?.type === 'text' && preview.content) {
      return (
        <pre className="p-6 text-sm font-mono whitespace-pre-wrap overflow-x-auto max-w-3xl mx-auto bg-gray-50 rounded-lg">
          {preview.content}
        </pre>
      );
    }

    // 图片渲染
    if ((isImage || preview?.type === 'image') && preview?.base64) {
      return (
        <div className="flex items-center justify-center p-4">
          <img
            src={`data:${preview.content_type};base64,${preview.base64}`}
            alt={filename}
            className="max-w-full max-h-[80vh] object-contain rounded-lg shadow-lg"
          />
        </div>
      );
    }

    // PDF
    if (isPdf) {
      return (
        <iframe
          src={`/api/v1/files/stream`}
          className="w-full h-[80vh] border-0"
          title={filename}
        />
      );
    }

    // 视频
    if (isVideo) {
      return (
        <div className="flex items-center justify-center p-4">
          <video controls className="max-w-full max-h-[80vh] rounded-lg shadow-lg">
            <source src="#" />
            您的浏览器不支持视频播放
          </video>
        </div>
      );
    }

    // 音频
    if (isAudio) {
      return (
        <div className="flex items-center justify-center p-8">
          <div className="bg-white rounded-xl shadow-lg p-8 max-w-md w-full text-center">
            <span className="text-5xl mb-4 block">🎵</span>
            <h3 className="font-medium text-gray-800 mb-4">{filename}</h3>
            <audio controls className="w-full">
              <source src="#" />
            </audio>
          </div>
        </div>
      );
    }

    // 不支持
    return (
      <div className="text-center py-12 text-gray-400">
        <span className="text-4xl mb-3 block">📎</span>
        <p>不支持预览此文件类型</p>
        <p className="text-xs mt-1">请下载后使用本地程序打开</p>
      </div>
    );
  };

  return (
    <div>
      <PageHeader
        title={filename || '预览'}
        onBack={() => navigate(-1)}
      />
      {renderPreview()}
    </div>
  );
}

/** 简单的 Markdown 渲染（使用 marked 库） */
function renderMarkdown(content: string): string {
  try {
    // 动态导入 marked（如果可用）
    // 简化处理：基本渲染
    return content
      .replace(/### (.+)/g, '<h3>$1</h3>')
      .replace(/## (.+)/g, '<h2>$1</h2>')
      .replace(/# (.+)/g, '<h1>$1</h1>')
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/\*(.+?)\*/g, '<em>$1</em>')
      .replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>')
      .replace(/`(.+?)`/g, '<code>$1</code>')
      .replace(/\n/g, '<br/>');
  } catch {
    return content;
  }
}
