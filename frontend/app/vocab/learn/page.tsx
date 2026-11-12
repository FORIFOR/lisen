"use client";

import Link from "next/link";
import { motion } from "motion/react";
import { ArrowRight, Loader2, Sparkles, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { buttonVariants } from "@/components/ui/button";
import { WordQuestion } from "@/components/word-question";
import { WordStudyCard } from "@/components/word-study";
import { api, type Profile, type VocabLesson, type WordFeedback, type WordItem, type WordStudyFeedback, type WordStudyItem } from "@/lib/api";
import { cn } from "@/lib/utils";

type NextOut = { lesson: VocabLesson; item: WordItem | WordStudyItem | null; done: boolean };

/** A 5-30 minute word lesson; the server decides the order and keeps the state, so it resumes on any device. */
export default function VocabLearnPage() {
  const [rate, setRate] = useState(1);
  const [lesson, setLesson] = useState<VocabLesson | null>(null);
  const [item, setItem] = useState<WordItem | WordStudyItem | null>(null);
  const [done, setDone] = useState(false);
  const loading = useRef(false);

  const loadNext = useCallback(async (id: string) => {
    if (loading.current) return;      // a double tap on "next" must not skip an item
    loading.current = true;
    try {
      const r = await api<NextOut>(`/v1/vocabulary/lessons/${id}/next`, { body: {} });
      setLesson(r.lesson);
      setItem(r.item);
      setDone(r.done);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      loading.current = false;
    }
  }, []);

  useEffect(() => {
    api<Profile>("/profile").then((p) => setRate(p.settings.playback_rate)).catch(() => {});
    const q = new URLSearchParams(window.location.search);
    const id = q.get("id");
    const m = Number(q.get("m") ?? 10);
    (async () => {
      try {
        const l = id
          ? await api<VocabLesson>(`/v1/vocabulary/lessons/${id}`)
          : await api<VocabLesson>("/v1/vocabulary/lessons", { body: { minutes: [5, 10, 15, 30].includes(m) ? m : 10, replace: q.get("new") === "1" } });
        // keep the lesson id in the address so a reload resumes the same lesson
        window.history.replaceState(null, "", `/vocab/learn?id=${l.id}`);
        setLesson(l);
        await loadNext(l.id);
      } catch (e) {
        toast.error((e as Error).message);
      }
    })();
  }, [loadNext]);

  const send = async <T,>(body: Record<string, unknown>): Promise<T | null> => {
    if (!item) return null;
    try {
      const fb = await api<T & { lesson?: VocabLesson }>("/v1/vocabulary/answers", { body: { item_id: item.item_id, ...body } });
      if (fb.lesson) setLesson(fb.lesson);
      return fb;
    } catch (e) {
      toast.error((e as Error).message);
      return null;
    }
  };

  const spent = item?.lesson?.spent_s ?? lesson?.spent_s ?? 0;
  const budget = lesson?.budget_s ?? 600;
  const result = lesson?.result;

  return (
    <div className="mx-auto min-h-dvh max-w-3xl px-4 pb-16" style={{ paddingTop: "max(env(safe-area-inset-top), 1rem)" }}>
      <header className="mb-6 flex items-center gap-3">
        <Link href="/vocab" aria-label="閉じる" className="inline-flex size-9 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted">
          <X className="size-5" />
        </Link>
        <div className="flex-1">
          <div className="text-sm font-medium">単語の学習{lesson ? ` · ${lesson.minutes}分` : ""}</div>
          {lesson && !done && (
            <div className="mt-1 h-1.5 overflow-hidden rounded-full bg-muted">
              <motion.div className="h-full rounded-full bg-primary" animate={{ width: `${Math.min(100, (spent / budget) * 100)}%` }} />
            </div>
          )}
        </div>
        {lesson && !done && <span className="text-xs tabular text-muted-foreground">{lesson.studied}語</span>}
      </header>

      {!done && !item && (
        <div className="flex justify-center py-24 text-muted-foreground">
          <Loader2 className="size-6 animate-spin" />
        </div>
      )}

      {!done && item?.type === "word_study" && (
        <WordStudyCard
          key={item.item_id}
          item={item}
          rate={rate}
          submit={(b) => send<WordStudyFeedback>(b)}
          next={() => lesson && loadNext(lesson.id)}
        />
      )}
      {!done && item?.type === "word" && (
        <WordQuestion key={item.item_id} item={item} rate={rate} submit={(b) => send<WordFeedback>(b)} next={() => lesson && loadNext(lesson.id)} />
      )}

      {done && (
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="space-y-4">
          <div className="rounded-3xl border bg-card p-6 text-center">
            <Sparkles className="mx-auto size-8 text-success" />
            <h1 className="mt-2 text-xl font-semibold">おつかれさまでした</h1>
            <p className="mt-1 text-sm text-muted-foreground tabular">
              {result ? `${Math.max(1, Math.round(result.spent_s / 60))}分 · ${result.studied.length}語を学習 · 復習 ${result.reviews}問` : ""}
            </p>
          </div>
          {result && (
            <>
              <section className="rounded-2xl border bg-card p-4">
                <h2 className="text-sm font-medium">成績（別々に集計）</h2>
                <ul className="mt-2 divide-y text-sm">
                  {(["first", "transfer", "review"] as const).map((p) => {
                    const v = result.phases[p];
                    return (
                      <li key={p} className="flex justify-between py-2">
                        <span>{result.phase_labels[p]}</span>
                        <span className="tabular text-muted-foreground">{v ? `${Math.round(v.accuracy * 100)}%（${v.n}問）` : "—"}</span>
                      </li>
                    );
                  })}
                </ul>
                {result.leftover > 0 && <p className="mt-2 text-xs text-muted-foreground">確認が {result.leftover}問 残っています。次の学習の最初に出します。</p>}
              </section>
              {result.studied.length > 0 && (
                <section className="rounded-2xl border bg-card p-4">
                  <h2 className="text-sm font-medium">今日の単語</h2>
                  <ul className="mt-2 divide-y text-sm">
                    {result.studied.map((w) => (
                      <li key={w.id} className="flex items-center gap-3 py-2">
                        <div className="min-w-0 flex-1">
                          <span className="font-medium">{w.lemma}</span> <span className="text-muted-foreground">{w.meaning}</span>
                        </div>
                        <span className="shrink-0 text-xs text-muted-foreground">{w.status_label.replace(/（.*）/, "")}</span>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
            </>
          )}
          <div className="flex flex-col gap-2 sm:flex-row">
            <Link href={`/vocab/learn?m=${lesson?.minutes ?? 10}&new=1`} className={cn(buttonVariants({ size: "lg" }), "h-12 flex-1")}>
              もう一度 {lesson?.minutes ?? 10}分 <ArrowRight className="size-4" />
            </Link>
            <Link href="/vocab" className={cn(buttonVariants({ size: "lg", variant: "outline" }), "h-12")}>
              単語のトップへ
            </Link>
          </div>
        </motion.div>
      )}
    </div>
  );
}
