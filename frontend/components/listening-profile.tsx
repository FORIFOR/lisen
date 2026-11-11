"use client";

import Link from "next/link";
import { Target } from "lucide-react";
import { useEffect, useState } from "react";
import { ABILITY_ORDER, api, type AbilityKey, type ErrorTrends, type ErrorType, type Kpi, type ListeningProfile } from "@/lib/api";
import { cn } from "@/lib/utils";

export type AbilityRow = {
  key: AbilityKey;
  label: string;
  detail: string;
  value: number | null;
  n: number;
  target: number;
  low?: number | null;
  high?: number | null;
  provisional?: boolean;
  previous?: number | null;
};

/** Four horizontal meters with the training target marked; the weakest ability is highlighted. */
export function AbilityBars({ rows, priority, compact }: { rows: AbilityRow[]; priority?: AbilityKey | null; compact?: boolean }) {
  const ordered = ABILITY_ORDER.map((k) => rows.find((r) => r.key === k)).filter(Boolean) as AbilityRow[];
  return (
    <ul className={cn("grid gap-3", !compact && "sm:grid-cols-2")}>
      {ordered.map((r) => {
        const pct = r.value == null ? null : Math.round(r.value * 100);
        const focus = priority === r.key;
        const delta = r.value != null && r.previous != null ? Math.round((r.value - r.previous) * 100) : null;
        return (
          <li key={r.key} className={cn("rounded-2xl border bg-card p-3.5", focus && "border-primary/40 bg-brand-soft")}>
            <div className="flex items-baseline justify-between gap-2">
              <div className="min-w-0">
                <div className="flex items-center gap-1.5 text-sm font-medium">
                  {r.label}
                  {focus && (
                    <span className="inline-flex items-center gap-0.5 rounded-full bg-primary px-1.5 py-px text-[10px] font-medium text-primary-foreground">
                      <Target className="size-2.5" /> 優先
                    </span>
                  )}
                </div>
                <div className="truncate text-xs text-muted-foreground">{r.detail}</div>
              </div>
              <div className="shrink-0 text-right">
                <span className="text-2xl font-semibold tabular">{pct == null ? "–" : `${pct}%`}</span>
                {delta != null && delta !== 0 && (
                  <span className={cn("ml-1 text-xs tabular", delta > 0 ? "text-success" : "text-destructive")}>
                    {delta > 0 ? "+" : ""}
                    {delta}
                  </span>
                )}
              </div>
            </div>
            <div className="relative mt-2 h-2 rounded-full bg-muted" role="meter" aria-valuenow={pct ?? 0} aria-valuemin={0} aria-valuemax={100} aria-label={r.label}>
              {r.low != null && r.high != null && r.value != null && (
                <div className="absolute inset-y-0 rounded-full bg-[var(--series-1)] opacity-20" style={{ left: `${r.low * 100}%`, width: `${(r.high - r.low) * 100}%` }} />
              )}
              {pct != null && <div className="absolute inset-y-0 left-0 rounded-full bg-[var(--series-1)]" style={{ width: `${pct}%` }} />}
              <div className="absolute -inset-y-1 w-0.5 rounded bg-foreground/60" style={{ left: `${r.target * 100}%` }} title={`目標 ${Math.round(r.target * 100)}%`} />
            </div>
            <div className="mt-1.5 flex justify-between text-[11px] text-muted-foreground tabular">
              <span>
                {r.n}問{r.provisional || r.n < 8 ? " · 暫定" : ""}
                {r.low != null && r.high != null && r.value != null && !compact && ` · 95%区間 ${Math.round(r.low * 100)}–${Math.round(r.high * 100)}%`}
              </span>
              <span>目標 {Math.round(r.target * 100)}%</span>
            </div>
          </li>
        );
      })}
    </ul>
  );
}

// ------------------------------------------------------------------ full panel (analytics)


function pct(v: number | null | undefined) {
  return v == null ? "–" : `${Math.round(v * 100)}%`;
}

function KpiTile({ k, primary }: { k: Kpi; primary?: boolean }) {
  const d = k.recent.value != null && k.previous.value != null ? Math.round((k.recent.value - k.previous.value) * 100) : null;
  return (
    <div className={cn("rounded-2xl border bg-card p-4", primary && "border-primary/40")}>
      <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
        {k.label}
        {primary && <span className="rounded-full bg-primary px-1.5 py-px text-[10px] text-primary-foreground">最重要</span>}
      </div>
      <div className="mt-1 flex items-baseline gap-2">
        <span className="text-2xl font-semibold tabular">{pct(k.recent.value)}</span>
        {d != null && d !== 0 && <span className={cn("text-xs tabular", d > 0 ? "text-success" : "text-destructive")}>{d > 0 ? `+${d}` : d}（前の14日比）</span>}
      </div>
      <div className="text-[11px] text-muted-foreground tabular">
        直近14日 {k.recent.n}問 · {k.note}
      </div>
    </div>
  );
}

