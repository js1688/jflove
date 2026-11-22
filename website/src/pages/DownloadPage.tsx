import { meta, openapi, releaseAsset, releases } from '../content';
import { CodeBlock, KVTable, PageHead, SectionTitle } from '../components/ui';

/**
 * 下载与安装（合并页，用户反馈：下载和安装是两码事，应该合并成一条路）。
 *
 * 设计原则：**傻瓜式**。每个平台给一个能直接点的下载按钮 + 一步一步的安装说明，
 * 不再要求用户自己去 Releases 页面翻。
 *
 * 链接来源：GitHub Release 真实资产直链（`https://github.com/js1688/jflove/releases/download/v<版本>/<文件名>`），
 * 版本号来自 `content/meta.json`，不硬编码。
 */

const REPO = 'https://github.com/js1688/jflove';

export function DownloadPage() {
  const v = meta.version;

  // 产物名 / 体积 / 链接一律来自 content/releases.json（gen_releases.py 从 GitHub API 生成）。
  // 页面**不拼文件名、不写体积** —— 否则发版必漏改，下载按钮会指向不存在的文件。
  const winSetup = releaseAsset('windows-setup');
  const winPortable = releaseAsset('windows-portable');
  const rpm = releaseAsset('linux-rpm');
  const apk = releaseAsset('android-apk');

  return (
    <>
      <PageHead
        crumb="产品 / 下载与安装"
        title={`下载与安装 v${v}`}
        lede={`三端客户端点一下就能下；服务端用 Docker 一条命令起。每一节都写清楚了前置条件、参数作用与常见问题。${
          releases.published_at
            ? ` 本版发布于 ${releases.published_at.slice(0, 10)}。`
            : ''
        }`}
      />

      <section className="py-10">
        <div className="mx-auto max-w-[1140px] px-7">
          {/* ── 先给最快路径 ── */}
          <div className="mb-10 rounded-[10px] border border-brand-100 bg-brand-50 px-5 py-4">
            <b className="text-[14px] text-brand-700">第一次用？按这个顺序：</b>
            <ol className="mt-2 list-decimal space-y-1 pl-5 text-[13.5px] text-brand-700">
              <li>先在一台常开的机器（NAS / 服务器 / 家里的小主机）上装 <b>服务端</b>，见下面第 1 节</li>
              <li>再用下面第 2~4 节的<b>任意一个客户端</b>连上它</li>
              <li>首次打开会在客户端里引导你<b>创建管理员账号</b>，不需要命令行</li>
            </ol>
          </div>

          {/* ═══════════ 1. 服务端 ═══════════ */}
          <SectionTitle
            title="1. 服务端（必须先装）"
            sub="它是数据中心，客户端都连它。用 Docker 部署，所以必须先有 Docker。"
          />

          <div className="mb-6 rounded-[10px] border border-line p-5">
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <span className="rounded bg-canvas-soft px-2 py-0.5 text-[11px] text-ink-500">前置条件</span>
              <b className="text-[13.5px]">需要先安装 Docker</b>
            </div>
            <KVTable
              rows={[
                [
                  '为什么需要 Docker',
                  '服务端打包成镜像分发，不依赖你机器上的 Python 版本，升级/回滚都是换一个镜像标签的事。',
                ],
                [
                  '还没装 Docker？',
                  <span key="d">
                    官方文档：
                    <a className="text-brand-600 underline" href="https://docs.docker.com/engine/install/" target="_blank" rel="noreferrer">
                      docs.docker.com/engine/install
                    </a>
                    （Linux / Windows / macOS 都有）。装完执行 <code className="rounded bg-canvas-soft px-1">docker version</code> 能看到版本号即可。
                  </span>,
                ],
                [
                  '权限提示',
                  <span key="perm">
                    如果 docker 命令报 Permission denied，把当前用户加入 docker 组：
                    <code className="rounded bg-canvas-soft px-1">
                      sudo usermod -aG docker $USER
                    </code>
                    （需重新登录生效），或每次用 sudo。
                  </span>,
                ],
                ['磁盘要求', '至少 1 GB 空闲；实际占用取决于你管理多少文件（文件本身存在宿主机磁盘上）。'],
              ]}
            />
          </div>

          <div className="mb-4">
            <CodeBlock
              label="一行命令启动服务端"
              code={`docker run -d --name jflove-server \\
  -p 8989:8989 \\
  -v /你的/数据目录:/data \\
  -v /你的/存储目录:/storage \\
  --restart=always \\
  ghcr.io/js1688/jflove-server:${v}`}
            />
          </div>

          <div className="mb-6 rounded-[10px] border border-line p-5">
            <b className="mb-3 block text-[13.5px]">每个参数的作用（照着改）</b>
            <KVTable
              rows={[
                [
                  '-p 8989:8989',
                  '把容器内 8989 端口映射到宿主机。客户端连的就是这个端口。想换端口改冒号**左边**（如 -p 18989:8989）。',
                ],
                [
                  '-v /你的/数据目录:/data',
                  '数据库存这里。**必须挂载出来**，否则删容器 = 数据丢失。建议 /home/你的用户名/jflove/data。',
                ],
                [
                  '-v /你的/存储目录:/storage',
                  '你要管理的磁盘目录挂进容器。之后在客户端「磁盘管理」里选的路径，就是这个目录下的子目录。可以挂多个。',
                ],
                [
                  '--restart=always',
                  '机器重启后容器自动拉起，不用手动 start。',
                ],
                [
                  '--name jflove-server',
                  '容器名。Web 端镜像默认按这个名字找后端，**别改**（除非你改用完整地址连）。',
                ],
              ]}
            />
          </div>

          <div className="mb-8">
            <CodeBlock
              label="怎么确认起来了"
              code={`docker ps                                    # 看到 jflove-server 且 STATUS 是 Up
curl http://127.0.0.1:8989/health            # 返回 {"status":"ok"} 就成了`}
            />
          </div>

          {/* ═══════════ 2~4 客户端 ═══════════ */}
          <SectionTitle
            title="2. 客户端：点一下就能下"
            sub="三端任选，都是同一台服务端上的同一批数据。"
          />

          <div className="grid gap-4 sm:grid-cols-2">
            <DownloadCard
              os="Windows"
              primary
              file={winSetup?.name ?? '（Release 中未找到）'}
              url={winSetup?.url ?? releases.release_url}
              size={winSetup?.size_human ?? '—'}
              label="安装版（推荐）"
              steps={[
                '点击下方按钮下载 .exe',
                '双击运行，按向导装完（会创建开始菜单与卸载器）',
                '首次打开填服务端地址：http://<服务器IP>:8989',
                '按引导创建管理员账号',
              ]}
              note="如果 Windows 提示「已保护你的电脑」，点「更多信息」→「仍要运行」。这是未购买代码签名证书的正常提示。"
            />
            <DownloadCard
              os="Windows"
              file={winPortable?.name ?? '（Release 中未找到）'}
              url={winPortable?.url ?? releases.release_url}
              size={winPortable?.size_human ?? '—'}
              label="免安装版"
              steps={[
                '点击下载 zip',
                '解压到任意目录',
                '运行目录里的 JFLove 主程序',
                '不写注册表；删掉目录就等于卸载',
              ]}
              note="适合放 U 盘携带，或没有管理员权限的电脑。"
            />
            <DownloadCard
              os="Fedora / RHEL"
              file={rpm?.name ?? '（Release 中未找到）'}
              url={rpm?.url ?? releases.release_url}
              size={rpm?.size_human ?? '—'}
              label="RPM 安装包"
              steps={[
                '点击下载 .rpm',
                '终端执行：sudo dnf install ./jflove-desktop-*.x86_64.rpm',
                '从应用菜单启动，或命令行运行 jflove',
                '首次打开填服务端地址',
              ]}
              note="Debian/Ubuntu 用户可先 alien 转换，或直接下载 onedir 目录运行；也可先用 Web 端。"
            />
            <DownloadCard
              os="Android"
              file={apk?.name ?? '（Release 中未找到）'}
              url={apk?.url ?? releases.release_url}
              size={apk?.size_human ?? '—'}
              label="APK 安装包"
              steps={[
                '手机浏览器点下载（或电脑下载后传手机）',
                '允许「安装未知来源应用」',
                '安装后打开，填服务端地址：http://<服务器IP>:8989',
                '登录后可在「相册同步」里开启照片自动备份',
              ]}
              note="首版未上架应用商店。iOS / 鸿蒙暂未提供。"
            />
          </div>

          <p className="mt-3 text-[12.5px] text-ink-500">
            也可以到{' '}
            <a className="text-brand-600 underline" href={`${REPO}/releases`} target="_blank" rel="noreferrer">
              GitHub Releases
            </a>{' '}
            页面查看全部历史版本。
          </p>

          {/* ═══════════ 5. Web 端 ═══════════ */}
          <div className="mt-12">
            <SectionTitle title="3. Web 端（可选，浏览器直接用）" sub="不用装任何东西，起一个镜像就能开网页版" />
            <CodeBlock
              label="启动 Web 端"
              code={`docker run -d --name jflove-web \\
  -p 18080:80 \\
  --restart=always \\
  ghcr.io/js1688/jflove-web:${v}`}
            />
            <div className="mt-3">
              <KVTable
                rows={[
                  ['访问地址', 'http://<服务器IP>:18080'],
                  ['为什么不用填服务端地址', '前端默认走同源 /api，镜像内的 nginx 已反代到 jflove-server:8989。前提是后端容器名保持 jflove-server。'],
                  ['前后端分开部署怎么办', '在 Web 端「设置 → 服务端 → 修改服务器地址」里填完整地址（含 http:// 和端口）。'],
                  ['启动顺序', '两者可任意顺序启动，nginx 用变量解析上游，后端没起也能启动。'],
                ]}
              />
            </div>
          </div>

          {/* ═══════════ 6. 明文白名单 ═══════════ */}
          <div className="mt-12">
            <SectionTitle
              title="4. 如果你在服务端前面加了反向代理"
              sub="这三条明文接口必须放行，否则客户端连不上"
            />
            <div className="grid gap-2 sm:grid-cols-3">
              {openapi.plainPaths.map((p) => (
                <div key={p} className="rounded-[10px] border border-line bg-canvas-soft px-4 py-2.5 text-[12.5px]">
                  <code>{p}</code>
                </div>
              ))}
            </div>
            <p className="mt-2 text-[12.5px] text-ink-500">
              其余接口全部是加密信封，反代按普通 HTTP 转发即可，**不需要也不应该**解析响应体。
            </p>
          </div>

          {/* ═══════════ 7. 一键更新脚本 ═══════════ */}
          <div className="mt-12">
            <SectionTitle
              title="5. 以后怎么升级"
              sub="不用重复上面那些命令，用一键更新脚本"
            />
            <div className="mb-4 rounded-[10px] border border-line p-5">
              <p className="mb-3 text-[13.5px] text-ink-700">
                把下面这个脚本存成 <code className="rounded bg-canvas-soft px-1">update-jflove.sh</code>，
                放到数据目录旁边，以后升级一条命令：<code className="rounded bg-canvas-soft px-1">bash update-jflove.sh</code>
              </p>
              <p className="mb-3 text-[13px] text-ink-500">
                它会：问你要升到哪个版本 → 先拉镜像（拉不到就不动线上）→ 可选更新数据库（先下载校验再替换，并自动备份旧库）
                → 停旧容器、起新容器 → 打印回滚命令。
              </p>
              <a
                className="inline-flex items-center gap-1.5 rounded-[7px] bg-brand-500 px-4 py-2 text-[13.5px] font-[520] text-white transition-colors hover:bg-brand-600"
                href={`${REPO}/blob/main/website/docs/update-jflove.sh`}
                target="_blank"
                rel="noreferrer"
              >
                查看/复制脚本
              </a>
            </div>
            <div className="rounded-[10px] border border-line p-5">
              <b className="mb-2 block text-[13.5px]">脚本里的关键变量（按你的环境改）</b>
              <KVTable
                rows={[
                  ['SERVER_PORT / WEB_PORT', '宿主机端口，默认 8989 / 18080'],
                  ['DATA_DIR', '数据库所在宿主机目录 → 容器 /data'],
                  ['STORAGE_DIR', '磁盘目录 → 容器 /storage（通常是 /mnt）'],
                  ['DB_URL_BASE', '数据库下载地址前缀，脚本会自动拼 v<版本号>'],
                ]}
              />
            </div>
            <div className="mt-4">
              <CodeBlock
                label="手动升级（不想用脚本时）"
                code={`# 1) 拉新镜像
docker pull ghcr.io/js1688/jflove-server:${v}
docker pull ghcr.io/js1688/jflove-web:${v}

# 2) 删旧容器（数据在宿主机挂载目录里，不会丢）
docker rm -f jflove-server jflove-web

# 3) 用新镜像按本文第 1、3 节的命令重新 run

# 4) 清理旧镜像
docker image prune -f`}
              />
            </div>
          </div>

          {/* ═══════════ 8. 常见问题 ═══════════ */}
          <div className="mt-12">
            <SectionTitle title="6. 装不上？先看这几条" />
            <div className="space-y-3">
              {FAQ.map((item) => (
                <details key={item.q} className="rounded-[10px] border border-line">
                  <summary className="cursor-pointer list-none px-4 py-3 text-[13.5px] font-[560] hover:bg-canvas-soft">
                    {item.q}
                  </summary>
                  <div className="border-t border-line bg-canvas-soft px-4 py-3 text-[13px] leading-relaxed text-ink-700">
                    {item.a}
                  </div>
                </details>
              ))}
            </div>
          </div>
        </div>
      </section>
    </>
  );
}

