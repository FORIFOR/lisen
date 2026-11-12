"use client";

import Link from "next/link";
import { motion } from "motion/react";
import { ArrowRight, BookPlus, Check, ExternalLink, HelpCircle, Loader2, Sparkles, Volume2, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { AudioPlayer, useAudio } from "@/components/audio-player";
import { Button, buttonVariants } from "@/components/ui/button";
import { Highlight, playAudio, POS_LABEL } from "@/components/word-question";
import { api, NEWS_LEVEL_LABEL, type NewsLesson, type NewsLevel, type NewsTransfer, type NewsTransferFb, type NewsWordAdded } from "@/lib/api";
import { published } from "@/lib/news";
import { cn } from "@/lib/utils";

type Step = "listen" | "questions" | "read" | "unseen" | "done";

function stepFor(l: NewsLesson): Step {
  if (!l.first_done) return l.first_answered.length ? "questions" : "listen";
  const t = l.transfer ?? [];
  return t.length && t.some((x) => !x.answered) ? "read" : "done";
}

function Choices({ choices, picked, answer, onPick, disabled }: { choices: string[]; picked?: number | null; answer?: number; onPick: (i: number | null) => void; disabled?: boolean }) {
  const shown = answer !== undefined;
  return (
    <div className="grid gap-2">
      {choices.map((c, i) => (
        <button
          key={i}
          type="button"
          disabled={disabled || shown}
          onClick={() => onPick(i)}
          className={cn(
            "flex min-h-12 items-center gap-3 rounded-xl border px-4 py-2.5 text-left text-[15px] transition-colors",
            !shown && "hover:bg-muted active:bg-muted",
            shown && i === answer && "border-success/50 bg-success-soft",
            shown && picked === i && i !== answer && "border-destructive/40 bg-danger-soft",
          )}
        >
          <span className="tabular text-xs text-muted-foreground">{i + 1}</span>
          <span className="flex-1">{c}</span>
          {shown && i === answer && <Check className="size-4 text-success" />}
        </button>
      ))}
      {!shown && (
        <button type="button" disabled={disabled} onClick={() => onPick(null)} className="flex h-11 items-center justify-center gap-1.5 rounded-xl text-sm text-muted-foreground hover:bg-muted">
          <HelpCircle className="size-4" /> 分からない
        </button>
      )}
    </div>
  );
}

function Listen({ lesson, onNext, onLevel }: { lesson: NewsLesson; onNext: () => void; onLevel: (l: NewsLevel) => void }) {
  const ctl = useAudio(lesson.audio, { rate: 1, allowSlow: false });
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="レベル">
        {(["A2", "B1", "B2"] as NewsLevel[]).map((l) => {
          const off = lesson.unavailable?.[l];
          return (
            <button
              key={l}
              type="button"
              role="radio"
              aria-checked={lesson.level === l}
              aria-disabled={!!off}
              disabled={!!off}
              onClick={() => onLevel(l)}
              title={off ?? undefined}
              className={cn(
                "h-10 rounded-xl border px-3 text-sm",
                lesson.level === l ? "border-primary bg-brand-soft font-medium text-primary" : off ? "cursor-not-allowed border-dashed text-muted-foreground/60" : "hover:bg-muted",
              )}
            >
              {NEWS_LEVEL_LABEL[l]} <span className="text-xs text-muted-foreground">{off ? `· ${off}` : l}</span>
            </button>
          );
        })}
      </div>
      <div className="rounded-3xl border bg-card p-5">
        <div className="text-xs text-muted-foreground">1. まず字幕なしで聞いてください（約{Math.max(1, Math.round(lesson.seconds / 60))}分）</div>
        <div className="mt-1 text-lg font-semibold leading-snug">{lesson.title_en}</div>
        <AudioPlayer ctl={ctl} allowSlow={false} className="mt-4" label="ニュース（等速）" />
        <p className="mt-3 text-xs text-muted-foreground">何度聞いても構いません。英文と訳は、内容の問題に答えたあとに表示します。</p>
      </div>
      <Button size="lg" className="h-14 w-full text-base" onClick={onNext}>
        内容の問題へ（3問） <ArrowRight className="size-4" />
      </Button>
    </div>
  );
}

