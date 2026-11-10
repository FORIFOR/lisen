"use client";

import { CircleAlert, ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

type FormStats = {
  form: string;
  words: number;
  words_per_second: number;
  words_per_line: number;
  long_word_share: number;
  seconds: number;
  gist_inference: number;
};
type Check = { metric: string; ok: boolean; mean?: number; min?: number; max?: number; max_deviation?: number; tolerance?: number; detail?: string };
type Overview = {
  plan: { slot: number; day: number; form: string; name: string; available: boolean; taken: boolean;
          result: { score: number; dictation: number; comprehension: number; day: number } | null }[];
  equivalence: { forms: FormStats[]; checks: Check[]; ok: boolean };
  note: string;
};

const METRIC: Record<string, string> = {
  structure: "構成（レベル1〜5 × 各2問）",
  words: "語数",
  words_per_second: "話す速さ（語/秒）",
  words_per_line: "1文の長さ（語）",
  long_word_share: "3音節以上の語の割合",
  gist_inference: "要点・推論の設問数",
};

function tol(c: Check) {
  if (c.metric === "structure") return c.detail ?? "";
  if (c.metric === "long_word_share") return `平均との差 ${Math.round((c.max_deviation ?? 0) * 1000) / 10}pt（基準 ${(c.tolerance ?? 0) * 100}pt 以内）`;
  if (c.metric === "gist_inference") return `差 ${c.max_deviation}問（基準 ${c.tolerance}問 以内）`;
  return `平均との差 最大 ${Math.round((c.max_deviation ?? 0) * 100)}%（基準 ${Math.round((c.tolerance ?? 0) * 100)}% 以内）`;
}

/** Checkpoint schedule (random form order per learner), results, and how comparable the forms are. */
export function CheckpointPanel() {
  const [o, setO] = useState<Overview | null>(null);
  useEffect(() => {
    api<Overview>("/v1/checkpoints").then(setO).catch(() => {});
  }, []);
  if (!o) return null;
  const byForm = Object.fromEntries(o.equivalence.forms.map((f) => [f.form, f]));
  return (
    <section className="rounded-2xl border bg-card p-4 md:p-5">
      <h2 className="text-sm font-medium">初見リスニングテスト（14日ごと）</h2>
      <p className="mt-0.5 text-xs text-muted-foreground">{o.note}</p>
      <div className="mt-4 overflow-x-auto">
        <table className="w-full min-w-[520px] text-sm">
          <thead className="text-xs text-muted-foreground">
            <tr>
              <th className="py-1 text-left font-normal">回</th>
              <th className="text-left font-normal">音声セット</th>
              <th className="text-right font-normal">スコア</th>
              <th className="text-right font-normal">書取</th>
              <th className="text-right font-normal">理解</th>
              <th className="text-right font-normal">語/秒</th>
              <th className="text-right font-normal">語数</th>
            </tr>
          </thead>
          <tbody className="tabular">
            {o.plan.map((p) => (
              <tr key={p.slot} className={cn("border-t", !p.taken && !p.available && "text-muted-foreground")}>
                <td className="py-2">{p.name}</td>
                <td>{p.form}</td>
                <td className="text-right font-semibold">{p.result ? Math.round(p.result.score) : p.available ? "受験可" : "–"}</td>
                <td className="text-right">{p.result ? `${Math.round(p.result.dictation * 100)}%` : ""}</td>
                <td className="text-right">{p.result ? `${Math.round(p.result.comprehension * 100)}%` : ""}</td>
                <td className="text-right text-muted-foreground">{byForm[p.form]?.words_per_second}</td>
                <td className="text-right text-muted-foreground">{byForm[p.form]?.words}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <details className="mt-3 text-sm">
        <summary className="cursor-pointer text-xs text-muted-foreground hover:text-foreground">
          5回の音声セットは同程度か（{o.equivalence.ok ? "すべて基準内" : "基準外あり"}）
        </summary>
        <ul className="mt-2 space-y-1 text-xs">
          {o.equivalence.checks.map((c) => (
            <li key={c.metric} className={cn("flex items-start gap-1.5", c.ok ? "text-muted-foreground" : "text-destructive")}>
              {c.ok ? <ShieldCheck className="mt-px size-3.5 shrink-0 text-success" /> : <CircleAlert className="mt-px size-3.5 shrink-0" />}
              <span>
                <span className="text-foreground">{METRIC[c.metric] ?? c.metric}</span>：{tol(c)}
              </span>
            </li>
          ))}
        </ul>
        <p className="mt-2 text-xs text-muted-foreground">
          長さ・速さ・語の難しさなど客観的な指標の比較です。実際の難しさが等しいことの証明ではありません（多くの利用者の回答データが必要です）。そのため音声セットの順番を利用者ごとにランダムにしています。
        </p>
      </details>
    </section>
  );
}
