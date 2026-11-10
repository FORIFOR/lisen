"use client";

import Link from "next/link";
import { ArrowRight, AudioLines, CircleAlert, ClipboardCheck, Loader2, RotateCcw, ShieldCheck, Sparkles, Trash2, Volume2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { AnalyticsTabs } from "@/components/analytics-tabs";
import { Meter } from "@/components/charts";
import { Button, buttonVariants } from "@/components/ui/button";
import { DiagnosticResultView } from "@/components/vocab-result";
import { MasteryChips, playAudio } from "@/components/word-question";
import { api, ApiError, MODALITY_LABEL, type Lesson, type LessonCheck, type Modality, type VocabStats } from "@/lib/api";
import { cn } from "@/lib/utils";

const ORDER: Modality[] = ["text", "audio", "context"];

function Stat({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="rounded-2xl border bg-card p-4">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-3xl font-semibold tabular">{value}</div>
      {sub && <div className="text-xs text-muted-foreground">{sub}</div>}
    </div>
  );
}

function Panel({ title, note, children, className }: { title: string; note?: string; children: React.ReactNode; className?: string }) {
  return (
    <section className={cn("rounded-2xl border bg-card p-4 md:p-5", className)}>
      <h2 className="text-sm font-medium">{title}</h2>
      {note && <p className="mt-0.5 text-xs text-muted-foreground">{note}</p>}
      <div className="mt-4">{children}</div>
    </section>
  );
}

function Checks({ checks }: { checks: LessonCheck[] }) {
  return (
    <ul className="mt-2 grid gap-1 text-xs sm:grid-cols-2">
      {checks.map((c) => (
        <li key={c.check} className={cn("flex items-start gap-1.5", c.ok ? "text-muted-foreground" : "text-destructive")}>
          {c.skipped ? (
            <CircleAlert className="mt-px size-3.5 shrink-0 text-muted-foreground" />
          ) : c.ok ? (
            <ShieldCheck className="mt-px size-3.5 shrink-0 text-success" />
          ) : (
            <CircleAlert className="mt-px size-3.5 shrink-0" />
          )}
          <span>
            {c.check}
            {c.detail && <span className="ml-1 opacity-80">（{c.detail}）</span>}
          </span>
        </li>
      ))}
    </ul>
  );
}

export default function VocabularyAnalyticsPage() {
  const [s, setS] = useState<VocabStats | null>(null);
  const [generating, setGenerating] = useState(false);
  const [rejected, setRejected] = useState<LessonCheck[] | null>(null);
  const [openLesson, setOpenLesson] = useState<string | null>(null);

  const load = useCallback(() => {
    api<VocabStats>("/v1/vocabulary/stats")
      .then(setS)
      .catch((e) => toast.error((e as Error).message));
  }, []);
  useEffect(load, [load]);

  const generate = async (words?: string[]) => {
    setGenerating(true);
    setRejected(null);
    try {
      const l = await api<Lesson>("/v1/audio-lessons/generate", { body: { words: words ?? null } });
      if (l.fallback) toast.warning(`Gemini が使えなかったため、ローカル生成で作りました（${l.fallback}）`);
      else toast.success(l.cached ? "同じ単語の教材がすでにあります" : "音声教材を作成しました。次の学習で「初見リスニング」に出題されます");
      load();
    } catch (e) {
      if (e instanceof ApiError && e.status === 422) {
        // show which quality checks failed
        setRejected([{ check: e.message, ok: false, detail: "" }, ...((e.data?.checks as LessonCheck[] | undefined) ?? [])]);
      } else toast.error((e as Error).message);
    } finally {
      setGenerating(false);
    }
  };

  const remove = async (id: string) => {
    try {
      await api(`/v1/audio-lessons/${id}`, { method: "DELETE" });
      load();
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  if (!s)
    return (
      <div className="flex justify-center py-24 text-muted-foreground">
        <Loader2 className="size-6 animate-spin" />
      </div>
    );

  const diagnosed = !!s.diagnostic.last;
  const gen = s.generator;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-xs font-medium tracking-wider text-muted-foreground">LISTENFLOW / VOCABULARY</p>
          <h1 className="text-2xl font-semibold tracking-tight">英単語の理解度</h1>
        </div>
        <AnalyticsTabs />
      </div>

      {!diagnosed && (
        <div className="flex flex-col gap-3 rounded-2xl border border-primary/30 bg-brand-soft p-4 sm:flex-row sm:items-center">
          <ClipboardCheck className="size-6 shrink-0 text-primary" />
          <div className="flex-1 text-sm">
            <div className="font-medium">{s.diagnostic.active ? `語彙診断の途中です（${s.diagnostic.active.answered} / ${s.diagnostic.active.max}）` : "まず語彙診断を受けましょう（約8分）"}</div>
            <div className="text-muted-foreground">忘れている単語と、「文字なら分かるのに音では分からない」単語を特定します</div>
          </div>
          <Link href="/vocab/diagnostic" className={buttonVariants()}>
            {s.diagnostic.active ? "続きから" : "診断を受ける"} <ArrowRight className="size-4" />
          </Link>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Stat label="診断済み単語" value={s.tested_words} sub={`全 ${s.dataset.words} 語中`} />
        <Stat label="要復習" value={s.due} sub="今日 FSRS で復習" />
        <Stat label="習得済み" value={s.acquired} sub="文字・音声・文脈すべて80%以上" />
        <Stat label="推定の語彙レベル" value={`${s.estimated_tier} / 4`} sub={diagnosed ? "語彙診断より" : "リスニングのレベルから仮置き"} />
      </div>

      <div className="flex flex-wrap gap-2">
        <Link href="/vocab/review" className={buttonVariants({ size: "lg" })}>
          <RotateCcw className="size-4" /> 単語の復習（{s.due}）
        </Link>
        <Link href="/vocab/practice" className={buttonVariants({ size: "lg", variant: "outline" })}>
          <Sparkles className="size-4" /> 単語トレーニング
        </Link>
        {diagnosed && (
          <Link href="/vocab/diagnostic" className={buttonVariants({ size: "lg", variant: "ghost" })}>
            診断を受け直す
          </Link>
        )}
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="能力別の習熟度" note="同じ単語でも、文字・音声・会話の中で別々に測っています。直近の回答ほど重く反映">
          <ul className="space-y-4">
            {ORDER.map((m) => {
              const v = s.modality[m];
              return (
                <li key={m} className={cn(v.mastery == null && "opacity-50")}>
                  <div className="mb-1 flex justify-between text-sm">
                    <span>{v.label}</span>
                    <span className="tabular text-muted-foreground">
                      {v.mastery == null ? "未測定" : `${Math.round(v.mastery * 100)}%`}
                      <span className="ml-1 text-xs">（{v.tested}語）</span>
                    </span>
                  </div>
                  <Meter value={v.mastery ?? 0} />
                </li>
              );
            })}
          </ul>
          {s.modality.text.mastery != null && s.modality.audio.mastery != null && s.modality.text.mastery - s.modality.audio.mastery >= 0.15 && (
            <p className="mt-4 rounded-xl bg-warning-soft px-3 py-2 text-sm">
              文字では分かるのに、音では認識できていない単語が多い状態です。音声・会話の問題を優先して出題します。
            </p>
          )}
        </Panel>

        <Panel title="今日の重点単語" note="3つの能力のうち、いちばん弱いものが低い順">
          {s.focus.length ? (
            <ul>
              {s.focus.map((w) => (
                <li key={w.id} className="border-b py-2.5 last:border-0">
                  <div className="flex items-center gap-3">
                    <button
                      type="button"
                      onClick={() => playAudio(w.audio)}
                      aria-label={`${w.lemma} を再生`}
                      className="flex size-8 shrink-0 items-center justify-center rounded-full border text-muted-foreground hover:bg-muted"
                    >
                      <Volume2 className="size-4" />
                    </button>
                    <div className="min-w-0 flex-1">
                      <div className="font-medium">{w.lemma}</div>
                      <div className="truncate text-sm text-muted-foreground">{w.meaning}</div>
                    </div>
                    <span className="shrink-0 text-xs text-muted-foreground">弱い: {w.weakest_label}</span>
                  </div>
                  <MasteryChips mastery={w.mastery} className="mt-1.5 pl-11" />
                </li>
              ))}
            </ul>
          ) : (
            <p className="py-8 text-center text-sm text-muted-foreground">診断や学習をすると、ここに重点単語が表示されます</p>
          )}
        </Panel>
      </div>

      <div className="grid gap-5 lg:grid-cols-2">
        <Panel title="難易度別" note="難易度は ListenFlow 独自の4段階です（CEFR や TOEIC 公式の値ではありません）">
          <table className="w-full text-sm">
            <thead className="text-xs text-muted-foreground">
              <tr>
                <th className="py-1 text-left font-normal">難易度</th>
                <th className="text-right font-normal">診断済み</th>
                {ORDER.map((m) => (
                  <th key={m} className="text-right font-normal">
                    {MODALITY_LABEL[m].slice(0, 2)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="tabular">
              {s.tiers.map((t) => (
                <tr key={t.tier} className="border-t">
                  <td className="py-2">{t.tier}</td>
                  <td className="text-right text-muted-foreground">
                    {t.tested} / {t.total}
                  </td>
                  {ORDER.map((m) => (
                    <td key={m} className="text-right">
                      {t[m] == null ? "–" : `${Math.round((t[m] as number) * 100)}%`}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>

        <Panel title="単語の状態">
          <ul className="space-y-2">
            {s.status
              .filter((x) => x.status !== "untested")
              .map((x) => (
                <li key={x.status} className="flex items-center gap-3 text-sm">
                  <span className="flex-1">{x.label}</span>
                  <span className="w-1/3">
                    <Meter value={s.tested_words ? x.count / s.tested_words : 0} />
                  </span>
                  <span className="w-10 text-right tabular text-muted-foreground">{x.count}</span>
                </li>
              ))}
          </ul>
        </Panel>
      </div>

      <Panel
        title="苦手語彙の音声教材"
        note={
          gen.engine === "gemini"
            ? `Gemini（${gen.gemini_model}）で会話文を作り、Mac の音声合成で音声化します。送信するのは対象の単語とレベルだけです${gen.mode === "auto" ? "。Gemini が使えないときはローカル生成に切り替えます" : ""}`
            : gen.gemini_configured
              ? "設定で「ローカル生成」が選ばれています。例文を組み合わせて会話を作ります"
              : "Gemini が未設定のため、例文を組み合わせたローカル生成で作ります。設定画面で API キーを登録すると自然な会話文になります"
        }
      >
        <div className="flex flex-wrap items-center gap-2">
          <Button onClick={() => generate()} disabled={generating || !gen.tts_available}>
            {generating ? <Loader2 className="size-4 animate-spin" /> : <AudioLines className="size-4" />}
            重点単語から教材を作る
          </Button>
          {!gen.tts_available && <span className="text-xs text-muted-foreground">{gen.tts_message}</span>}
          {s.auto_lesson_running && <span className="text-xs text-muted-foreground">自動で作成中…</span>}
        </div>
        {rejected && (
          <div className="mt-3 rounded-xl bg-danger-soft p-3 text-sm">
            <Checks checks={rejected} />
          </div>
        )}
        <p className="mt-2 text-xs text-muted-foreground">
          品質チェック: 指定語がすべて含まれる・英語のみ・正解が1つだけの問題・音声の長さと発話速度・音声認識（faster-whisper）で音声が英文と一致し指定語が聞き取れ、発音が明瞭なこと。不合格の教材は使いません。作った教材は次の学習の「初見リスニング」で優先的に出題され、書き取りで単語を聞き取れたかが「会話で理解」に反映されます。
        </p>
        {s.lessons.length > 0 && (
          <ul className="mt-4 space-y-2">
            {s.lessons.map((l) => (
              <li key={l.id} className="rounded-xl border p-3 text-sm">
                <div className="flex items-center gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="font-medium">{l.targets.map((t) => t.lemma).join(" / ")}</div>
                    <div className="text-xs text-muted-foreground">
                      {l.engine === "gemini" ? "Gemini" : "ローカル生成"} · {new Date(l.created_at).toLocaleDateString()} · {l.heard ? "学習済み" : "未学習（次の学習で出題）"}
                    </div>
                  </div>
                  <Button variant="ghost" size="sm" onClick={() => setOpenLesson(openLesson === l.id ? null : l.id)}>
                    品質チェック
                  </Button>
                  <Button variant="ghost" size="icon" aria-label="教材を削除" onClick={() => remove(l.id)}>
                    <Trash2 className="size-4" />
                  </Button>
                </div>
                {openLesson === l.id && <Checks checks={l.checks} />}
              </li>
            ))}
          </ul>
        )}
      </Panel>

      {s.diagnostic.last && (
        <details className="rounded-2xl border bg-card p-4 md:p-5">
          <summary className="cursor-pointer text-sm font-medium">前回の語彙診断の結果（{new Date(s.diagnostic.last.completed_at).toLocaleDateString()}）</summary>
          <div className="mt-4">
            <DiagnosticResultView result={s.diagnostic.last.result} />
          </div>
        </details>
      )}

      <p className="text-center text-xs text-muted-foreground">
        単語データ: {s.dataset.name}（{s.dataset.license}）。TOEIC の公式スコアを推定するものではありません。
      </p>
    </div>
  );
}
