import { motion } from "motion/react";

import { DOC_GROUPS } from "../docsData";
import type { DocEndpoint } from "../docsData";
import { Card } from "./Card";

/** 反引号内联 code 与 **粗体** 渲染（内容为自有文档数据，非用户输入） */
function renderInline(text: string) {
  return text.split("`").map((part, i) => {
    if (i % 2 === 1) {
      return (
        <code key={i} className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[11px] text-accent">
          {part}
        </code>
      );
    }
    // **粗体** 分段
    return (
      <span key={i}>
        {part.split(/\*\*(.+?)\*\*/g).map((seg, j) =>
          j % 2 === 1 ? (
            <strong key={j} className="font-semibold text-ink-2">{seg}</strong>
          ) : (
            <span key={j}>{seg}</span>
          ),
        )}
      </span>
    );
  });
}

const METHOD_CLS: Record<string, string> = {
  GET: "border-ok/40 bg-ok-bg text-ok",
  POST: "border-info/40 bg-info-bg text-info",
  WS: "border-accent-2/40 bg-accent-2-bg text-accent-2",
};

function CodeBlock({ label, code }: { label: string; code: string }) {
  return (
    <div className="mb-3">
      <div className="mb-1 text-[11px] uppercase tracking-wider text-ink-3">{label}</div>
      <pre className="scroll-thin overflow-x-auto rounded-xl border border-line bg-bg-2 px-3.5 py-3 font-mono text-xs leading-relaxed text-ink-2">
        <code>{code}</code>
      </pre>
    </div>
  );
}

function EndpointCard({ ep, delay }: { ep: DocEndpoint; delay: number }) {
  return (
    <Card delay={delay} className="mb-4 p-5">
      {ep.method ? (
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <span
            className={`rounded-md border px-2 py-0.5 font-mono text-[11px] ${METHOD_CLS[ep.method] ?? ""}`}
          >
            {ep.method}
          </span>
          <code className="font-mono text-sm text-ink">{ep.path}</code>
          <span className="text-sm font-medium text-ink-2">{ep.title}</span>
        </div>
      ) : (
        <h4 className="mb-2 text-sm font-semibold text-ink">{ep.path}</h4>
      )}

      {ep.note && (
        <p className="mb-3 text-xs leading-5 text-ink-3">{renderInline(ep.note)}</p>
      )}

      {ep.params && ep.params.length > 0 && (
        <table className="mb-3 w-full border-collapse text-sm">
          <thead>
            <tr className="border-b border-line text-left text-[11px] uppercase tracking-wider text-ink-3">
              <th className="py-1.5 pr-3">字段</th>
              <th className="py-1.5 pr-3">必填</th>
              <th className="py-1.5">说明</th>
            </tr>
          </thead>
          <tbody className="text-ink-2">
            {ep.params.map((p) => (
              <tr key={p.name} className="border-b border-line last:border-0">
                <td className="py-1.5 pr-3 font-mono text-xs">{p.name}</td>
                <td className="py-1.5 pr-3 text-xs">{p.req}</td>
                <td className="py-1.5 text-xs">{p.desc}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {ep.statusTable && (
        <>
          <table className="mb-3 w-full border-collapse text-sm">
            <thead>
              <tr className="border-b border-line text-left text-[11px] uppercase tracking-wider text-ink-3">
                <th className="py-1.5 pr-3">状态码</th>
                <th className="py-1.5 pr-3">含义</th>
                <th className="py-1.5">调用方行为</th>
              </tr>
            </thead>
            <tbody className="text-ink-2">
              {ep.statusTable.map(([code, meaning, act]) => (
                <tr key={code} className="border-b border-line last:border-0">
                  <td className="py-1.5 pr-3 font-mono text-xs">{code}</td>
                  <td className="py-1.5 pr-3">{meaning}</td>
                  <td className="py-1.5">{act}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {ep.footnote && <p className="mt-2 text-xs leading-5 text-ink-3">{renderInline(ep.footnote)}</p>}
        </>
      )}

      {ep.bullets && (
        <ul className="list-inside list-disc space-y-1.5 text-sm text-ink-2">
          {ep.bullets.map((b, i) => (
            <li key={i}>{renderInline(b)}</li>
          ))}
        </ul>
      )}

      {ep.body && <CodeBlock label="请求体" code={ep.body} />}
      {ep.curl && <CodeBlock label="curl" code={ep.curl} />}
      {ep.py && <CodeBlock label="Python" code={ep.py} />}
      {ep.resp && <CodeBlock label="响应" code={ep.resp} />}
    </Card>
  );
}

/** API 文档（原 api.html）：左侧 TOC + 右侧分节渲染。 */
export function ApiDocsPage() {
  return (
    <div className="flex flex-col gap-8 lg:flex-row">
      {/* TOC */}
      <nav className="shrink-0 lg:w-56" aria-label="文档目录">
        <div className="flex flex-row flex-wrap gap-1 lg:sticky lg:top-24 lg:flex-col">
          {DOC_GROUPS.map((g) => (
            <a
              key={g.id}
              href={`#${g.id}`}
              className="rounded-lg px-3 py-1.5 text-xs text-ink-3 transition-colors hover:bg-surface-2 hover:text-ink"
            >
              {g.name}
            </a>
          ))}
        </div>
      </nav>

      {/* 内容 */}
      <main className="min-w-0 flex-1 space-y-10">
        {DOC_GROUPS.map((g, gi) => (
          <section key={g.id} id={g.id} className="scroll-mt-24">
            <motion.h2
              initial={{ opacity: 0, y: 12 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              className="mb-1 text-base font-semibold text-ink"
            >
              {g.name}
            </motion.h2>
            {g.desc ? (
              <p className="mb-4 text-xs text-ink-3">{g.desc}</p>
            ) : (
              <div className="mb-4" />
            )}
            {g.endpoints.map((ep, ei) => (
              <EndpointCard key={`${gi}-${ei}`} ep={ep} delay={Math.min(0.15, ei * 0.04)} />
            ))}
          </section>
        ))}
      </main>
    </div>
  );
}