function DownloadCard({
  os,
  file,
  url,
  size,
  label,
  steps,
  note,
  primary,
}: {
  os: string;
  file: string;
  url: string;
  size: string;
  label: string;
  steps: string[];
  note?: string;
  primary?: boolean;
}) {
  return (
    <div className={`rounded-[10px] border p-5 ${primary ? 'border-brand-500' : 'border-line'}`}>
      <div className="mb-1.5 flex items-center justify-between gap-2">
        <b className="text-[14.5px] font-[620]">{os}</b>
        <span className="tnum text-[12px] text-ink-500">{size}</span>
      </div>
      <div className="mb-3 text-[12px] text-ink-500">{label}</div>

      <a
        href={url}
        className="mb-3 inline-flex w-full items-center justify-center gap-1.5 rounded-[7px] bg-brand-500 px-4 py-2.5 text-[13.5px] font-[540] text-white transition-colors hover:bg-brand-600"
      >
        立即下载
        <span aria-hidden>↓</span>
      </a>

      <code className="mb-3 block break-all rounded bg-canvas-soft px-2 py-1 text-[11px] text-ink-500">
        {file}
      </code>

      <ol className="list-decimal space-y-1 pl-4 text-[12.8px] text-ink-700">
        {steps.map((s) => (
          <li key={s}>{s}</li>
        ))}
      </ol>

      {note ? <p className="mt-2.5 text-[12px] text-ink-500">{note}</p> : null}
    </div>
  );
}

