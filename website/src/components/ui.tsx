import type { ReactNode } from 'react';
import { Link } from 'react-router';

/** 通用页面标题区：面包屑 + 大标题 + 导语 */
export function PageHead({
  crumb,
  title,
  lede,
  aside,
}: {
  crumb: string;
  title: string;
  lede: string;
  aside?: ReactNode;
}) {
  return (
    <section className="border-b border-line">
      <div className="mx-auto max-w-[1140px] px-7 pt-14 pb-10">
        <div className="mb-3 text-[12.5px] text-ink-500">{crumb}</div>
        <h1 className="mb-3 text-[32px] font-[680] tracking-[-0.02em]">{title}</h1>
        <p className="max-w-[680px] text-[14.5px] text-ink-700">{lede}</p>
        {aside}
      </div>
    </section>
  );
}

/** 区块标题 */
export function SectionTitle({
  title,
  sub,
  right,
}: {
  title: string;
  sub?: string;
  right?: ReactNode;
}) {
  return (
    <div className="mb-7 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h2 className="text-[20px] font-[660] tracking-[-0.015em]">{title}</h2>
        {sub ? <p className="mt-1 text-[13px] text-ink-500">{sub}</p> : null}
      </div>
      {right}
    </div>
  );
}

/** 提示条（信息） */
export function InfoBar({ children }: { children: ReactNode }) {
  return (
    <div className="rounded-[10px] border border-brand-100 bg-brand-50 px-4 py-3 text-[13px] text-brand-700">
      {children}
    </div>
  );
}

/** 入口卡片 */
export function EntryCard({
  to,
  title,
  badge,
  desc,
  cta,
  tags,
}: {
  to: string;
  title: string;
  badge?: string;
  desc: string;
  cta: string;
  tags?: string[];
}) {
  return (
    <Link
      to={to}
      className="group flex flex-col rounded-[10px] border border-line p-5 transition-colors hover:border-brand-500"
    >
      <div className="mb-2 flex items-center justify-between gap-2">
        <b className="text-[14.5px] font-[620]">{title}</b>
        {badge ? (
          <span className="tnum rounded border border-line px-1.5 py-0.5 text-[11px] text-ink-500">
            {badge}
          </span>
        ) : null}
      </div>
      <p className="mb-3 flex-1 text-[12.8px] leading-relaxed text-ink-500">{desc}</p>
      {tags?.length ? (
        <div className="mb-3 flex flex-wrap gap-1.5">
          {tags.map((t) => (
            <span key={t} className="rounded bg-canvas-soft px-1.5 py-0.5 text-[11px] text-ink-500">
              {t}
            </span>
          ))}
        </div>
      ) : null}
      <span className="text-[12.5px] font-[540] text-brand-600">
        {cta} <span aria-hidden>→</span>
      </span>
    </Link>
  );
}

/** 代码块 */
export function CodeBlock({ code, label }: { code: string; label?: string }) {
  return (
    <div className="overflow-hidden rounded-[10px] border border-line bg-canvas-soft">
      {label ? (
        <div className="border-b border-line bg-white px-3 py-1.5 text-[11.5px] text-ink-500">
          {label}
        </div>
      ) : null}
      <pre className="overflow-x-auto px-4 py-3 text-[12.3px] leading-[1.85] text-ink-700">
        <code>{code}</code>
      </pre>
    </div>
  );
}

/** 键值表：用于「字段 → 说明」 */
export function KVTable({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <div className="overflow-hidden rounded-[10px] border border-line">
      {rows.map(([k, v], i) => (
        <div
          key={k + i}
          className={`grid grid-cols-[180px_1fr] gap-3 px-4 py-2.5 text-[13px] ${
            i > 0 ? 'border-t border-line' : ''
          }`}
        >
          <span className="font-[560] text-ink-900">{k}</span>
          <span className="text-ink-700">{v}</span>
        </div>
      ))}
    </div>
  );
}
