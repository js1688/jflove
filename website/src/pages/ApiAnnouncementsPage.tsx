import { meta, openapi } from '../content';
import { InfoBar, KVTable, PageHead, SectionTitle } from '../components/ui';

/**
 * API 公告页。
 *
 * **数据源说明（重要）**：项目里没有结构化的接口变更台账，
 * 发布记录里的接口变更以散文形式散落在「发布范围」章节。
 * 因此本页内容**取自** `文档记录/版本发布记录/v<当前版本>.md` 的公开结论，
 * 并且**只在有真实记录时才写**，不编造变更条目。
 *
 * 已知的可核对事实（v1.10.21）：
 *   - 无接口增删（OpenAPI 75 个操作与上一版一致）
 *   - 加密协议版本未变（X-Encrypted-Stream: v1）
 *   - 无 DDL 变更
 *   - 用户可见变化在 SSH 认证，不涉及 HTTP 接口
 */
export function ApiAnnouncementsPage() {
  return (
    <>
      <PageHead
        crumb="文档 / API 公告"
        title="API 公告"
        lede="接口层的不兼容变更与升级要求。本页只记录有实据的变更，不做预告式承诺。"
      />

      <section className="py-10">
        <div className="mx-auto max-w-[1140px] px-7">
          <InfoBar>
            当前接口版本：OpenAPI {openapi.openapi} · {openapi.total} 个操作 ·{' '}
            <span className="tnum">v{openapi.info.version}</span>
          </InfoBar>

          <div className="mt-8">
            <SectionTitle
              title={`v${meta.version}`}
              sub="最近一次发布的接口层结论"
            />
            <KVTable
              rows={[
                ['接口增删', '无。本次发布 OpenAPI 仍为 75 个操作 / 12 个分组。'],
                ['加密协议', '未变，仍为 X-Encrypted-Stream: v1（ChaCha20-Poly1305 信封）。'],
                ['数据库变更', '无 DDL。升级不需要执行任何迁移脚本。'],
                [
                  '客户端要求',
                  '三端可独立升级，无强制同步升级要求。',
                ],
                [
                  '用户可见变化',
                  '本次变化集中在 SSH 远程连接的认证流程（多步认证共用一条 ssh-userauth 会话），不涉及 HTTP 接口。',
                ],
              ]}
            />
            <p className="mt-2 text-[12px] text-ink-500">
              依据：`文档记录/版本发布记录/v{meta.version}.md` 的「发布范围」与「发布前检查」章节。
            </p>
          </div>

          <div className="mt-12">
            <SectionTitle
              title="历史上影响调用方的变更"
              sub="只列真正会让老代码调不通的那几条"
            />
            <div className="space-y-3">
              <ChangeItem
                title="文件合并协议改为强制异步"
                body="旧客户端同步等待合并结果的做法不再被支持。服务端与 App / 桌面 / Web 必须同批次升级，旧客户端会被明确提示升级，不会静默失败。"
                tag="不兼容"
              />
              <ChangeItem
                title="JWT 只从加密 body 的 token 字段读取"
                body="不再支持从 Authorization 请求头兜底读取令牌。若你的脚本依赖该请求头，需要改成把 token 放进加密信封。"
                tag="不兼容"
              />
              <ChangeItem
                title="明文白名单收敛为 3 条"
                body={`只有 /health、/api/v1/auth/key-exchange、/api/v1/auth/admin-exists 为明文，其余全部加密。当前白名单共 ${openapi.plainPaths.length} 条，任何新增都需要评审。`}
                tag="收紧"
              />
            </div>
          </div>

          <div className="mt-12">
            <SectionTitle title="如何自查你的集成是否受影响" />
            <ol className="list-decimal space-y-1.5 pl-5 text-[13.5px] text-ink-700">
              <li>
                检查是否所有请求都带了 <code className="rounded bg-canvas-soft px-1">X-Session-ID</code>，
                且 body 是加密信封。
              </li>
              <li>
                检查 JWT 是否放在信封内的 <code className="rounded bg-canvas-soft px-1">token</code> 字段，
                而不是请求头。
              </li>
              <li>
                检查大文件上传完成后是否<b>轮询合并进度</b>，而不是同步等待合并返回。
              </li>
              <li>
                下载/预览是否按 <b>64KB 分帧</b>逐帧解密，而不是当作一个整体密文。
              </li>
            </ol>
          </div>
        </div>
      </section>
    </>
  );
}

function ChangeItem({ title, body, tag }: { title: string; body: string; tag: string }) {
  return (
    <div className="rounded-[10px] border border-line p-4">
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        <b className="text-[13.8px] font-[620]">{title}</b>
        <span className="rounded bg-canvas-soft px-1.5 py-0.5 text-[11px] text-ink-500">{tag}</span>
      </div>
      <p className="text-[13px] leading-relaxed text-ink-700">{body}</p>
    </div>
  );
}