const FAQ: { q: string; a: string }[] = [
  {
    q: '客户端提示连不上服务端',
    a: '依次检查：① 服务端容器是否 Up（docker ps）；② 端口 8989 是否对外开放（云服务器要在安全组放行）；③ 客户端填的地址是否带 http:// 与端口号；④ 在服务器本机执行 curl http://127.0.0.1:8989/health 是否返回 {"status":"ok"}。',
  },
  {
    q: 'docker 命令报 Permission denied',
    a: '当前用户不在 docker 组。执行 sudo usermod -aG docker $USER 然后重新登录；或临时用 sudo docker。',
  },
  {
    q: '数据会不会因为删容器丢失',
    a: '只要 -v 的宿主机目录没删就不会丢。数据库在 <数据目录>/jflove-prod.db，建议定期备份这个文件。',
  },
  {
    q: '想换服务端端口',
    a: '改 -p 的宿主机侧，例如 -p 18989:8989，然后客户端地址写 http://<IP>:18989。容器内始终是 8989。',
  },
  {
    q: '从旧版本升级会丢数据吗',
    a: '不会。升级只是换镜像，数据在宿主机挂载目录里。如果该版本有表结构变更，用一键更新脚本可以选择同时替换数据库文件，脚本会自动备份旧库。',
  },
  {
    q: '没有公网 IP 能用吗',
    a: '能。服务端和客户端在同一局域网即可。要在外网用需要自己做内网穿透或端口映射，注意改强密码。',
  },
];
