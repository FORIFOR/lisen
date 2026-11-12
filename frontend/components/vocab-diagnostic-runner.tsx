"use client";

import Link from "next/link";
import { motion } from "motion/react";
import { ArrowRight, Loader2, RotateCcw, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Kbd } from "@/components/audio-player";
import { Button, buttonVariants } from "@/components/ui/button";
import { DiagnosticResultView, PlacementResultView } from "@/components/vocab-result";
import { WordQuestion } from "@/components/word-question";
import { api, type Diagnostic, type Profile, type VocabStats, type WordFeedback, type WordItem } from "@/lib/api";
import { cn } from "@/lib/utils";

type NextOut = { diagnostic: Diagnostic; item: WordItem | null; done: boolean };

/** The word diagnostic. "listening": by ear first (part of the listening profile).
 *  "placement": the word book's entrance — written words first, words known in writing re-checked by ear. */
export function VocabDiagnosticRunner({ style }: { style: "listening" | "placement" }) {
  const placement = style === "placement";
  const [rate, setRate] = useState(1);
  const [active, setActive] = useState<VocabStats["diagnostic"]["active"] | undefined>(undefined);
  const [diag, setDiag] = useState<Diagnostic | null>(null);
  const [item, setItem] = useState<WordItem | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Profile>("/profile").then((p) => setRate(p.settings.playback_rate)).catch(() => {});
    api<VocabStats>("/v1/vocabulary/stats")
      .then((s) => setActive(s.diagnostic.active && (s.diagnostic.active.style ?? "listening") === style ? s.diagnostic.active : null))
      .catch((e) => toast.error((e as Error).message));
  }, [style]);

  const loadNext = useCallback(async (id: string) => {
    try {
      const r = await api<NextOut>(`/v1/vocabulary/diagnostics/${id}/next`, { body: {} });
      setDiag(r.diagnostic);
      setItem(r.item);
    } catch (e) {
      toast.error((e as Error).message);
    }
  }, []);

  const start = async (replace: boolean) => {
    setBusy(true);
    try {
      const d = await api<Diagnostic>("/v1/vocabulary/diagnostics", { body: { replace, style } });
      setDiag(d);
      await loadNext(d.id);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const submit = async (body: { choice: number | null; confidence: string; ms: number }) => {
    if (!item) return null;
    try {
      return await api<WordFeedback>("/v1/vocabulary/answers", { body: { item_id: item.item_id, ...body } });
    } catch (e) {
      toast.error((e as Error).message);
      return null;
    }
  };

  const done = diag?.status === "completed" && diag.result;
  const answered = item?.progress?.answered ?? diag?.answered ?? 0;
  const max = diag?.max ?? (placement ? 30 : 40);
  const back = placement ? "/vocab" : "/analytics/vocabulary";

  return (
    <div className="mx-auto min-h-dvh max-w-3xl px-4 pb-16" style={{ paddingTop: "max(env(safe-area-inset-top), 1rem)" }}>
      <header className="mb-6 flex items-center gap-3">
        <Link href={back} aria-label="閉じる" className="inline-flex size-9 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted">
          <X className="size-5" />
        </Link>
        <div className="flex-1">
          <div className="text-sm font-medium">{placement ? "語彙の覚え直し診断" : "TOEIC 語彙診断"}</div>
          {diag && !done && (
            <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
              <motion.div className="h-full rounded-full bg-primary" animate={{ width: `${(answered / max) * 100}%` }} />
            </div>
          )}
        </div>
        {diag && !done && <span className="text-xs tabular text-muted-foreground">{answered} / {max}</span>}
      </header>

      {!diag && placement && (
        <motion.section initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="rounded-3xl border bg-card p-6">
          <h1 className="text-2xl font-semibold tracking-tight">覚え直す単語を、テストで選びます</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            約30問・8〜10分。まず英単語を見て意味を選びます（正解が続くと難しい単語へ）。文字で分かった単語の一部は、あとで音声だけ・例文の中でも確認します。
          </p>
          <ul className="mt-4 grid gap-2 text-sm sm:grid-cols-3">
            <li className="rounded-xl bg-muted/60 p-3">
              <div className="font-medium">1. 文字の語彙力</div>
              <div className="text-xs text-muted-foreground">英単語 → 日本語4択</div>
            </li>
            <li className="rounded-xl bg-muted/60 p-3">
              <div className="font-medium">2. 音声の語彙力</div>
              <div className="text-xs text-muted-foreground">単語の音声 → 日本語4択</div>
            </li>
            <li className="rounded-xl bg-muted/60 p-3">
              <div className="font-medium">3. 文脈での理解</div>
              <div className="text-xs text-muted-foreground">例文の音声 → 使われた語の意味</div>
            </li>
          </ul>
          <p className="mt-4 text-xs text-muted-foreground">
            知らない単語は「分からない」<Kbd>0</Kbd>を押してください（当てずっぽうの正解を減らすため）。回答時間も記録しますが、速さだけで判定はしません。30問では1,000語の一つひとつは判定できないので、この結果は語彙レベルの目安と最初の学習単語の選定に使い、各単語の状態はその後の学習で更新します。
          </p>
          <div className="mt-6 flex flex-wrap gap-2">
            {active === undefined ? (
              <Loader2 className="size-5 animate-spin text-muted-foreground" />
            ) : active ? (
              <>
                <Button size="lg" onClick={() => start(false)} disabled={busy}>
                  続きから再開（{active.answered} / {active.max}） <ArrowRight className="size-4" />
                </Button>
                <Button size="lg" variant="outline" onClick={() => start(true)} disabled={busy}>
                  <RotateCcw className="size-4" /> 最初からやり直す
                </Button>
              </>
            ) : (
              <Button size="lg" onClick={() => start(false)} disabled={busy}>
                診断を始める <ArrowRight className="size-4" />
              </Button>
            )}
          </div>
        </motion.section>
      )}

      {!diag && !placement && (
        <motion.section initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="rounded-3xl border bg-card p-6">
          <h1 className="text-2xl font-semibold tracking-tight">どの単語を、どの形で忘れているかを調べます</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            約40問・8分。答えに合わせて難易度が上下します。同じ単語でも「文字で分かる」「音だけで分かる」「文の中で聞き取れる」を別々に測ります。
          </p>
          <ul className="mt-4 grid gap-2 text-sm sm:grid-cols-3">
            <li className="rounded-xl bg-muted/60 p-3">
              <div className="font-medium">音声で認識</div>
              <div className="text-xs text-muted-foreground">単語の音だけを聞いて意味を選ぶ</div>
            </li>
            <li className="rounded-xl bg-muted/60 p-3">
              <div className="font-medium">会話で理解</div>
              <div className="text-xs text-muted-foreground">例文を聞いて、使われた語の意味を選ぶ</div>
            </li>
            <li className="rounded-xl bg-muted/60 p-3">
              <div className="font-medium">文字で理解</div>
              <div className="text-xs text-muted-foreground">聞き取れなかった語を、後で文字で確認</div>
            </li>
          </ul>
          <p className="mt-4 text-xs text-muted-foreground">
            自信がないときは「勘で答える」<Kbd>G</Kbd>、知らないときは「分からない」<Kbd>0</Kbd>。答え合わせは最後にまとめて表示します。途中でやめても、別の端末から続きを再開できます。
          </p>
          <div className="mt-6 flex flex-wrap gap-2">
            {active === undefined ? (
              <Loader2 className="size-5 animate-spin text-muted-foreground" />
            ) : active ? (
              <>
                <Button size="lg" onClick={() => start(false)} disabled={busy}>
                  続きから再開（{active.answered} / {active.max}） <ArrowRight className="size-4" />
                </Button>
                <Button size="lg" variant="outline" onClick={() => start(true)} disabled={busy}>
                  <RotateCcw className="size-4" /> 最初からやり直す
                </Button>
              </>
            ) : (
              <Button size="lg" onClick={() => start(false)} disabled={busy}>
                診断を始める <ArrowRight className="size-4" />
              </Button>
            )}
          </div>
        </motion.section>
      )}

      {diag && !done && item && (
        <WordQuestion key={item.item_id} item={item} rate={rate} submit={submit} next={() => loadNext(diag.id)} blind />
      )}
      {diag && !done && !item && (
        <div className="flex justify-center py-24 text-muted-foreground">
          <Loader2 className="size-6 animate-spin" />
        </div>
      )}

      {done && diag.result && (
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="space-y-5">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">診断結果</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              {placement
                ? "結果に合わせて、単語の学習で「意味から覚える」「音声で覚え直す」「例文で聞き取る」の順に出題します。"
                : "間違えた単語・勘で答えた単語は、単語の学習で先に覚え直し、そのあと FSRS の復習に入ります。"}
            </p>
          </div>
          {placement ? <PlacementResultView result={diag.result} /> : <DiagnosticResultView result={diag.result} />}
          <div className="flex flex-wrap gap-2">
            <Link href="/vocab" className={buttonVariants({ size: "lg" })}>
              単語の学習を始める <ArrowRight className="size-4" />
            </Link>
            <Link href="/analytics/vocabulary" className={cn(buttonVariants({ size: "lg", variant: "outline" }))}>
              語彙の分析を見る
            </Link>
          </div>
        </motion.div>
      )}
    </div>
  );
}
