"use client";

import { Loader2, Lock } from "lucide-react";
import { useEffect, useState } from "react";
import { AnalyticsTabs } from "@/components/analytics-tabs";
import { ListeningProfilePanel } from "@/components/listening-profile";
import { BarChart, LineChart, Meter } from "@/components/charts";
import { api, LEVEL_LABEL } from "@/lib/api";
import { cn } from "@/lib/utils";

type Analytics = {
  total_minutes: number;
  study_days: number;
  streak: number;
  day: number;
  daily: { date: string; minutes: number }[];
  trend: { date: string; dictation?: number; comprehension?: number; review?: number }[];
  mastery: { skill: string; label: string; mastery: number; evidence: number }[];
  weakest: { skill: string; label: string; mastery: number }[];
  errors: { skill: string; label: string; count: number }[];
  levels: { level: number; name: string; unlocked: boolean; current: boolean; dictation: number | null; comprehension: number | null; attempts: number }[];
  items_done: number;
  clips_heard: number;
  review_forecast: { date: string; count: number }[];
  cards: number;
  tests: { form: string; taken_at: string; day: number; dictation: number; comprehension: number; score: number; level_estimate: number }[];
  curriculum: { start: number; end: number; name: string; description: string }[];
};

const TEST_NAME: Record<string, string> = { A: "初回診断", B: "Day 15", C: "Day 29", D: "Day 43", E: "Day 57" };

function Stat({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="rounded-2xl border bg-card p-4">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular">{value}</div>
      {sub && <div className="text-xs text-muted-foreground">{sub}</div>}
    </div>
  );
}

function Panel({ title, children, className, note }: { title: string; children: React.ReactNode; className?: string; note?: string }) {
  return (
    <section className={cn("rounded-2xl border bg-card p-4 md:p-5", className)}>
      <h2 className="text-sm font-medium">{title}</h2>
      {note && <p className="mt-0.5 text-xs text-muted-foreground">{note}</p>}
      <div className="mt-4">{children}</div>
    </section>
  );
}

const md = (d: string) => `${Number(d.slice(5, 7))}/${Number(d.slice(8, 10))}`;

