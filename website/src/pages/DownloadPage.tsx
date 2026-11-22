import { meta } from '../content';
import { PageHead, SectionTitle } from '../components/ui';

/**
 * 下载页。
 *
 * 产物体积来自 GitHub Release（v1.10.21）的真实资产；文件名按 Release 实际命名。
 * 若版本升级，这里的体积需要同步（门禁不校验体积，因为它是外部数据；
 * 但文件名里的版本号来自 `meta.version`，不会写错）。
 */
export function DownloadPage() {
  const v = meta.version;
  return (
    <>
      <PageHead
        crumb="产品 / 下载"
        title={`下载 v${v}`}
        lede="四个平台产物，全部来自同一份代码与同一套加密协议。服务端用 Docker 镜像，见安装指引。"
      />

      <section className="py-10">
        <div className="mx-auto max-w-[1140px] px-7">
          <SectionTitle
            title="客户端"
            sub={`体积为该版本 GitHub Release 上的真实大小 · 当前 v${v}`}
          />

          <div className="grid gap-4 sm:grid-cols-2">
            <Artifact
              os="Windows"
              name={`JFLove-${v}-win64-setup.exe`}
              size="177.5 MB"
              desc="安装版：双击安装，含开始菜单快捷方式与卸载器。推荐普通用户选这个。"
              primary
            />
            <Artifact
              os="Windows"
              name={`JFLove-${v}-win64-portable.zip`}
              size="268.2 MB"
              desc="免安装版：解压即用，不写注册表。适合放 U 盘或绿色使用。"
            />
            <Artifact
              os="Fedora / RHEL"
              name={`jflove-desktop-${v}-1.fc43.x86_64.rpm`}
              size="268.2 MB"
              desc="RPM 安装包。装完从应用菜单启动，或命令行运行 jflove。"
            />
            <Artifact
              os="Android"
              name="app-release.apk"
              size="66.9 MB"
              desc="Release 签名 APK。首版 Android-only，iOS 与鸿蒙预留。"
            />
          </div>

          <div className="mt-12">
            <SectionTitle title="服务端与 Web 端" sub="都是 Docker 镜像，不需要下载文件" />
            <div className="space-y-3">
              <Mirror
                name="服务端镜像"
                image={`ghcr.io/js1688/jflove-server:${v}`}
                note="端口 8989；/data 存数据库，/storage 映射你的磁盘目录"
              />
              <Mirror
                name="Web 端镜像"
                image={`ghcr.io/js1688/jflove-web:${v}`}
                note="端口 80（宿主机通常映射到 18080）；已内置 /api 反代到服务端"
              />
            </div>
            <p className="mt-3 text-[12.5px] text-ink-500">
              完整的启动命令、环境变量与端口说明见{' '}
              <a href="/install" className="text-brand-600">
                安装指引
              </a>
              。
            </p>
          </div>

          <div className="mt-12">
            <SectionTitle title="选哪个端？" />
            <div className="overflow-x-auto rounded-[10px] border border-line">
              <table className="w-full text-[13px]">
                <thead className="bg-canvas-soft text-[12px] text-ink-500">
                  <tr>
                    <th className="px-4 py-2.5 text-left font-[560]">你的情况</th>
                    <th className="px-4 py-2.5 text-left font-[560]">建议</th>
                  </tr>
                </thead>
                <tbody>
                  {[
                    ['要本地文本编辑器 / 云盘挂载 / SSH 终端', '桌面端（Windows 或 Linux）'],
                    ['只想在电脑上快速看文件、写笔记', 'Web 端（浏览器直接开）'],
                    ['要手机相册自动备份', '移动端（Android）'],
                    ['要自建服务，多端共享同一批数据', '先装服务端，再按需装客户端'],
                  ].map(([a, b]) => (
                    <tr key={a} className="border-t border-line">
                      <td className="px-4 py-2.5 text-ink-700">{a}</td>
                      <td className="px-4 py-2.5 font-[540]">{b}</td>
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

function Artifact({
  os,
  name,
  size,
  desc,
  primary,
}: {
  os: string;
  name: string;
  size: string;
  desc: string;
  primary?: boolean;
}) {
  return (
    <div
      className={`rounded-[10px] border p-5 ${
        primary ? 'border-brand-500' : 'border-line'
      }`}
    >
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <b className="text-[14px] font-[620]">{os}</b>
        <span className="tnum text-[12px] text-ink-500">{size}</span>
      </div>
      <code className="mb-2 block break-all rounded bg-canvas-soft px-2 py-1 text-[11.5px] text-ink-700">
        {name}
      </code>
      <p className="text-[12.8px] leading-relaxed text-ink-500">{desc}</p>
    </div>
  );
}

function Mirror({ name, image, note }: { name: string; image: string; note: string }) {
  return (
    <div className="rounded-[10px] border border-line p-4">
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <b className="text-[13.8px] font-[620]">{name}</b>
        <code className="rounded bg-canvas-soft px-1.5 py-0.5 text-[11.5px] text-ink-700">
          {image}
        </code>
      </div>
      <p className="text-[12.8px] text-ink-500">{note}</p>
    </div>
  );
}