function ErrorTrendsPanel({ t }: { t: ErrorTrends }) {
  const order: ErrorType[] = ["phonetic", "vocabulary", "grammar", "meaning"];
  return (
    <div className="rounded-2xl border bg-card p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-medium">聞き取れなかった原因の傾向</h3>
        <span className="text-xs text-muted-foreground tabular">直近 {t.n} 件の「1文の攻略」</span>
      </div>
      {t.n === 0 ? (
        <p className="mt-2 text-xs text-muted-foreground">まだ記録がありません。トレーニングで聞き取れなかった1文を確かめると、ここに集計されます。</p>
      ) : (
        <>
          <ul className="mt-3 space-y-2">
            {order.map((e) => {
              const v = t.counts[e] ?? 0;
              const pctv = t.n ? Math.round((v / t.n) * 100) : 0;
              return (
                <li key={e} className="grid grid-cols-[8.5rem_1fr_3rem] items-center gap-2 text-sm">
                  <span className={cn(t.dominant === e && "font-medium")}>{t.labels[e]}</span>
                  <div className="h-2 rounded-full bg-muted" role="meter" aria-label={t.labels[e]} aria-valuenow={pctv} aria-valuemin={0} aria-valuemax={100}>
                    <div className="h-2 rounded-full bg-[var(--series-1)]" style={{ width: `${pctv}%` }} />
                  </div>
                  <span className="text-right text-xs text-muted-foreground tabular">{v}件</span>
                </li>
              );
            })}
          </ul>
          <p className="mt-3 text-xs text-muted-foreground">
            {t.dominant
              ? `いちばん多い原因は「${t.labels[t.dominant]}」です。次は ${t.next_step} を増やすと効果が見込めます。`
              : `原因を判断するには ${t.min_n} 件以上の記録が必要です（1回の誤答では断定しません）。`}
          </p>
        </>
      )}
    </div>
  );
}

const SOURCE_LABEL: Record<string, string> = { diagnostic: "リスニング診断" };

export function ListeningProfilePanel() {
  const [p, setP] = useState<ListeningProfile | null>(null);
  useEffect(() => {
    api<ListeningProfile>("/v1/listening-profile").then(setP).catch(() => {});
  }, []);
  if (!p) return null;
  const rows: AbilityRow[] = ABILITY_ORDER.map((k) => ({ key: k, ...p.abilities[k] }));
  const k = p.kpis;
  return (
    <section className="space-y-4 rounded-3xl border bg-card/50 p-4 md:p-5">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <p className="text-[11px] font-semibold tracking-[0.16em] text-muted-foreground">LISTENING PROFILE</p>
          <h2 className="text-lg font-semibold">聞き取れない原因</h2>
        </div>
        {!p.diagnosed && (
          <Link href="/onboarding" className="text-xs text-primary underline-offset-2 hover:underline">
            リスニング診断を受ける
          </Link>
        )}
      </div>
      <AbilityBars rows={rows} priority={p.priority} />
      {p.advice && <p className="rounded-xl bg-brand-soft px-3 py-2 text-sm">{p.advice}</p>}
      <p className="text-xs text-muted-foreground">
        直近の回答ほど重く数えた推定値です（帯は95%区間の目安）。回答が8問未満の能力は暫定です。TOEIC スコアの予測ではありません。
      </p>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-3">
        <KpiTile k={k.unseen_comprehension} primary />
        <KpiTile k={k.first_play} primary />
        <KpiTile k={k.transfer} />
        <KpiTile k={k.sound} />
        <KpiTile k={k.audio_vocab} />
        <div className="rounded-2xl border bg-card p-4">
          <div className="text-xs text-muted-foreground">{k.retention.label}</div>
          <div className="mt-1 text-2xl font-semibold tabular">
            {pct(k.retention.d7)} <span className="text-sm font-normal text-muted-foreground">/ 7日</span>
          </div>
          <div className="text-[11px] text-muted-foreground tabular">30日では {pct(k.retention.d30)}</div>
        </div>
      </div>

      <ErrorTrendsPanel t={p.error_trends} />

      {p.history.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[480px] text-sm">
            <caption className="mb-1 text-left text-xs text-muted-foreground">測定の記録（診断と14日ごとの確認テスト）</caption>
            <thead className="text-xs text-muted-foreground">
              <tr>
                <th className="py-1 text-left font-normal">日付</th>
                <th className="text-left font-normal">測定</th>
                {ABILITY_ORDER.map((a) => (
                  <th key={a} className="text-right font-normal">
                    {p.abilities[a].label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="tabular">
              {p.history.map((h) => (
                <tr key={h.created_at} className="border-t">
                  <td className="py-1.5">{h.date}</td>
                  <td>{SOURCE_LABEL[h.source] ?? h.source.replace("checkpoint-", "確認テスト ")}</td>
                  {ABILITY_ORDER.map((a) => (
                    <td key={a} className="text-right">
                      {pct(h.data[a]?.value)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
