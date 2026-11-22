import { meta, openapi } from '../content';
import { CodeBlock, KVTable, PageHead, SectionTitle } from '../components/ui';

/**
 * 安全说明页。
 *
 * 内容全部对应 `AGENTS.md §9 安全宪法` 与后端实现，**不写没有实现的东西**。
 * 特别地：明确列出「本项目不做什么」（不引入长期身份密钥 / 不做公钥固定），
 * 这是与用户协商过的决策，不是遗漏。
 */
export function SecurityPage() {
  return (
    <>
      <PageHead
        crumb="文档 / 安全说明"
        title="安全说明"
        lede="除 3 条明文白名单外，所有接口的请求体、成功响应、错误响应一律加密。下面是这套设计的边界与理由。"
      />

      <section className="py-10">
        <div className="mx-auto max-w-[1140px] px-7">
          <SectionTitle title="加密协议" sub="四层：密钥交换 → 派生 → 信封 → 文件流" />
          <div className="grid gap-4 lg:grid-cols-2">
            <KVTable
              rows={[
                ['密钥交换', 'X25519 ECDH 临时密钥（每次会话重新生成）'],
                ['密钥派生', 'HKDF-SHA256，盐 b"jflove-v1"，length=32，info 为空'],
                ['对称加密', 'ChaCha20-Poly1305，12 字节随机 nonce（绝不复用）'],
                ['文件流', '每 64KB 明文一帧，帧内嵌独立 nonce 与认证标签'],
                ['令牌', 'JWT(ES256)，只走加密 body 的 token 字段'],
              ]}
            />
            <div className="space-y-3">
              <CodeBlock
                label="会话密钥派生"
                code={`shared = X25519(client_priv, server_pub)
session_key = HKDF_SHA256(
    ikm=shared,
    salt=b"jflove-v1",
    info=b"",
    length=32)`}
              />
              <CodeBlock
                label="文件流帧格式（大端）"
                code={`[4B 帧长 L][12B nonce][N B 密文 + 16B tag]
L = 12 + N + 16 = 28 + N`}
              />
            </div>
          </div>

          <div className="mt-12">
            <SectionTitle
              title="明文白名单"
              sub={`只有 ${openapi.plainPaths.length} 条，任何新增都需要评审`}
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
              为什么是这三条：密钥交换发生在会话密钥建立之前，不可能加密；健康检查与
              「管理员是否存在」不含敏感信息，且客户端登录前必须先问。
            </p>
          </div>

          <div className="mt-12">
            <SectionTitle title="前向保密" sub="为什么每次会话都重新协商" />
            <div className="rounded-[10px] border border-line p-5">
              <p className="mb-3 text-[13.5px] text-ink-700">
                每次会话生成临时 X25519 密钥对，session_key 只存在服务端内存的字典里，
                <b>私钥用完即销毁</b>，不落库、不写日志。服务重启后所有客户端需要重新交换密钥 ——
                这是设计如此，不是故障。
              </p>
              <p className="text-[13.5px] text-ink-700">
                意义：即使将来某一刻的服务端密钥泄露，也<b>无法解密历史流量</b>（前向保密 / PFS）。
              </p>
            </div>
          </div>

          <div className="mt-12">
            <SectionTitle title="鉴权与权限" sub="三条硬规则" />
            <div className="space-y-3">
              <Rule
                n="01"
                title="JWT 只从加密 body 读取"
                body="不接受 Authorization 请求头兜底 —— 否则企业 MITM 代理能看到明文 Bearer token。这是刻意不支持，不是遗漏。"
              />
              <Rule
                n="02"
                title="带路径参数的接口必须做归属校验"
                body="URL 上允许数字 ID / UUID，但每条带路径参数的路由都要验证资源属于当前用户（或当前用户是管理员）。换 ID 不能绕过权限。"
              />
              <Rule
                n="03"
                title="响应头不带敏感元数据"
                body="不返回 Content-Disposition: filename=... 这类字段。客户端从已加密的请求里自己决定保存名。"
              />
            </div>
          </div>

          <div className="mt-12">
            <SectionTitle title="本项目不做什么" sub="明确边界，避免误解" />
            <KVTable
              rows={[
                [
                  '不做公钥固定（pinning）',
                  '不引入服务端长期身份密钥。当前威胁模型主要是被动 MITM（如公司 HTTPS 解密做 DLP），ECDH 已能挡住；主动 MITM 需要精确针对，性价比不高。',
                ],
                [
                  '不做明文文件传输',
                  '下载 / 预览一律走加密流，不使用 FileResponse 直返裸文件。',
                ],
                [
                  '不在日志里记录用户内容',
                  'filename / path 在日志中截断或哈希化；token / 密码 / session_key 永不记录。',
                ],
                [
                  '不把数据交给第三方云',
                  '服务端跑在你自己的机器上，磁盘映射你自己的目录。',
                ],
              ]}
            />
          </div>

          <div className="mt-12">
            <SectionTitle title="自己怎么确认连接是加密的" />
            <ol className="list-decimal space-y-1.5 pl-5 text-[13.5px] text-ink-700">
              <li>
                在客户端打开「安全状态」页，看「加密算法」是否显示{' '}
                <code className="rounded bg-canvas-soft px-1">X25519 ECDH + HKDF-SHA256</code>。
              </li>
              <li>
                看「前向保密」是否显示{' '}
                <code className="rounded bg-canvas-soft px-1">已启用（临时密钥、私钥用完即销毁）</code>。
              </li>
              <li>
                想看报文：除 <code className="rounded bg-canvas-soft px-1">/health</code>、
                <code className="rounded bg-canvas-soft px-1">key-exchange</code>、
                <code className="rounded bg-canvas-soft px-1">admin-exists</code> 外，
                其它接口的请求与响应体都应该是 <code className="rounded bg-canvas-soft px-1">{'{ nonce, ciphertext }'}</code>。
              </li>
              <li>
                当前协议版本：<b>X-Encrypted-Stream: v1</b>（随版本 <span className="tnum">v{meta.version}</span> 未变）。
              </li>
            </ol>
          </div>
        </div>
      </section>
    </>
  );
}

function Rule({ n, title, body }: { n: string; title: string; body: string }) {
  return (
    <div className="flex gap-3 rounded-[10px] border border-line p-4">
      <span className="tnum text-[12px] font-[620] text-brand-600">{n}</span>
      <div>
        <div className="mb-1 text-[13.8px] font-[620]">{title}</div>
        <p className="text-[12.8px] leading-relaxed text-ink-700">{body}</p>
      </div>
    </div>
  );
}
