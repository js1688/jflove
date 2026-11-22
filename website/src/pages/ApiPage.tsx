import { openapi } from '../content';
import { CodeBlock, InfoBar, KVTable, PageHead, SectionTitle } from '../components/ui';

/**
 * API 文档页：**从 OpenAPI 生成的数据渲染**（design-spec §7）。
 *
 * 关键约束：
 *   - 接口清单来自 `content/api-openapi.json`（`app.openapi()` 生成），**禁止手抄**
 *   - 调用流程内置于页面顶部，因为加密接口无法直接 Try it out
 *   - 不宣称提供 SDK / 在线调试 / 参数校验（这些都不存在）
 */
export function ApiPage() {
  return (
    <>
      <PageHead
        crumb="文档 / API 文档"
        title="接口文档"
        lede="所有业务接口走端到端加密信封。本页由后端 OpenAPI 直接生成，接口清单与分组不会手抄走样。"
      />

      <section className="py-10">
        <div className="mx-auto max-w-[1140px] px-7">
          <InfoBar>
            OpenAPI {openapi.openapi} · {openapi.total} 个操作 · {openapi.groupCount} 个分组 ·
            服务端版本 <span className="tnum">v{openapi.info.version}</span>
          </InfoBar>

          {/* ── 调用流程（本页核心）── */}
          <div className="mt-8">
            <SectionTitle
              title="怎么调通：五步"
              sub="加密接口不能直接在 Swagger 里点「Try it out」，必须按下面的顺序在客户端本地完成加密"
            />
            <div className="grid gap-4 lg:grid-cols-2">
              <div>
                <Step n={1} title="生成密钥对">
                  客户端本地生成 <b>X25519</b> 密钥对，公钥做 Base64 编码。
                </Step>
                <Step n={2} title="密钥交换（唯一明文 POST）">
                  调 <code className="rounded bg-canvas-soft px-1">POST /api/v1/auth/key-exchange</code>，
                  带上 <code className="rounded bg-canvas-soft px-1">client_public_key</code> 与
                  <code className="rounded bg-canvas-soft px-1">refresh: false</code>；
                  返回 <code className="rounded bg-canvas-soft px-1">session_id</code> 与{' '}
                  <code className="rounded bg-canvas-soft px-1">server_public_key</code>。
                </Step>
                <Step n={3} title="派生会话密钥">
                  <code className="rounded bg-canvas-soft px-1">X25519(client_priv, server_pub)</code> →{' '}
                  <code className="rounded bg-canvas-soft px-1">
                    HKDF-SHA256(salt=b&quot;jflove-v1&quot;, length=32)
                  </code>
                  。盐是固定字面量，info 为空。
                </Step>
                <Step n={4} title="之后每个请求都带信封">
                  Header 带 <code className="rounded bg-canvas-soft px-1">X-Session-ID</code>；
                  Body 是 <code className="rounded bg-canvas-soft px-1">{'{ nonce, ciphertext }'}</code>。
                  算法 ChaCha20-Poly1305，nonce 每次 12 字节随机、<b>绝不复用</b>。
                </Step>
                <Step n={5} title="登录拿 JWT" last>
                  信封内传 <code className="rounded bg-canvas-soft px-1">username</code> /{' '}
                  <code className="rounded bg-canvas-soft px-1">password</code>，返回{' '}
                  <code className="rounded bg-canvas-soft px-1">token</code>。
                  之后需要身份的接口把 token 放进<b>信封内的</b>{' '}
                  <code className="rounded bg-canvas-soft px-1">token</code> 字段 ——
                  服务端<b>不读</b> <code className="rounded bg-canvas-soft px-1">Authorization</code> 头。
                </Step>
              </div>

              <div className="space-y-3">
                <CodeBlock
                  label="① 密钥交换（明文，不带 X-Session-ID）"
                  code={`POST /api/v1/auth/key-exchange
Content-Type: application/json

{
  "client_public_key": "<Base64 的 X25519 公钥>",
  "refresh": false
}

→ 200 { "session_id": "...", "server_public_key": "..." }`}
                />
                <CodeBlock
                  label="② 之后每个请求：Header + 加密信封"
                  code={`POST /api/v1/auth/login
X-Session-ID: 550e8400-e29b-41d4-a716-446655440000
Content-Type: application/json

{
  "nonce": "<Base64, 12B 随机>",
  "ciphertext": "<Base64, 密文 + 16B 认证标签>"
}

← 成功响应同样是 { nonce, ciphertext }`}
                />
                <CodeBlock
                  label="③ 派生会话密钥（伪代码）"
                  code={`shared = X25519(client_private, server_public)
session_key = HKDF_SHA256(
    ikm=shared, salt=b"jflove-v1", info=b"", length=32)`}
                />
              </div>
            </div>
          </div>

          {/* ── 三个易踩细节 ── */}
          <div className="mt-12">
            <SectionTitle title="三个容易踩的细节" sub="都是实测踩过的，不是猜的" />
            <div className="grid gap-4 md:grid-cols-3">
              <Detail
                title="错误响应也是加密的"
                body="业务异常、404/405、422 校验失败、500 兜底全部经全局处理器包成信封。出错时不要找明文 detail，要先解密。"
              />
              <Detail
                title="GET 可以用 query 传信封"
                body="浏览器 GET 无法带 body，中间件额外支持 nonce/ciphertext 放在查询串。非浏览器客户端建议一律用 body，避免长密文撑爆 URL。"
              />
              <Detail
                title="文件流是分帧的，不是整体信封"
                body="下载/预览/流式播放按 64KB 明文分帧：[4B 大端帧长][12B nonce][密文+16B tag]，逐帧解密。"
              />
            </div>
          </div>

          {/* ── 明文白名单 ── */}
          <div className="mt-12">
            <SectionTitle
              title="明文白名单"
              sub="只有这三条不走加密信封，其余全部加密"
            />
            <div className="grid gap-2 sm:grid-cols-3">
              {openapi.plainPaths.map((p) => (
                <div
                  key={p}
                  className="rounded-[10px] border border-line bg-canvas-soft px-4 py-3 text-[12.5px]"
                >
                  <code>{p}</code>
                </div>
              ))}
            </div>
          </div>

          {/* ── 接口清单 ── */}
          <div className="mt-12">
            <SectionTitle
              title="接口清单"
              sub={`共 ${openapi.total} 个操作，按 ${openapi.groupCount} 个模块分组。请求/响应字段写在每个接口的描述里。`}
            />
            <div className="space-y-6">
              {openapi.groups.map((group) => (
                <div key={group.tag} id={`g-${group.tag}`}>
                  <div className="mb-2 flex items-center gap-2.5">
                    <h3 className="text-[15px] font-[620]">{group.tag}</h3>
                    <span className="tnum rounded bg-brand-50 px-1.5 py-0.5 text-[11px] text-brand-600">
                      {group.count}
                    </span>
                  </div>
                  <div className="overflow-hidden rounded-[10px] border border-line">
                    {group.operations.map((op, i) => (
                      <details
                        key={op.method + op.path}
                        className={`group ${i > 0 ? 'border-t border-line' : ''}`}
                      >
                        <summary className="flex cursor-pointer list-none items-center gap-2.5 px-4 py-2.5 text-[13px] hover:bg-canvas-soft">
                          <span
                            className={`w-[52px] shrink-0 rounded px-1.5 py-0.5 text-center text-[11px] font-[600] ${
                              op.method === 'GET'
                                ? 'bg-brand-50 text-brand-600'
                                : 'bg-canvas-soft text-ink-700'
                            }`}
                          >
                            {op.method}
                          </span>
                          <code className="shrink-0 text-[12.5px] text-ink-900">{op.path}</code>
                          <span className="truncate text-ink-500">{op.summary}</span>
                          {op.plain ? (
                            <span className="ml-auto shrink-0 rounded bg-canvas-soft px-1.5 py-0.5 text-[10.5px] text-ink-500">
                              明文
                            </span>
                          ) : null}
                        </summary>
                        {op.description ? (
                          <div className="whitespace-pre-wrap border-t border-line bg-canvas-soft px-4 py-3 text-[12.5px] leading-relaxed text-ink-700">
                            {op.description}
                          </div>
                        ) : (
                          <div className="border-t border-line bg-canvas-soft px-4 py-3 text-[12.5px] text-ink-500">
                            该接口没有补充描述。
                          </div>
                        )}
                      </details>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* ── 诚实的边界说明 ── */}
          <div className="mt-12">
            <SectionTitle title="本页不提供什么" sub="避免误解，先说清楚" />
            <KVTable
              rows={[
                ['在线调试', '不提供。加密接口需要本地完成密钥交换与加密，网页里直接发是调不通的。'],
                [
                  'SDK',
                  '不提供。当前没有官方客户端 SDK，本文只给协议级说明。',
                ],
                [
                  '机器可校验的参数结构',
                  `只有 1/${openapi.total} 个接口在 OpenAPI 里声明了 requestBody（其余接口手动解密请求体，框架无法推断）。字段说明以每个接口的描述文本为准。`,
                ],
                [
                  '官方 Swagger',
                  '服务端自带 /docs 与 /openapi.json（FastAPI 默认）。它们对你自己的部署可用，但同样无法直接点通加密接口。',
                ],
              ]}
            />
          </div>
        </div>
      </section>
    </>
  );
}

function Step({
  n,
  title,
  children,
  last,
}: {
  n: number;
  title: string;
  children: React.ReactNode;
  last?: boolean;
}) {
  return (
    <div className="relative pb-4 pl-9">
      {!last ? (
        <span className="absolute left-[11px] top-6 h-full w-px bg-line" aria-hidden />
      ) : null}
      <span className="tnum absolute left-0 top-0 flex h-[22px] w-[22px] items-center justify-center rounded-full bg-brand-50 text-[11.5px] font-[620] text-brand-600">
        {n}
      </span>
      <div className="text-[13.5px] font-[600]">{title}</div>
      <div className="mt-1 text-[13px] leading-relaxed text-ink-700">{children}</div>
    </div>
  );
}

function Detail({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-[10px] border border-line p-4">
      <div className="mb-1.5 text-[13.5px] font-[620]">{title}</div>
      <p className="text-[12.8px] leading-relaxed text-ink-500">{body}</p>
    </div>
  );
}