export default function AnalyticsPage() {
  const [a, setA] = useState<Analytics | null>(null);
  useEffect(() => {
    api<Analytics>("/analytics").then(setA).catch(() => {});
  }, []);
  if (!a)
    return (
      <div className="flex justify-center py-24 text-muted-foreground">
        <Loader2 className="size-6 animate-spin" />
      </div>
    );

  const avgRecent = a.daily.slice(-7).reduce((s, d) => s + d.minutes, 0) / 7;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold tracking-tight">分析</h1>
        <AnalyticsTabs />
      </div>
      <ListeningProfilePanel />
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="累計学習時間" value={`${Math.floor(a.total_minutes / 60)}時間${a.total_minutes % 60}分`} />
        <Stat label="学習日数" value={`${a.study_days}日`} sub={`${a.streak}日連続`} />
        <Stat label="聞いた音声" value={`${a.clips_heard}本`} sub={`${a.items_done} 問に回答`} />
        <Stat label="復習カード" value={a.cards} sub="FSRS で管理中" />
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="学習時間（30日間）" note={`直近7日の平均 ${avgRecent.toFixed(0)}分/日`}>
          <BarChart
            ariaLabel="日ごとの学習時間"
            unit="分"
            goal={30}
            data={a.daily.map((d) => ({ label: md(d.date), value: d.minutes }))}
          />
          <div className="mt-1 flex justify-between text-[10px] text-muted-foreground">
            <span>{md(a.daily[0].date)}</span>
            <span>今日</span>
          </div>
        </Panel>

        <Panel title="正答率の推移" note="ディクテーションは語単位の正確さ、内容理解は設問の正答率">
          {a.trend.length ? (
            <LineChart
              ariaLabel="日ごとの正答率"
              labels={a.trend.map((t) => md(t.date))}
              series={[
                { key: "d", label: "ディクテーション", color: "var(--series-1)", values: a.trend.map((t) => t.dictation ?? null) },
                { key: "c", label: "内容理解", color: "var(--series-2)", values: a.trend.map((t) => t.comprehension ?? null) },
              ]}
            />
          ) : (
            <p className="py-8 text-center text-sm text-muted-foreground">学習するとここに推移が表示されます</p>
          )}
        </Panel>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="聞き取りスキル別の習熟度" note="直近の回答ほど重く反映。データが少ない項目は薄く表示">
          <ul className="space-y-3">
            {a.mastery.map((m) => (
              <li key={m.skill} className={cn(m.evidence < 3 && "opacity-50")}>
                <div className="mb-1 flex justify-between text-sm">
                  <span>{m.label}</span>
                  <span className="tabular text-muted-foreground">{Math.round(m.mastery * 100)}%</span>
                </div>
                <Meter value={m.mastery} />
              </li>
            ))}
          </ul>
          {a.weakest.length > 0 && (
            <p className="mt-4 rounded-xl bg-warning-soft px-3 py-2 text-sm">
              いまの弱点: <strong>{a.weakest.map((w) => w.label).join("、")}</strong> — 弱点別の集中訓練で優先的に出題されます
            </p>
          )}
        </Panel>

        <Panel title="聞き取りミスの内訳（14日間）">
          {a.errors.length ? (
            <table className="w-full text-sm">
              <tbody>
                {a.errors.map((e) => (
                  <tr key={e.skill} className="border-b last:border-0">
                    <td className="py-2">{e.label}</td>
                    <td className="w-1/2 py-2">
                      <Meter value={e.count / a.errors[0].count} />
                    </td>
                    <td className="py-2 text-right tabular text-muted-foreground">{e.count}語</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="py-8 text-center text-sm text-muted-foreground">まだミスのデータがありません</p>
          )}
        </Panel>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="レベルと教材の解放" note="日数ではなく、各レベルでの正答率で次のレベルが解放されます（書き取り85%・理解75%）">
          <ul className="space-y-2">
            {a.levels.map((l) => (
              <li key={l.level} className={cn("flex items-center gap-3 rounded-xl border px-3 py-2 text-sm", l.current && "border-primary bg-brand-soft")}>
                <span className="w-10 font-semibold">{LEVEL_LABEL[l.level]}</span>
                <span className="flex-1 text-muted-foreground">{l.name}</span>
                {l.unlocked ? (
                  <span className="tabular text-xs text-muted-foreground">
                    書取 {l.dictation != null ? `${Math.round(l.dictation * 100)}%` : "–"} · 理解 {l.comprehension != null ? `${Math.round(l.comprehension * 100)}%` : "–"}
                  </span>
                ) : (
                  <Lock className="size-4 text-muted-foreground" />
                )}
              </li>
            ))}
          </ul>
        </Panel>

        <Panel title="初見リスニングテスト" note="毎回、一度も聞いていない別の音声で測定します">
          {a.tests.length ? (
            <table className="w-full text-sm">
              <thead className="text-xs text-muted-foreground">
                <tr>
                  <th className="py-1 text-left font-normal">テスト</th>
                  <th className="text-right font-normal">スコア</th>
                  <th className="text-right font-normal">書取</th>
                  <th className="text-right font-normal">理解</th>
                  <th className="text-right font-normal">推定</th>
                </tr>
              </thead>
              <tbody className="tabular">
                {a.tests.map((t) => (
                  <tr key={t.taken_at} className="border-t">
                    <td className="py-2">
                      {TEST_NAME[t.form]} <span className="text-xs text-muted-foreground">Day {t.day}</span>
                    </td>
                    <td className="text-right font-semibold">{Math.round(t.score)}</td>
                    <td className="text-right">{Math.round(t.dictation * 100)}%</td>
                    <td className="text-right">{Math.round(t.comprehension * 100)}%</td>
                    <td className="text-right">{LEVEL_LABEL[t.level_estimate]}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="py-6 text-center text-sm text-muted-foreground">まだテスト結果がありません</p>
          )}
        </Panel>
      </div>

      <Panel title="60日間のカリキュラム" note={`現在 Day ${a.day}。各期間で重点的に扱う音のテーマが変わります`}>
        <ol className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {a.curriculum.map((c) => {
            const now = a.day >= c.start && a.day <= c.end;
            const past = a.day > c.end;
            return (
              <li key={c.start} className={cn("rounded-xl border p-3 text-sm", now && "border-primary bg-brand-soft", past && "opacity-60")}>
                <div className="text-xs text-muted-foreground">
                  Day {c.start}–{c.end}
                  {now && " · いまここ"}
                </div>
                <div className="font-medium">{c.name}</div>
                <div className="text-xs text-muted-foreground">{c.description}</div>
              </li>
            );
          })}
        </ol>
      </Panel>
    </div>
  );
}