function Questions({ lesson, onDone }: { lesson: NewsLesson; onDone: (l: NewsLesson) => void }) {
  const ctl = useAudio(lesson.audio, { rate: 1, allowSlow: false });
  const [idx, setIdx] = useState(() => lesson.questions.findIndex((_, i) => !lesson.first_answered.includes(i)));
  const [busy, setBusy] = useState(false);
  const started = useRef(0);
  useEffect(() => {
    started.current = performance.now();
  }, [idx]);
  const q = lesson.questions[idx];

  const pick = async (choice: number | null) => {
    if (busy) return;
    setBusy(true);
    try {
      const r = await api<{ done: boolean; lesson?: NewsLesson }>(`/v1/news/${lesson.id}/attempt`, {
        body: { level: lesson.level, phase: "first", index: idx, choice, ms: Math.round(performance.now() - started.current) },
      });
      if (r.done && r.lesson) onDone(r.lesson);
      else {
        const next = lesson.questions.findIndex((_, i) => i > idx && !lesson.first_answered.includes(i));
        setIdx(next >= 0 ? next : idx + 1);
      }
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  if (!q) return null;
  return (
    <div className="space-y-4">
      <AudioPlayer ctl={ctl} compact allowSlow={false} label="もう一度聞く" />
      <div className="rounded-3xl border bg-card p-5">
        <div className="text-xs text-muted-foreground tabular">2. 内容の問題 {idx + 1} / {lesson.questions.length}（答えは3問のあとに表示）</div>
        <div className="mt-2 text-base font-medium">{q.q}</div>
        <div className="mt-4">
          <Choices key={idx} choices={q.choices} onPick={pick} disabled={busy} />
        </div>
      </div>
    </div>
  );
}

function Read({ lesson, onNext, setLesson }: { lesson: NewsLesson; onNext: () => void; setLesson: (l: NewsLesson) => void }) {
  const [adding, setAdding] = useState<string | null>(null);
  const vocab = lesson.vocabulary ?? [];
  const add = async (lemma: string) => {
    setAdding(lemma);
    try {
      const r = await api<NewsWordAdded>(`/v1/news/${lesson.id}/vocabulary`, { body: { lemma } });
      toast.success(`${lemma}: ${r.message}`);
      setLesson({ ...lesson, vocabulary: vocab.map((v) => (v.lemma === lemma ? { ...v, added: true } : v)) });
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setAdding(null);
    }
  };
  const right = (lesson.results ?? []).filter((r) => r.correct).length;
  return (
    <div className="space-y-4">
      <section className="rounded-2xl border bg-card p-4">
        <div className="flex items-baseline justify-between">
          <h2 className="text-sm font-medium">内容の問題</h2>
          <span className="text-sm tabular">{right} / {lesson.results?.length ?? 0} 問正解</span>
        </div>
        <ul className="mt-2 space-y-3">
          {(lesson.results ?? []).map((r, i) => (
            <li key={i} className="text-sm">
              <div className="flex gap-2">
                {r.correct ? <Check className="mt-0.5 size-4 shrink-0 text-success" /> : <X className="mt-0.5 size-4 shrink-0 text-destructive" />}
                <span className="font-medium">{r.q}</span>
              </div>
              <div className="ml-6 text-muted-foreground">
                正解: {r.choices[r.answer]}
                {!r.correct && ` · あなたの答え: ${r.choice == null ? "分からない" : r.choices[r.choice]}`}
              </div>
              {r.explanation && <div className="ml-6 text-xs text-muted-foreground">{r.explanation}</div>}
            </li>
          ))}
        </ul>
      </section>

      <section className="rounded-2xl border bg-card p-4">
        <h2 className="text-sm font-medium">3. 英文と訳</h2>
        {lesson.summary_ja && <p className="mt-1 text-sm text-muted-foreground">{lesson.title_ja} — {lesson.summary_ja}</p>}
        <ol className="mt-3 space-y-3">
          {(lesson.sentences ?? []).map((s, i) => {
            const v = vocab.find((x) => x.sentence === i);
            return (
              <li key={i}>
                <button type="button" onClick={() => playAudio(s.audio)} className="flex w-full items-start gap-2 rounded-xl px-2 py-1.5 text-left hover:bg-muted">
                  <span className="mt-0.5 w-4 shrink-0 text-right text-xs tabular text-muted-foreground">{i + 1}</span>
                  <Volume2 className="mt-1 size-4 shrink-0 text-muted-foreground" />
                  <span className="flex-1">
                    <span className="block text-[15px] leading-relaxed"><Highlight text={s.en} form={v?.form} /></span>
                    <span className="block text-sm text-muted-foreground">{s.ja}</span>
                  </span>
                </button>
              </li>
            );
          })}
        </ol>
      </section>

      {vocab.length > 0 && (
        <section className="rounded-2xl border bg-card p-4">
          <h2 className="text-sm font-medium">このニュースの単語</h2>
          <p className="mt-0.5 text-xs text-muted-foreground">知らなかった単語は単語帳に追加すると、次の単語の学習で最初に出します。</p>
          <ul className="mt-2 divide-y">
            {vocab.map((v) => (
              <li key={v.lemma} className="flex items-center gap-3 py-2.5">
                <div className="min-w-0 flex-1">
                  <div className="text-[15px] font-medium">
                    {v.lemma} <span className="text-xs font-normal text-muted-foreground">{POS_LABEL[v.pos] ?? v.pos}</span>
                  </div>
                  <div className="text-sm text-muted-foreground">{v.meaning}</div>
                </div>
                <Button size="sm" variant={v.added ? "ghost" : "outline"} disabled={v.added || adding === v.lemma} onClick={() => add(v.lemma)} className="h-10">
                  {v.added ? <><Check className="size-4" /> 追加済み</> : <><BookPlus className="size-4" /> 単語帳へ</>}
                </Button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <Button size="lg" className="h-14 w-full text-base" onClick={onNext}>
        {(lesson.transfer ?? []).some((t) => !t.answered) ? "4. 別の文で確認へ" : "結果を見る"} <ArrowRight className="size-4" />
      </Button>
    </div>
  );
}

function Unseen({ lesson, onDone }: { lesson: NewsLesson; onDone: (t: NewsTransfer[]) => void }) {
  const [items, setItems] = useState<NewsTransfer[]>(lesson.transfer ?? []);
  const [i, setI] = useState(() => Math.max(0, items.findIndex((t) => !t.answered)));
  const [busy, setBusy] = useState(false);
  const [added, setAdded] = useState<string | null>(null);
  const t = items[i];
  const ctl = useAudio(t?.audio, { rate: 1, autoplay: true, allowSlow: false });
  const started = useRef(0);
  useEffect(() => {
    started.current = performance.now();
  }, [i]);

  const pick = async (choice: number | null) => {
    if (busy || !t || t.answered) return;
    setBusy(true);
    try {
      const fb = await api<NewsTransferFb & { added: NewsWordAdded | null }>(`/v1/news/${lesson.id}/attempt`, {
        body: { level: lesson.level, phase: "transfer", index: t.index, choice, ms: Math.round(performance.now() - started.current) },
      });
      setItems((xs) => xs.map((x, k) => (k === i ? { ...x, answered: fb } : x)));
      setAdded(fb.added ? fb.added.message : null);
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  const next = () => {
    setAdded(null);
    if (i + 1 < items.length) setI(i + 1);
    else onDone(items);
  };
  if (!t) return null;
  const fb = t.answered;
  return (
    <div className="space-y-4 pb-24 sm:pb-0">
      <div className="rounded-3xl border bg-card p-5">
        <div className="text-xs text-muted-foreground tabular">4. 初めて聞く文で確認 {i + 1} / {items.length}（別の場面・別の声・等速）</div>
        <AudioPlayer ctl={ctl} compact allowSlow={false} className="mt-3" label="英文" />
        <div className="mt-4 text-base font-medium">この文の「{t.lemma}」の意味は？</div>
        <div className="mt-3">
          <Choices key={i} choices={t.choices} picked={fb?.choice} answer={fb?.answer} onPick={pick} disabled={busy} />
        </div>
        {fb && (
          <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="mt-4 rounded-xl bg-muted/60 p-3 text-sm">
            <div className="leading-relaxed"><Highlight text={fb.en} form={fb.form} /></div>
            <div className="text-muted-foreground">{fb.ja}</div>
            {fb.choice == null && <div className="mt-1 text-xs text-muted-foreground">「分からない」を選びました（正解に印を付けています）</div>}
            {added && <div className="mt-1 text-xs text-primary">{added}</div>}
          </motion.div>
        )}
      </div>
      {fb && (
        <div className="fixed inset-x-0 bottom-0 z-20 border-t bg-background/95 px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] backdrop-blur sm:static sm:border-0 sm:bg-transparent sm:p-0">
          <Button size="lg" className="mx-auto flex h-14 w-full max-w-3xl text-base" onClick={next} autoFocus>
            {i + 1 < items.length ? "次へ" : "結果を見る"} <ArrowRight className="size-4" />
          </Button>
        </div>
      )}
    </div>
  );
}

function Done({ lesson }: { lesson: NewsLesson }) {
  const first = lesson.results ?? [];
  const tr = (lesson.transfer ?? []).filter((t) => t.answered);
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="space-y-4">
      <div className="rounded-3xl border bg-card p-6 text-center">
        <Sparkles className="mx-auto size-8 text-success" />
        <h1 className="mt-2 text-xl font-semibold">おつかれさまでした</h1>
        <p className="mt-1 text-sm text-muted-foreground">{lesson.title_ja}</p>
      </div>
      <section className="rounded-2xl border bg-card p-4 text-sm">
        <h2 className="font-medium">成績（別々に集計）</h2>
        <ul className="mt-2 divide-y">
          <li className="flex justify-between py-2"><span>初めて聞いたときの理解</span><span className="tabular text-muted-foreground">{first.filter((r) => r.correct).length} / {first.length}</span></li>
          <li className="flex justify-between py-2"><span>別の文での確認</span><span className="tabular text-muted-foreground">{tr.length ? `${tr.filter((t) => t.answered!.correct).length} / ${tr.length}` : "—"}</span></li>
        </ul>
        <p className="mt-1 text-xs text-muted-foreground">別の文で間違えた単語は単語帳に追加し、次の単語の学習で最初に出します。</p>
      </section>
      <div className="flex flex-col gap-2 sm:flex-row">
        <Link href="/news" className={cn(buttonVariants({ size: "lg" }), "h-12 shrink-0 sm:flex-1")}>ほかのニュース</Link>
        <Link href="/vocab" className={cn(buttonVariants({ size: "lg", variant: "outline" }), "h-12 shrink-0 sm:flex-1")}>単語の学習へ</Link>
      </div>
    </motion.div>
  );
}

/** One news lesson: listen (no text) -> 3 questions -> text, translation and words -> unseen sentences. */
export default function NewsLessonPage() {
  const [lesson, setLesson] = useState<NewsLesson | null>(null);
  const [step, setStep] = useState<Step>("listen");

  const load = useCallback(async (id: string, level?: string | null) => {
    try {
      const l = await api<NewsLesson>(`/v1/news/${id}${level ? `?level=${level}` : ""}`);
      setLesson(l);
      setStep(stepFor(l));
      window.history.replaceState(null, "", `/news/lesson?id=${l.id}&level=${l.level}`);
    } catch (e) {
      toast.error((e as Error).message);
    }
  }, []);

  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    const id = q.get("id");
    // eslint-disable-next-line react-hooks/set-state-in-effect -- load the lesson named in the address once on mount
    if (id) load(id, q.get("level"));
  }, [load]);

  const p = lesson ? published(lesson.source.published_at) : null;
  return (
    <div className="mx-auto min-h-dvh max-w-3xl px-4 pb-16" style={{ paddingTop: "max(env(safe-area-inset-top), 1rem)" }}>
      <header className="mb-5 flex items-center gap-3">
        <Link href="/news" aria-label="閉じる" className="inline-flex size-9 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted">
          <X className="size-5" />
        </Link>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium">今日のニュース{lesson ? ` · ${NEWS_LEVEL_LABEL[lesson.level]}` : ""}</div>
          {lesson && p && (
            <div className="truncate text-[11px] text-muted-foreground">
              {lesson.source.publisher}（{lesson.source.kind_label}） · <span className={cn(p.old && "rounded bg-warning-soft px-1 text-foreground")}>{p.text}</span>
              {lesson.corroborated.length > 0 && ` · ${lesson.corroborated.join("・")} も報道`}
            </div>
          )}
        </div>
      </header>

      {!lesson && (
        <div className="flex justify-center py-24 text-muted-foreground">
          <Loader2 className="size-6 animate-spin" />
        </div>
      )}
      {lesson && step === "listen" && <Listen lesson={lesson} onNext={() => setStep("questions")} onLevel={(l) => load(lesson.id, l)} />}
      {lesson && step === "questions" && (
        <Questions
          lesson={lesson}
          onDone={(l) => {
            setLesson(l);
            setStep("read");
          }}
        />
      )}
      {lesson && step === "read" && <Read lesson={lesson} setLesson={setLesson} onNext={() => setStep((lesson.transfer ?? []).some((t) => !t.answered) ? "unseen" : "done")} />}
      {lesson && step === "unseen" && (
        <Unseen
          lesson={lesson}
          onDone={(t) => {
            setLesson({ ...lesson, transfer: t });
            setStep("done");
          }}
        />
      )}
      {lesson && step === "done" && <Done lesson={lesson} />}

      {lesson && step !== "listen" && step !== "questions" && (
        <a href={lesson.source.url} target="_blank" rel="noreferrer" className="mt-6 inline-flex items-center gap-1 text-xs text-muted-foreground underline-offset-4 hover:underline">
          <ExternalLink className="size-3.5" /> 元記事: {lesson.source.title}（{lesson.source.publisher}）
        </a>
      )}
    </div>
  );
}
