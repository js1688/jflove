import { features, platforms, supports, supportCount } from '../content';
import { PageHead, SectionTitle } from '../components/ui';

/**
 * 功能详情页：11 项能力逐条展开（design-spec §5）。
 *
 * 每项：编号 + 名称 + 三端徽章 + 说明 + 支持度。
 * 数据全部来自 `content/features.json`（README「功能特性」的镜像），禁止硬编码。
 */
export function FeaturesPage() {
  return (
    <>
      <PageHead
        crumb="产品 / 功能详情"
        title={`${features.count} 项能力，逐条说清楚`}
        lede="每一项都标注了哪几端真的支持。写「—」的地方就是没做 —— 不做跨端功能承诺，也不让不支持的入口出现在界面上。"
      />

      <section className="py-12">
        <div className="mx-auto max-w-[1140px] px-7">
          {/* 支持度矩阵 */}
          <div className="mb-10 overflow-x-auto rounded-[10px] border border-line">
            <table className="w-full text-[13px]">
              <thead className="bg-canvas-soft text-[12px] text-ink-500">
                <tr>
                  <th className="px-4 py-2.5 text-left font-[560]">能力</th>
                  {platforms.map((p) => (
                    <th key={p} className="px-4 py-2.5 text-center font-[560]">
                      {p}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {features.features.map((f) => (
                  <tr key={f.name} className="border-t border-line">
                    <td className="px-4 py-2.5">{f.name}</td>
                    {platforms.map((p) => (
                      <td key={p} className="px-4 py-2.5 text-center">
                        {supports(f, p) ? (
                          <span className="text-brand-600">✓</span>
                        ) : (
                          <span className="text-ink-300">—</span>
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
              <tfoot className="border-t border-line bg-canvas-soft text-[12px] text-ink-500">
                <tr>
                  <td className="px-4 py-2.5 font-[560]">支持度合计</td>
                  {platforms.map((p) => (
                    <td key={p} className="tnum px-4 py-2.5 text-center">
                      {supportCount(p)}/{features.count}
                    </td>
                  ))}
                </tr>
              </tfoot>
            </table>
          </div>

          <SectionTitle
            title="逐项说明"
            sub={`数据源：${features.source}，与 README「功能特性」同一次维护`}
          />

          <div className="space-y-3">
            {features.features.map((f, i) => (
              <article
                key={f.name}
                id={`f-${i + 1}`}
                className="rounded-[10px] border border-line p-5"
              >
                <div className="mb-2 flex flex-wrap items-center gap-2.5">
                  <span className="tnum text-[12px] font-[560] text-ink-300">
                    {String(i + 1).padStart(2, '0')} / {features.count}
                  </span>
                  <h3 className="text-[15.5px] font-[620]">{f.name}</h3>
                  <span className="flex flex-wrap gap-1.5">
                    {platforms.map((p) => (
                      <span
                        key={p}
                        className={`rounded px-1.5 py-0.5 text-[11px] ${
                          supports(f, p)
                            ? 'bg-brand-50 text-brand-600'
                            : 'bg-canvas-soft text-ink-300'
                        }`}
                      >
                        {p}
                      </span>
                    ))}
                  </span>
                </div>
                <p className="text-[13.5px] text-ink-700">{f.summary}</p>
              </article>
            ))}
          </div>
        </div>
      </section>
    </>
  );
}
