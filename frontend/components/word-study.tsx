"use client";

import { motion } from "motion/react";
import { ArrowRight, Check, Volume2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { AudioPlayer, isTyping, Kbd, useAudio } from "@/components/audio-player";
import { Button } from "@/components/ui/button";
import { Highlight, playAudio, POS_LABEL } from "@/components/word-question";
import type { WordStudyFeedback, WordStudyItem } from "@/lib/api";
import { cn } from "@/lib/utils";

type Props = {
  item: WordStudyItem;
  rate: number;
  submit: (body: { recall: "yes" | "no"; ms: number }) => Promise<WordStudyFeedback | null>;
  next: () => void;
};

/** Re-learning one word: hear and see it, try to recall the meaning, then see meaning, scene and an example.
 *  The self-report is only a note; whether the word is known is decided by the listening checks that follow. */
export function WordStudyCard({ item, rate, submit, next }: Props) {
  const [fb, setFb] = useState<WordStudyFeedback | null>(null);
  const [busy, setBusy] = useState(false);
  // one player (the page shares a single audio element for iOS): the word first, then the example sentence
  const ctl = useAudio(fb?.example_audio ?? item.audio, { rate: fb ? rate : 1, autoplay: true, allowSlow: !!fb });
  const shownAt = useRef(0);
  useEffect(() => {
    shownAt.current = performance.now();
  }, [item.item_id]);

  const answer = async (recall: "yes" | "no") => {
    if (fb || busy) return;
    setBusy(true);
    const r = await submit({ recall, ms: Math.round(performance.now() - shownAt.current) });
    setBusy(false);
    if (r) setFb(r);
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (!fb && (e.key === "1" || e.key === "y")) answer("yes");
      else if (!fb && (e.key === "2" || e.key === "n")) answer("no");
      else if (fb && e.key === "Enter") {
        e.preventDefault();
        next();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  return (
    <div className="mx-auto max-w-2xl pb-28 sm:pb-0">
      <div className="mb-4 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span className="rounded-full bg-brand-soft px-2.5 py-1 font-medium text-primary">覚え直す</span>
        <span className="rounded-full border px-2 py-0.5">難易度 {item.tier}</span>
        {item.reason && <span className="ml-auto rounded-full bg-warning-soft px-2 py-0.5 text-foreground/80">{item.reason}</span>}
      </div>

      <div className="rounded-3xl border bg-card px-5 py-8 text-center">
        <div className="text-4xl font-semibold tracking-tight">{item.lemma}</div>
        <div className="mt-1 text-sm text-muted-foreground">{POS_LABEL[item.pos] ?? item.pos}</div>
        <button
          type="button"
          onClick={() => (fb ? playAudio(item.audio) : ctl.replay())}
          disabled={!item.audio}
          className="mx-auto mt-4 inline-flex h-11 items-center gap-2 rounded-full border px-4 text-sm hover:bg-muted disabled:opacity-40"
          aria-label="単語を再生"
        >
          <Volume2 className="size-4" /> 発音を聞く
        </button>
        {!fb && ctl.blocked && !ctl.playing && <p className="mt-2 text-xs text-muted-foreground">上のボタンで再生できます</p>}
      </div>

      {!fb ? (
        <p className="mt-5 text-center text-sm text-muted-foreground">{item.prompt.replace("「答えを見る」を押してください", "答えてください")}</p>
      ) : (
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="mt-4 space-y-3">
          <div className="rounded-2xl border bg-card p-4">
            <div className="text-xs text-muted-foreground">意味</div>
            <div className="mt-0.5 text-2xl font-semibold tracking-tight">{fb.meaning}</div>
            {fb.scene && <div className="mt-1 text-sm text-muted-foreground">よく使う場面: {fb.scene}</div>}
          </div>
          <div className="rounded-2xl border bg-card p-4">
            <div className="text-xs text-muted-foreground">例文（次に、音だけでこの文を聞いて確認します）</div>
            <p className="mt-1 text-base leading-relaxed">
              <Highlight text={fb.example} form={fb.form} />
            </p>
            {fb.example_ja && <p className="mt-1 text-sm text-muted-foreground">{fb.example_ja}</p>}
            {fb.example_audio && <AudioPlayer ctl={ctl} compact className="mt-3" label="例文" />}
          </div>
          <p className="text-xs text-muted-foreground">
            {fb.recall === "yes" ? "思い出せた単語も、音声と例文で確認してから「習得」になります。" : "このあと音声と例文で確認し、忘れやすい単語は FSRS が早めに復習に出します。"}
          </p>
        </motion.div>
      )}

      {/* the answer buttons sit at the bottom of the screen on phones (reachable with one thumb) */}
      <div className="fixed inset-x-0 bottom-0 z-20 border-t bg-background/95 px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] backdrop-blur sm:static sm:mt-5 sm:border-0 sm:bg-transparent sm:p-0">
        <div className="mx-auto max-w-2xl">
          {!fb ? (
            <div className="grid grid-cols-2 gap-2.5">
              <Button size="lg" variant="outline" className={cn("h-14 text-base")} disabled={busy} onClick={() => answer("no")}>
                <X className="size-4" /> 思い出せない <Kbd>2</Kbd>
              </Button>
              <Button size="lg" className="h-14 text-base" disabled={busy} onClick={() => answer("yes")}>
                <Check className="size-4" /> 思い出せた <Kbd>1</Kbd>
              </Button>
            </div>
          ) : (
            <Button size="lg" className="h-14 w-full text-base" onClick={next} autoFocus>
              次へ <ArrowRight className="size-4" /> <Kbd>Enter</Kbd>
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
