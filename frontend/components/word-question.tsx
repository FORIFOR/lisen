"use client";

import { motion } from "motion/react";
import { ArrowRight, Check, HelpCircle, Volume2, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { AudioPlayer, isTyping, Kbd, useAudio } from "@/components/audio-player";
import { Button } from "@/components/ui/button";
import { audioUrl, MODALITY_LABEL, type Mastery, type Modality, type WordFeedback, type WordItem } from "@/lib/api";
import { cn } from "@/lib/utils";

export const POS_LABEL: Record<string, string> = { n: "名詞", v: "動詞", adj: "形容詞", adv: "副詞", prep: "前置詞", conj: "接続詞", pron: "代名詞" };

export function playAudio(rel?: string | null) {
  if (rel) new Audio(audioUrl(rel)).play().catch(() => {});
}

export function MasteryChips({ mastery, className }: { mastery: Mastery; className?: string }) {
  return (
    <div className={cn("flex flex-wrap gap-1.5 text-xs", className)}>
      {(["text", "audio", "context"] as Modality[]).map((m) => {
        const v = mastery[m];
        return (
          <span
            key={m}
            className={cn(
              "rounded-full border px-2 py-0.5 tabular",
              v == null ? "text-muted-foreground" : v >= 0.8 ? "border-success/40 bg-success-soft" : v < 0.5 ? "border-destructive/30 bg-danger-soft" : "bg-warning-soft",
            )}
          >
            {MODALITY_LABEL[m]} {v == null ? "未測定" : `${Math.round(v * 100)}%`}
          </span>
        );
      })}
    </div>
  );
}

export function Highlight({ text, form }: { text: string; form?: string }) {
  if (!form) return <>{text}</>;
  const i = text.toLowerCase().indexOf(form.toLowerCase());
  if (i < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, i)}
      <mark className="rounded bg-brand-soft px-0.5 font-semibold text-primary">{text.slice(i, i + form.length)}</mark>
      {text.slice(i + form.length)}
    </>
  );
}

type Props = {
  item: WordItem;
  rate: number;
  submit: (body: { choice: number | null; confidence: string; ms: number }) => Promise<WordFeedback | null>;
  next: () => void;
  /** diagnostic: answers are recorded without showing the right answer */
  blind?: boolean;
};

