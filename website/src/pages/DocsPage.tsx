import { meta, openapi } from '../content';
import { EntryCard, InfoBar, PageHead, SectionTitle } from '../components/ui';

/**
 * 文档中心：轻量入口页（design-spec §5「以入口卡为主，不放长正文」）。
 *
 * 顶部固定一条**版本一致性横幅**，告诉读者这些文档是按哪个版本的真实界面写的。
 */
export function DocsPage() {
  return (
    <>
      <PageHead
        crumb="产品 / 文档中心"
        title="文档与指引"
        lede="安装、操作、接口、安全 —— 五份资料，各自一页。想直接上手的话，从「操作指引」进去最快。"
      />

      <section className="py-10">
        <div className="mx-auto max-w-[1140px] px-7">
          <InfoBar>
            <span className="tnum">所有指引均按当前版本 v{meta.version} 的真实界面编写，按钮文案与菜单顺序逐字对齐各客户端。</span>
            <span className="ml-2 text-[12px] opacity-80">
              （三端菜单有差异，见页底对照表）
            </span>
          </InfoBar>

          <div className="mt-7 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <EntryCard
              to="/install"
              title="安装指引"
              badge="4 类产物"
              desc="Windows 安装包与便携版、Fedora RPM、Android APK。含服务端需要开放的明文接口、各端怎么填服务器地址。"
              cta="按端查看"
              tags={['Windows', 'Fedora', 'Android', '服务端']}
            />
            <EntryCard
              to="/guide/web"
              title="操作指引"
              badge="3 端"
              desc="逐菜单、逐按钮的实操说明：文件传哪里、笔记怎么建、离线下载怎么勾、设置改哪一项。"
              cta="Web 端指引"
              tags={['桌面', 'Web', '移动']}
            />
            <EntryCard
              to="/api"
              title="API 文档"
              badge={`${openapi.total} 个接口`}
              desc={`按 ${openapi.groupCount} 个模块分组，逐个列出方法、路径与摘要，并标出哪些走加密信封、哪些在明文白名单里。`}
              cta="看接口清单"
              tags={openapi.groups.slice(0, 4).map((g) => g.tag)}
            />
            <EntryCard
              to="/api-announcements"
              title="API 公告"
              badge="1 条"
              desc="接口层的不兼容变更与升级要求。本版最重要的一条：文件合并协议为强制异步，服务端与三端必须同批次升级。"
              cta="看公告"
              tags={['合并协议', '强制异步', `v${meta.version}`]}
            />
            <EntryCard
              to="/security"
              title="安全说明"
              badge={`${openapi.plainPaths.length} 个明文接口`}
              desc="加密协议、密钥交换、鉴权方式与「不做什么」的边界。含明文白名单、文件流帧结构，以及本项目明确不引入长期密钥的原因。"
              cta="看安全说明"
              tags={['X25519', 'HKDF-SHA256', 'ChaCha20']}
            />
            <EntryCard
              to="/features"
              title="功能特性"
              badge="产品"
              desc="11 项能力逐条说清楚，每项标注三端支持度。适合先看「这产品能干什么」再决定装哪个端。"
              cta="看功能详情"
              tags={['三端矩阵', '能力清单']}
            />
          </div>

          {/* 三端菜单差异对照 */}
          <div className="mt-12">
            <SectionTitle
              title="三端菜单差异对照"
              sub="菜单项按端裁剪，不支持的入口不会出现 —— 找功能前先确认自己在哪一端"
            />
            <div className="overflow-x-auto rounded-[10px] border border-line">
              <table className="w-full text-[13px]">
                <thead className="bg-canvas-soft text-[12px] text-ink-500">
                  <tr>
                    <th className="px-4 py-2.5 text-left font-[560]">菜单</th>
                    <th className="px-4 py-2.5 text-center font-[560]">桌面</th>
                    <th className="px-4 py-2.5 text-center font-[560]">Web</th>
                    <th className="px-4 py-2.5 text-center font-[560]">移动</th>
                  </tr>
                </thead>
                <tbody>
                  {MENU_DIFF.map((row) => (
                    <tr key={row.name} className="border-t border-line">
                      <td className="px-4 py-2 text-ink-700">{row.name}</td>
                      {row.cells.map((c, i) => (
                        <td key={i} className="px-4 py-2 text-center">
                          {c === true ? (
                            <span className="text-brand-600">✓</span>
                          ) : c === false ? (
                            <span className="text-ink-300">—</span>
                          ) : (
                            <span className="text-[12px] text-ink-500">{c}</span>
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="mt-2 text-[12px] text-ink-500">
              依据：`website/docs/ui-facts.md` §4（从三端源码实测，逐条带 <code>文件:行</code>）
            </p>
          </div>
        </div>
      </section>
    </>
  );
}

/**
 * 三端菜单差异。
 *
 * 这份数据**不是**从代码自动抽取的（源码里没有统一的菜单清单表），
 * 而是由 `website/docs/ui-facts.md` 人工核对后固化，并由门禁⑦⑧保证
 * 「指引 → 清单 → 源码」三级一致。改这里必须同步改事实清单。
 */
const MENU_DIFF: { name: string; cells: (boolean | string)[] }[] = [
  { name: '文件管理 / 文件', cells: [true, true, '叫「文件」'] },
  { name: '笔记管理 / 笔记', cells: [true, true, '叫「笔记」'] },
  { name: '文本编辑器', cells: [true, false, false] },
  { name: '云盘挂载', cells: [true, false, false] },
  { name: '传输任务', cells: [true, true, true] },
  { name: '离线下载', cells: [true, true, true] },
  { name: '远程连接', cells: [true, false, false] },
  { name: '相册同步', cells: [false, false, true] },
  { name: '安全状态', cells: [true, true, '在设置内'] },
  { name: '设置', cells: [true, true, true] },
  { name: '管理面板 4 项（管理员）', cells: [true, true, true] },
];
