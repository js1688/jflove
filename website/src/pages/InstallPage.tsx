import { meta, openapi } from '../content';
import { CodeBlock, KVTable, PageHead, SectionTitle } from '../components/ui';

/**
 * 安装指引页：服务端 + 三端客户端。
 *
 * 关键事实（来自代码，不是猜的）：
 *   - 服务端端口 8989，数据目录 /data，磁盘映射 /storage
 *   - 明文白名单 3 条（客户端登录前必须先能访问它们）
 *   - Web 端默认同源（DEFAULT_SERVER_URL=''），由 nginx 反代 /api
 */
export function InstallPage() {
  const v = meta.version;
  return (
    <>
      <PageHead
        crumb="文档 / 安装指引"
        title="安装指引"
        lede="先起服务端，再按需装客户端。服务端与 Web 端是 Docker 镜像，桌面端与移动端是安装包。"
      />

      <section className="py-10">
        <div className="mx-auto max-w-[1140px] px-7">
          {/* ── 服务端 ── */}
          <SectionTitle title="1. 服务端" sub={`Docker 镜像 ghcr.io/js1688/jflove-server:${v}`} />
          <div className="grid gap-4 lg:grid-cols-2">
            <div>
              <CodeBlock
                label="启动（端口 8989）"
                code={`docker run -d --name jflove-server \\
  -p 8989:8989 \\
  -v ~/jflove/data:/data \\
  -v /mnt:/storage \\
  --restart=always \\
  ghcr.io/js1688/jflove-server:${v}`}
              />
              <p className="mt-2 text-[12.5px] text-ink-500">
                <code>-v /data</code> 存数据库；<code>-v /storage</code> 映射你要管理的磁盘目录
                （容器内路径，宿主机路径按需替换）。
              </p>
            </div>
            <KVTable
              rows={[
                ['端口', '8989（容器内外一致）'],
                ['数据目录', '/data —— 数据库落在容器内，必须挂载出来'],
                ['磁盘目录', '/storage —— 你在「磁盘管理」里选的就是这里的子目录'],
                ['首次启动', '访问任意客户端，走「初始化管理员」流程创建第一个账号'],
              ]}
            />
          </div>

          {/* ── 明文白名单 ── */}
          <div className="mt-10">
            <SectionTitle
              title="客户端登录前必须能访问的三条明文接口"
              sub="如果你在服务端前面加了反向代理 / 防火墙，这三条必须放行，否则客户端连不上"
            />
            <div className="grid gap-2 sm:grid-cols-3">
              {openapi.plainPaths.map((p) => (
                <div
                  key={p}
                  className="rounded-[10px] border border-line bg-canvas-soft px-4 py-2.5 text-[12.5px]"
                >
                  <code>{p}</code>
                </div>
              ))}
            </div>
            <p className="mt-2 text-[12.5px] text-ink-500">
              其余接口全部是加密信封，反向代理只需按普通 HTTP 转发，**不需要也不应该**尝试解析响应体。
            </p>
          </div>

          {/* ── Web 端 ── */}
          <div className="mt-10">
            <SectionTitle title="2. Web 端" sub="浏览器直接打开，无需安装" />
            <div className="grid gap-4 lg:grid-cols-2">
              <CodeBlock
                label="启动（宿主机 18080 → 容器 80）"
                code={`docker run -d --name jflove-web \\
  -p 18080:80 \\
  --restart=always \\
  ghcr.io/js1688/jflove-web:${v}`}
              />
              <KVTable
                rows={[
                  ['默认同源', '前端默认走相对路径 /api，由镜像内 nginx 反代到 jflove-server:8989'],
                  ['服务名要求', '同 docker network 下，后端容器名需为 jflove-server；否则在设置里填完整地址'],
                  ['启动顺序', '两者可独立启动；nginx 用变量解析上游，后端没起也能启动'],
                  ['访问', 'http://<服务器IP>:18080'],
                ]}
              />
            </div>
          </div>

          {/* ── 桌面端 ── */}
          <div className="mt-10">
            <SectionTitle title="3. 桌面端" sub={`Windows / Fedora · v${v}`} />
            <div className="grid gap-4 md:grid-cols-3">
              <InstallCard
                title="Windows 安装版"
                file={`JFLove-${v}-win64-setup.exe`}
                steps={['双击运行安装包', '按向导装完（含开始菜单与卸载器）', '首次启动填服务端地址']}
              />
              <InstallCard
                title="Windows 免安装"
                file={`JFLove-${v}-win64-portable.zip`}
                steps={['解压到任意目录', '运行目录里的 JFLove 主程序', '不写注册表，删目录即卸载']}
              />
              <InstallCard
                title="Fedora / RHEL"
                file={`jflove-desktop-${v}-1.fc43.x86_64.rpm`}
                steps={[
                  'sudo dnf install ./jflove-desktop-*.x86_64.rpm',
                  '从应用菜单启动，或命令行运行 jflove',
                  '首次启动填服务端地址',
                ]}
              />
            </div>
            <p className="mt-2 text-[12.5px] text-ink-500">
              桌面端是 <b>onedir（目录形态）</b>打包：不提供单体 exe —— 单体形态每次启动都要把数百 MB
              运行时解压到临时目录，启动明显更慢。
            </p>
          </div>

          {/* ── 移动端 ── */}
          <div className="mt-10">
            <SectionTitle title="4. 移动端" sub="Android（iOS / 鸿蒙预留）" />
            <div className="grid gap-4 lg:grid-cols-2">
              <InstallCard
                title="Android APK"
                file="app-release.apk"
                steps={[
                  '下载 APK 并允许「安装未知来源应用」',
                  '安装后打开',
                  '填服务端地址（含 http:// 与端口 8989）后登录',
                ]}
              />
              <KVTable
                rows={[
                  ['系统要求', '支持 Android 的主流版本即可；首版未上架应用商店'],
                  ['相册备份', '默认关闭，需在「相册同步」里手动打开并指定远端目录'],
                  ['后台限制', '国产 ROM 需在系统设置里允许自启动，否则后台备份会被杀'],
                ]}
              />
            </div>
          </div>

          {/* ── 服务器地址怎么填 ── */}
          <div className="mt-10">
            <SectionTitle title="各端怎么填服务端地址" sub="最常见的第一次配置问题" />
            <div className="overflow-x-auto rounded-[10px] border border-line">
              <table className="w-full text-[13px]">
                <thead className="bg-canvas-soft text-[12px] text-ink-500">
                  <tr>
                    <th className="px-4 py-2.5 text-left font-[560]">端</th>
                    <th className="px-4 py-2.5 text-left font-[560]">填什么</th>
                  </tr>
                </thead>
                <tbody>
                  {[
                    ['桌面端', '设置 → 服务端 → 修改服务器地址，填 http://<服务器IP>:8989'],
                    ['Web 端', '默认留空即可（走当前站点同源）。只有前后端分域部署时才需要填完整地址'],
                    ['移动端', '设置 → 服务器 → 修改服务器地址，填 http://<服务器IP>:8989'],
                  ].map(([a, b]) => (
                    <tr key={a} className="border-t border-line">
                      <td className="px-4 py-2.5 font-[540]">{a}</td>
                      <td className="px-4 py-2.5 text-ink-700">{b}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </section>
    </>
  );
}

function InstallCard({
  title,
  file,
  steps,
}: {
  title: string;
  file: string;
  steps: string[];
}) {
  return (
    <div className="rounded-[10px] border border-line p-4">
      <div className="mb-1 text-[13.8px] font-[620]">{title}</div>
      <code className="mb-2.5 block break-all rounded bg-canvas-soft px-2 py-1 text-[11.5px] text-ink-700">
        {file}
      </code>
      <ol className="list-decimal space-y-1 pl-4 text-[12.8px] text-ink-700">
        {steps.map((s) => (
          <li key={s}>{s}</li>
        ))}
      </ol>
    </div>
  );
}
