"use client";

import Link from "next/link";
import { motion } from "motion/react";
import { ArrowRight, Loader2, Sparkles, X } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { buttonVariants } from "@/components/ui/button";
import { WordQuestion } from "@/components/word-question";
import { api, type Profile, type WordFeedback, type WordItem } from "@/lib/api";
import { cn } from "@/lib/utils";

/** Stand-alone word practice. review = only FSRS-due words; practice = due + next-step + new words. */
export function VocabRunner({ mode }: { mode: "review" | "practice" }) {
  const [rate, setRate] = useState(1);
  const [items, setItems] = useState<WordItem[] | null>(null);
  const [idx, setIdx] = useState(0);
  const [results, setResults] = useState<boolean[]>([]);

  const load = useCallback(async () => {
    try {
      const r = await api<{ due: number; items: WordItem[] }>(`/v1/vocabulary/reviews?limit=${mode === "review" ? 20 : 12}&include_new=${mode === "practice"}`);
      setItems(r.items);
      setIdx(0);
      setResults([]);
    } catch (e) {
      toast.error((e as Error).message);
    }
  }, [mode]);

  useEffect(() => {
    api<Profile>("/profile").then((p) => setRate(p.settings.playback_rate)).catch(() => {});
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch
    load();
  }, [load]);

  const item = items?.[idx];
  const submit = async (body: { choice: number | null; confidence: string; ms: number }) => {
    if (!item) return null;
    try {
      const fb = await api<WordFeedback>("/v1/vocabulary/reviews", { body: { item_id: item.item_id, ...body } });
      setResults((r) => [...r, !!fb.correct && fb.confidence !== "guess"]);
      return fb;
    } catch (e) {
      toast.error((e as Error).message);
      return null;
    }
  };

  const title = mode === "review" ? "単語の復習" : "単語トレーニング";
  const finished = items && idx >= items.length;

  return (
    <div className="mx-auto min-h-dvh max-w-3xl px-4 pb-16" style={{ paddingTop: "max(env(safe-area-inset-top), 1rem)" }}>
      <header className="mb-6 flex items-center gap-3">
        <Link href="/analytics/vocabulary" aria-label="閉じる" className="inline-flex size-9 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted">
          <X className="size-5" />
        </Link>
        <div className="flex-1">
          <div className="text-sm font-medium">{title}</div>
          {items && items.length > 0 && (
            <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
              <motion.div className="h-full rounded-full bg-primary" animate={{ width: `${(Math.min(idx, items.length) / items.length) * 100}%` }} />
            </div>
          )}
        </div>
        {items && items.length > 0 && !finished && <span className="text-xs tabular text-muted-foreground">{idx + 1} / {items.length}</span>}
      </header>

      {!items && (
        <div className="flex justify-center py-24 text-muted-foreground">
          <Loader2 className="size-6 animate-spin" />
        </div>
      )}

      {items && items.length === 0 && (
        <div className="rounded-3xl border bg-card p-6 text-center">
          <Sparkles className="mx-auto size-8 text-success" />
          <h1 className="mt-2 text-xl font-semibold">今日復習する単語はありません</h1>
          <p className="mt-1 text-sm text-muted-foreground">FSRS が次の復習日を決めています。新しい単語に進むこともできます。</p>
          <div className="mt-5 flex flex-wrap justify-center gap-2">
            {mode === "review" && (
              <Link href="/vocab/practice" className={buttonVariants({ size: "lg" })}>
                単語トレーニング <ArrowRight className="size-4" />
              </Link>
            )}
            <Link href="/analytics/vocabulary" className={cn(buttonVariants({ size: "lg", variant: "outline" }))}>
              語彙の分析へ
            </Link>
          </div>
        </div>
      )}

      {item && <WordQuestion key={item.item_id} item={item} rate={rate} submit={submit} next={() => setIdx((i) => i + 1)} />}

      {finished && items.length > 0 && (
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="rounded-3xl border bg-card p-6 text-center">
          <Sparkles className="mx-auto size-8 text-success" />
          <h1 className="mt-2 text-xl font-semibold">おつかれさまでした</h1>
          <p className="mt-1 text-muted-foreground tabular">
            {results.length}問中 {results.filter(Boolean).length}問 確実に正解
          </p>
          <div className="mt-5 flex flex-wrap justify-center gap-2">
            <button type="button" onClick={load} className={buttonVariants({ size: "lg" })}>
              続けて学習する
            </button>
            <Link href="/analytics/vocabulary" className={cn(buttonVariants({ size: "lg", variant: "outline" }))}>
              語彙の分析へ
            </Link>
          </div>
        </motion.div>
      )}
    </div>
  );
}
