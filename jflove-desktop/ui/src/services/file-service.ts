/**
 * 文件服务（桌面端）
 *
 * ## 与 Web 端 `jflove-web/src/services/file-service.ts` 的关键差异
 *
 * Web 端在**渲染层做加密与字节处理**：分片上传时在 JS 里算 SHA256
 * （`@noble/hashes`）、下载时用 `stream-frame` 逐帧解密、上传/下载都在 JS 里
 * 组装二进制。桌面端**不能这样**，原因有两条：
 *
 * 1. **加密链路只能有一份实现**（AGENTS.md §9.5/§9.6）：桌面端的请求加密、
 *    响应解密、流式帧解密全部在 `src/utils/http_client.py`。让 JS 再实现一遍，
 *    等于把密钥与算法暴露到渲染层（渲染层是"不可信区"，见安全宪法 §9.4）。
 * 2. **媒体预览必须走本地解码**（用户明确要求）：视频/音频由 `StreamProxy`
 *    解密后喂给 OS 解码器（`QMediaPlayer`），图片由 Qt 原生解码；
 *    渲染层只拿到一个本地 URL / 本地文件路径，**永远不碰文件字节**。
 *
 * 因此本文件是**薄封装**：
 *
 * | 能力 | 走哪条路 |
 * | --- | --- |
 * | 列磁盘 / 列目录 / 建目录 / 重命名 / 移动 / 删除 | `httpClient`（与 Web 同路径，Python 侧加密） |
 * | 上传 | 桥 `files.upload`（Python 分片 + 哈希 + 加密 + 进度事件） |
 * | 下载 | 桥 `files.download`（Python 解密 + 落盘；本地路径由原生对话框选） |
 * | 文本类预览 | 桥 `files.preview_text`（Python 解密后回文本） |
 * | 图片预览 | 桥 `files.write_temp` + `media.show_image`（Qt 原生解码） |
 * | 音视频预览 | 桥 `media.open_stream`（StreamProxy + QMediaPlayer） |
 *
 * 公开 API 与 Web 端保持同名同形，页面组件因此可以逐字复用。
 */
import { httpClient } from '../utils/http-client';
import { callBridge } from '../utils/desktop-bridge';
import type { VirtualDisk, FileItem } from '../types/models';

/** 桥返回的上传结果 */
export interface UploadResult {
  ok: boolean;
  name: string;
}

/** 桥返回的下载结果 */
export interface DownloadResult {
  ok: boolean;
  save_path: string;
  bytes: number;
}

/** 本地临时文件信息（图片预览用） */
export interface TempFileInfo {
  local_path: string;
  size: number;
  name: string;
}

/** 文本预览结果 */
export interface TextPreview {
  text: string;
  truncated: boolean;
  size: number;
}

