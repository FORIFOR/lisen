"use client";

import Link from "next/link";
import { motion } from "motion/react";
import { Award, CircleAlert, Sparkles } from "lucide-react";
import { buttonVariants } from "@/components/ui/button";
import { AbilityBars } from "@/components/listening-profile";
import { cn } from "@/lib/utils";
import { ABILITY_ORDER, LEVEL_LABEL, type Summary } from "@/lib/api";

const KIND_LABEL: Record<string, string> = {
  comprehension: "内容理解",
  dictation: "ディクテーション",
  review: "復習",
  discrimination: "聞き分け",
  vocab: "語彙",
  shadowing: "シャドーイング",
  word: "TOEIC 語彙",
  sentence: "構文理解",
};

export function SummaryView({ summary }: { summary: Summary }) {
  const s = summary.session;
  const t = summary.test;
  const p = summary.profile;
  const isProfile = s.mode === "profile";
  const minutes = Math.round(s.study_seconds / 60);
  return (
    <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="space-y-6">
      <div className="text-center">
        <div className="mx-auto mb-3 flex size-14 items-center justify-center rounded-full bg-success-soft">
          {t ? <Award className="size-7 text-success" /> : <Sparkles className="size-7 text-success" />}
        </div>
        <h1 className="text-2xl font-semibold">{isProfile ? "あなたの Listening Profile" : t ? "テスト結果" : "おつかれさまでした"}</h1>
        <p className="mt-1 text-muted-foreground">
          学習時間 {minutes}分 · {summary.items} 問{summary.new_clips ? ` · 新しい音声 ${summary.new_clips} 本` : ""}
        </p>
      </div>

      {p && (
        <section className="space-y-3">
          <AbilityBars
            priority={p.priority}
            rows={ABILITY_ORDER.map((k) => ({
              key: k,
              label: p.labels[k],
              detail: p.details[k],
              value: p.measured[k]?.value ?? null,
              n: p.measured[k]?.n ?? 0,
              target: p.targets[k],
              previous: p.previous?.[k]?.value ?? null,
            }))}
          />
          {p.advice && <div className="rounded-2xl border border-primary/30 bg-brand-soft p-4 text-sm font-medium">{p.advice}</div>}
          <p className="text-xs text-muted-foreground">
            約50問からの暫定的な推定です。毎日の学習の回答で更新され、学習プランの時間配分（最も弱い能力に約15%を追加、初見リスニングは25%以上を維持）に反映されます。TOEIC スコアの予測ではありません。
          </p>
        </section>
      )}

      {t && (
        <div className="rounded-2xl border bg-card p-5">
          <div className="flex items-end justify-between">
            <div>
              <div className="text-sm text-muted-foreground">総合スコア</div>
              <div className="text-5xl font-semibold tabular">{Math.round(t.score)}</div>
            </div>
            <div className="text-right">
              <div className="text-sm text-muted-foreground">推定レベル</div>
              <div className="text-2xl font-semibold">
                {t.level_estimate} <span className="text-base text-muted-foreground">({LEVEL_LABEL[t.level_estimate]})</span>
              </div>
            </div>
          </div>
          <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
            <div className="rounded-xl bg-muted p-3">
              書き取り正確さ <div className="text-xl font-semibold tabular">{Math.round(t.dictation * 100)}%</div>
            </div>
            <div className="rounded-xl bg-muted p-3">
              内容理解 <div className="text-xl font-semibold tabular">{Math.round(t.comprehension * 100)}%</div>
            </div>
          </div>
          <table className="mt-4 w-full text-sm">
            <thead className="text-xs text-muted-foreground">
              <tr>
                <th className="py-1 text-left font-normal">レベル</th>
                <th className="text-right font-normal">書き取り</th>
                <th className="text-right font-normal">理解</th>
              </tr>
            </thead>
            <tbody className="tabular">
              {Object.entries(t.detail.levels).map(([lv, v]) => (
                <tr key={lv} className="border-t">
                  <td className="py-1.5">
                    {lv} ({LEVEL_LABEL[Number(lv)]})
                  </td>
                  <td className="text-right">{v.dictation != null ? `${Math.round(v.dictation * 100)}%` : "–"}</td>
                  <td className="text-right">{v.comprehension != null ? `${Math.round(v.comprehension * 100)}%` : "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {summary.events.map((e, i) => (
        <div key={i} className="rounded-2xl bg-brand-soft p-4 text-sm">
          {e.event === "level_up" ? `🎉 レベル ${e.to} の教材が解放されました` : `教材の難易度をレベル ${e.to} に調整しました`}
        </div>
      ))}

      {!t && !isProfile && (
        <div className="grid gap-3 sm:grid-cols-2">
          {Object.entries(summary.by_kind).map(([k, v]) => (
            <div key={k} className="flex items-center justify-between rounded-2xl border bg-card p-4">
              <div>
                <div className="text-sm font-medium">{KIND_LABEL[k] ?? k}</div>
                <div className="text-xs text-muted-foreground">{v.count} 問</div>
              </div>
              <div className="text-2xl font-semibold tabular">{k === "shadowing" ? "–" : `${Math.round(v.average * 100)}%`}</div>
            </div>
          ))}
        </div>
      )}

      {summary.errors.length > 0 && (
        <div className="rounded-2xl border bg-card p-4">
          <div className="mb-3 flex items-center gap-2 text-sm font-medium">
            <CircleAlert className="size-4 text-warning" /> 今日の聞き取りミスの傾向
          </div>
          <ul className="space-y-2">
            {summary.errors.slice(0, 5).map((e) => (
              <li key={e.skill} className="flex items-center justify-between text-sm">
                {e.label}
                <span className="tabular text-muted-foreground">{e.count} 語</span>
              </li>
            ))}
          </ul>
          <p className="mt-3 text-xs text-muted-foreground">次回の「弱点別の集中訓練」はこの傾向をもとに自動で組まれます。</p>
        </div>
      )}

      <div className="flex flex-col gap-2 sm:flex-row sm:justify-center">
        <Link href="/" className={cn(buttonVariants({ size: "lg" }), "h-12 px-8")}>
          ホームへ
        </Link>
        <Link href="/analytics" className={cn(buttonVariants({ size: "lg", variant: "outline" }), "h-12 px-8")}>
          分析を見る
        </Link>
      </div>
    </motion.div>
  );
}