/** One vocabulary question: read (text), hear the word (audio) or hear it in a sentence (context). */
export function WordQuestion({ item, rate, submit, next, blind }: Props) {
  const listen = item.modality !== "text";
  // an unseen sentence is a test of normal listening: 1.0x, no slow button until it has been answered
  const fixed = blind || !!item.transfer;
  const ctl = useAudio(listen ? item.audio : null, { rate: item.transfer ? 1 : rate, autoplay: listen && !blind, allowSlow: !fixed });
  const [fb, setFb] = useState<WordFeedback | null>(null);
  const [choice, setChoice] = useState<number | null | undefined>(undefined);
  const [guess, setGuess] = useState(false);
  const shownAt = useRef(0);
  useEffect(() => {
    shownAt.current = performance.now();
  }, [item.item_id]);

  const choose = async (i: number | null) => {
    if (fb || choice !== undefined) return;
    setChoice(i);
    const r = await submit({ choice: i, confidence: i === null ? "unknown" : guess ? "guess" : "sure", ms: Math.round(performance.now() - shownAt.current) });
    if (!r) return setChoice(undefined);
    if (blind) return next();
    setFb(r);
  };

  useEffect(() => {
    if (fb) return;
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e) || e.metaKey || e.ctrlKey || e.altKey) return;
      if (["1", "2", "3", "4"].includes(e.key)) choose(Number(e.key) - 1);
      else if (e.key === "0") choose(null);
      else if (e.key === "g" || e.key === "G") setGuess((g) => !g);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
  useEffect(() => {
    if (!fb) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Enter" && !isTyping(e)) {
        e.preventDefault();
        next();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [fb, next]);

  return (
    <div className="mx-auto max-w-2xl">
      <div className="mb-4 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        <span className="rounded-full bg-brand-soft px-2.5 py-1 font-medium text-primary">
          {item.transfer ? "初見の例文 · 1.0倍速" : `TOEIC 語彙 · ${item.modality_label}`}
        </span>
        <span className="rounded-full border px-2 py-0.5">難易度 {item.tier}</span>
        {item.reason && <span className="ml-auto rounded-full bg-warning-soft px-2 py-0.5 text-foreground/80">{item.reason}</span>}
      </div>

      {item.modality === "text" ? (
        <div className="rounded-2xl border bg-card px-5 py-8 text-center">
          <div className="text-4xl font-semibold tracking-tight">{item.lemma}</div>
          {item.pos && <div className="mt-1 text-sm text-muted-foreground">{POS_LABEL[item.pos] ?? item.pos}</div>}
        </div>
      ) : (
        <AudioPlayer
          ctl={ctl}
          allowSlow={!fixed}
          label={item.modality === "audio" ? "単語の音声（文字は表示されません）" : item.transfer ? "初めて聞く文（文字は表示されません）" : "例文の音声（文字は表示されません）"}
        />
      )}

      <p className="mt-5 text-sm font-medium">{item.prompt}</p>
      <div className="mt-3 grid gap-2.5 sm:grid-cols-2">
        {item.choices.map((c, i) => {
          const isAns = fb && fb.answer === i;
          const picked = choice === i;
          return (
            <button
              key={i}
              type="button"
              disabled={!!fb || choice !== undefined}
              onClick={() => choose(i)}
              className={cn(
                "flex min-h-14 items-center gap-3 rounded-2xl border bg-card px-4 py-3 text-left transition-colors",
                !fb && choice === undefined && "hover:bg-muted",
                !fb && picked && "border-primary bg-brand-soft",
                fb && isAns && "border-success bg-success-soft",
                fb && picked && !isAns && "border-destructive bg-danger-soft",
              )}
            >
              <span className="flex size-7 shrink-0 items-center justify-center rounded-md border text-sm tabular">{i + 1}</span>
              <span className="flex-1">{c}</span>
              {fb && isAns && <Check className="size-4 text-success" />}
              {fb && picked && !isAns && <X className="size-4 text-destructive" />}
            </button>
          );
        })}
      </div>

      {!fb && (
        <div className="mt-3 flex flex-wrap items-center justify-between gap-2">
          <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg px-1 py-1 text-sm text-muted-foreground select-none">
            <input type="checkbox" className="size-4 accent-[var(--primary)]" checked={guess} onChange={(e) => setGuess(e.target.checked)} />
            自信がない（勘で答える） <Kbd>G</Kbd>
          </label>
          <Button variant="ghost" onClick={() => choose(null)} disabled={choice !== undefined}>
            <HelpCircle className="size-4" /> 分からない <Kbd>0</Kbd>
          </Button>
        </div>
      )}

      {fb && (
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="mt-4 space-y-3">
          <div
            className={cn(
              "flex items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium",
              fb.correct ? "bg-success-soft text-success" : "bg-danger-soft text-destructive",
            )}
          >
            {fb.correct ? <Check className="size-4" /> : <X className="size-4" />}
            {fb.correct ? (fb.confidence === "guess" ? "正解（勘だったので、もう一度出題します）" : "正解") : fb.choice == null ? "答えを確認しましょう" : "不正解"}
          </div>
          <div className="rounded-2xl border bg-card p-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="text-2xl font-semibold tracking-tight">
                  {fb.lemma}
                  {fb.pos && <span className="ml-2 align-middle text-xs font-normal text-muted-foreground">{POS_LABEL[fb.pos] ?? fb.pos}</span>}
                </div>
                <div className="mt-0.5 text-muted-foreground">{fb.meaning}</div>
              </div>
              <Button variant="outline" size="sm" onClick={() => playAudio(fb.word_audio)} aria-label="単語を再生">
                <Volume2 className="size-4" /> 単語
              </Button>
            </div>
            {fb.scene && <p className="mt-1 text-xs text-muted-foreground">よく使う場面: {fb.scene}</p>}
            {fb.sentence === "transfer" && fb.transfer ? (
              <button type="button" onClick={() => playAudio(fb.transfer_audio)} className="mt-3 flex w-full items-start gap-2 rounded-xl bg-muted/60 px-3 py-2 text-left text-sm hover:bg-muted">
                <Volume2 className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                <span>
                  <span className="block text-[11px] text-muted-foreground">いま聞いた文</span>
                  <Highlight text={fb.transfer} form={fb.transfer_form ?? fb.lemma} />
                  {fb.transfer_ja && <span className="mt-0.5 block text-xs text-muted-foreground">{fb.transfer_ja}</span>}
                </span>
              </button>
            ) : (
              fb.example && (
                <button type="button" onClick={() => playAudio(fb.example_audio)} className="mt-3 flex w-full items-start gap-2 rounded-xl bg-muted/60 px-3 py-2 text-left text-sm hover:bg-muted">
                  <Volume2 className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                  <span>
                    <Highlight text={fb.example} form={fb.form} />
                    {fb.example_ja && <span className="mt-0.5 block text-xs text-muted-foreground">{fb.example_ja}</span>}
                  </span>
                </button>
              )
            )}
            {fb.mastery && <MasteryChips mastery={fb.mastery} className="mt-3" />}
            {fb.status_label && <p className="mt-2 text-xs text-muted-foreground">この単語の状態: {fb.status_label}</p>}
          </div>
          <div className="sticky bottom-0 -mx-4 flex items-center justify-between gap-3 border-t bg-background/95 px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] backdrop-blur sm:static sm:mx-0 sm:border-0 sm:bg-transparent sm:p-0">
            <SrsLine srs={fb.srs} />
            <Button size="lg" className="h-12 px-8" onClick={next} autoFocus>
              次へ <ArrowRight className="size-4" /> <Kbd>Enter</Kbd>
            </Button>
          </div>
        </motion.div>
      )}
    </div>
  );
}

function SrsLine({ srs }: { srs?: { due: string } | null }) {
  const [now] = useState(() => Date.now());
  if (!srs) return <span className="text-xs text-muted-foreground">覚えている単語です（復習カードは作りません）</span>;
  const mins = Math.round((new Date(srs.due).getTime() - now) / 60000);
  const when = mins < 60 ? `${Math.max(1, mins)}分後` : mins < 1440 ? `${Math.round(mins / 60)}時間後` : `${Math.round(mins / 1440)}日後`;
  return <span className="text-xs text-muted-foreground">次の復習: {when}（FSRS）</span>;
}