export const fileService = {
  /** 获取用户可访问的虚拟磁盘列表 */
  async listDisks(): Promise<VirtualDisk[]> {
    const resp = await httpClient.get<{ disks: VirtualDisk[] }>('/api/v1/files/disks');
    return resp.disks;
  },

  /** 列出磁盘内文件/目录 */
  async listFiles(diskId: number, path: string): Promise<FileItem[]> {
    const resp = await httpClient.get<{ files: FileItem[] }>('/api/v1/files/list', {
      disk_id: diskId,
      path,
    });
    return resp.files;
  },

  /**
   * 创建目录。
   *
   * **与 Web 端的必要差异（这是个真 BUG，不是风格问题）**：
   * Web 端发的是 `{path: 当前目录, dir_name: 新目录名}`，但服务端 `/files/mkdir`
   * **只读 `path`**，语义是"**要创建的目录的完整相对路径**"（等同 `mkdir -p`）。
   * 于是服务端把"当前目录"又创建了一遍 —— 静默成功、接口 200，
   * 但新目录根本没出现（桌面端实测：`POST /files/mkdir` 无报错却毫无反应）。
   *
   * 这里按**原桌面端服务层**的契约（`file_service.make_dir(disk_id, rel_path)`）
   * 在客户端拼出完整相对路径，页面与 store 的调用签名保持不变。
   */
  async createDir(diskId: number, path: string, dirName: string): Promise<void> {
    const relPath = path ? `${path}/${dirName}` : dirName;
    await httpClient.post('/api/v1/files/mkdir', {
      disk_id: diskId,
      path: relPath,
    });
  },

  /** 重命名文件/目录 */
  async rename(diskId: number, path: string, newName: string): Promise<void> {
    await httpClient.post('/api/v1/files/rename', {
      disk_id: diskId,
      path,
      new_name: newName,
    });
  },

  /** 移动文件/目录 */
  async move(diskId: number, srcPath: string, dstDirPath: string): Promise<void> {
    await httpClient.post('/api/v1/files/move', {
      disk_id: diskId,
      src_path: srcPath,
      dst_dir_path: dstDirPath,
    });
  },

  /**
   * 删除文件/目录
   *
   * **BUG 修复（v1.5.0）**：服务端的删除路由是 `DELETE /api/v1/files`（`@router.delete("")`），
   * 原来请求的是 `/api/v1/files/delete` —— **该路由不存在，服务端返回 404**，
   * 于是"右键删除"看起来走完了流程（请求发出、无前端报错），文件却纹丝不动。
   * 移动端与桌面端服务层一直用的是正确路由，只有这里写错了。
   */
  /** 删除文件/目录 */
  async delete(diskId: number, path: string): Promise<void> {
    await httpClient.delete('/api/v1/files', { disk_id: diskId, path });
  },

  /**
   * 上传本地文件（**Python 侧完成分片 + SHA256 + 加密**）。
   *
   * 渲染层只提供本地绝对路径（由原生「打开文件」对话框取得）。
   * 方法名与 Web 端保持一致，页面组件可直接复用。
   */
  async uploadFile(diskId: number, path: string, localPath: string): Promise<UploadResult> {
    return callBridge<UploadResult>('files', 'upload', {
      disk_id: diskId,
      rel_path: path,
      local_path: localPath,
    });
  },

  /** 下载到本地路径（**Python 侧解密后落盘**） */
  async download(diskId: number, path: string, savePath: string): Promise<DownloadResult> {
    return callBridge<DownloadResult>('files', 'download', {
      disk_id: diskId,
      rel_path: path,
      save_path: savePath,
    });
  },

  /** 取文本类预览内容（Python 解密后回文本，渲染层不碰字节） */
  async previewText(diskId: number, path: string): Promise<TextPreview> {
    return callBridge<TextPreview>('files', 'preview_text', {
      disk_id: diskId,
      rel_path: path,
    });
  },

  /**
   * 把服务端文件解密后写到本地临时文件，返回本地路径。
   *
   * 图片预览用：把路径交给 `media.show_image`，由 **Qt 原生解码**显示
   * （可覆盖 Chromium 不支持、但 Qt 侧有插件的格式）。
   */
  async writeTemp(diskId: number, path: string, name: string): Promise<TempFileInfo> {
    return callBridge<TempFileInfo>('files', 'write_temp', {
      disk_id: diskId,
      rel_path: path,
      name,
    });
  },

  /** 选择本地文件（原生「打开」对话框，可多选） */
  async pickLocalFiles(): Promise<{ path: string; name: string; size: number }[]> {
    const resp = await callBridge<{
      files: { path: string; name: string; size: number }[];
    }>('dialogs', 'open_files', {});
    return resp.files;
  },

  /** 选择保存位置（原生「另存为」对话框） */
  async pickSavePath(defaultName: string): Promise<string> {
    const resp = await callBridge<{ path: string }>('dialogs', 'save_file', {
      default_name: defaultName,
    });
    return resp.path;
  },

  /** 选择本地目录（原生目录选择对话框） */
  async pickDirectory(title: string): Promise<string> {
    const resp = await callBridge<{ path: string }>('dialogs', 'select_directory', {
      title,
    });
    return resp.path;
  },

  /**
   * 桌面端**不提供** `downloadStream`。
   *
   * 下载/流式读取都在 Python 侧完成（`files.download` / `StreamProxy`），
   * 渲染层拿不到字节流。保留该方法只为在页面里显式失败，避免静默走错路。
   */
  async downloadStream(): Promise<never> {
    throw new Error('桌面端不支持在渲染层拉取字节流：请使用 fileService.download()');
  },
};
