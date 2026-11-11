"use client";

import { motion } from "motion/react";
import { BookOpen, Ear, Lightbulb, MapPin, Repeat2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { AudioPlayer, isTyping, useAudio } from "@/components/audio-player";
import { ERROR_TIPS, Header, NextButton, type Props, useEnter, Verdict } from "@/components/items";
import { Button } from "@/components/ui/button";
import type { Feedback, StudyItem, TransferItem } from "@/lib/api";
import { cn } from "@/lib/utils";

type Check = "clear" | "unknown_words" | "structure";
const CHECKS: { id: Check; label: string; hint: string }[] = [
  { id: "clear", label: "意味が分かる", hint: "読めば分かる" },
  { id: "unknown_words", label: "知らない単語がある", hint: "分からない語をタップ" },
  { id: "structure", label: "単語は分かるが意味がつかめない", hint: "文の組み立てが不明" },
];

function clean(w: string) {
  return w.replace(/^[^A-Za-z0-9']+|[^A-Za-z0-9']+$/g, "");
}

/** Step 2 of the cycle: the line that was not heard — read it, say whether the text is clear, then see why. */
export function StudyView({ item, rate, submit, next }: Props<StudyItem>) {
  const ctl = useAudio(item.segment.audio, { rate });
  const [check, setCheck] = useState<Check | null>(null);
  const [unknown, setUnknown] = useState<string[]>([]);
  const [revealed, setRevealed] = useState(false);
  const [fb, setFb] = useState<Feedback | null>(null);
  const [busy, setBusy] = useState(false);
  const shownAt = useRef(0);
  useEffect(() => {
    shownAt.current = performance.now();
  }, [item.item_id]);

  const send = async (understood: boolean | null) => {
    if (busy || fb) return;
    setBusy(true);
    const r = await submit({
      text_check: check,
      unknown_words: unknown,
      meaning_understood: understood,
      ms: Math.round(performance.now() - shownAt.current),
    });
    setBusy(false);
    if (r) setFb(r);
  };
  const choose = (c: Check) => {
    if (revealed) return;
    setCheck(c);
    if (c !== "unknown_words") setRevealed(true);
  };
  // the self-check is only confirmed against the translation when the learner said they understood
  const askConfirm = check === "clear" && !!item.ja;
  useEffect(() => {
    if (revealed || fb) return;
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e) || e.metaKey || e.ctrlKey) return;
      if (["1", "2", "3"].includes(e.key)) choose(CHECKS[Number(e.key) - 1].id);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
  useEnter(!!fb, next);
  useEnter(revealed && !askConfirm && !fb && !busy, () => send(null));

  const words = item.words_in_text ?? item.text.split(" ");
  const keySpan = phraseSpan(words, item.key?.expr);
  return (
    <div className="mx-auto max-w-2xl">
      <Header kind="1文の攻略" title={item.clip_title} level={item.level} reason={`聞き取り ${Math.round(item.accuracy * 100)}%`} />
      {item.context.length > 0 && (
        <p className="mb-2 text-sm text-muted-foreground">
          直前: {item.context[0].speaker}「{item.context[0].text}」
        </p>
      )}
      <AudioPlayer ctl={ctl} label={`話者 ${item.segment.speaker} · 聞き取れなかった文`} />

      <div className="mt-4 rounded-2xl border bg-card p-4">
        <p className="mb-2 text-xs font-medium text-muted-foreground">英文{check === "unknown_words" && !revealed ? "（分からない単語をタップ）" : ""}</p>
        <p className="flex flex-wrap gap-x-1.5 gap-y-1 text-lg leading-relaxed">
          {words.map((w, i) => {
            const c = clean(w);
            const picked = unknown.includes(c.toLowerCase());
            const tappable = check === "unknown_words" && !revealed && !!c;
            const isKey = revealed && keySpan !== null && i >= keySpan[0] && i < keySpan[1];
            return tappable ? (
              <button
                key={i}
                type="button"
                onClick={() => setUnknown((u) => (picked ? u.filter((x) => x !== c.toLowerCase()) : [...u, c.toLowerCase()]))}
                className={cn("rounded px-0.5 underline decoration-dotted underline-offset-4", picked && "bg-warning-soft")}
              >
                {w}
              </button>
            ) : (
              <span key={i} className={cn(picked && "bg-warning-soft", isKey && "font-semibold text-primary")}>
                {w}
              </span>
            );
          })}
        </p>
      </div>

      {!revealed && (
        <div className="mt-4">
          <p className="text-[15px] font-medium">英文を見れば意味が分かりますか？</p>
          <div className="mt-2 grid gap-2 sm:grid-cols-3">
            {CHECKS.map((c, i) => (
              <button
                key={c.id}
                type="button"
                onClick={() => choose(c.id)}
                className={cn("flex min-h-14 items-center gap-3 rounded-2xl border bg-card px-3 py-2.5 text-left hover:bg-muted", check === c.id && "border-primary bg-brand-soft")}
              >
                <span className="flex size-7 shrink-0 items-center justify-center rounded-md border text-sm tabular">{i + 1}</span>
                <span>
                  <span className="block text-sm font-medium">{c.label}</span>
                  <span className="block text-xs text-muted-foreground">{c.hint}</span>
                </span>
              </button>
            ))}
          </div>
          {check === "unknown_words" && (
            <div className="mt-3 flex justify-end">
              <Button onClick={() => setRevealed(true)}>{unknown.length ? `${unknown.length}語を選んで解説へ` : "解説へ"}</Button>
            </div>
          )}
        </div>
      )}

      {revealed && (
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="mt-4 space-y-3">
          <div className="space-y-3 rounded-2xl border bg-card p-4 text-sm">
            <Row icon={<BookOpen className="size-4" />} label="日本語訳">
              {item.ja ?? <span className="text-muted-foreground">この教材には日本語訳がありません</span>}
            </Row>
            {item.scene && (
              <Row icon={<MapPin className="size-4" />} label="場面">
                {item.scene}
              </Row>
            )}
            {item.key && (
              <Row icon={<Lightbulb className="size-4 text-warning" />} label="重要表現">
                <b>{item.key.expr}</b> = {item.key.ja}
              </Row>
            )}
            {item.grammar && (
              <Row icon={<Lightbulb className="size-4 text-warning" />} label="構文">
                <b>{item.grammar.pattern}</b> = {item.grammar.ja}
              </Row>
            )}
            {item.listening.length > 0 && (
              <Row icon={<Ear className="size-4" />} label="聞き取れなかった音">
                <ul className="space-y-1.5">
                  {item.listening.map((l) => (
                    <li key={l.category}>
                      <span className="font-medium">{l.label}</span>: {l.words.join(", ")}
                      {ERROR_TIPS[l.category] && <span className="block text-xs text-muted-foreground">{ERROR_TIPS[l.category]}</span>}
                    </li>
                  ))}
                </ul>
              </Row>
            )}
          </div>

          {!fb && askConfirm && (
            <div>
              <p className="text-[15px] font-medium">訳と同じ意味で理解できていましたか？</p>
              <div className="mt-2 grid grid-cols-2 gap-2">
                <Button variant="outline" className="h-11" disabled={busy} onClick={() => send(true)}>
                  はい、同じだった
                </Button>
                <Button variant="outline" className="h-11" disabled={busy} onClick={() => send(false)}>
                  いいえ、違っていた
                </Button>
              </div>
            </div>
          )}
          {!fb && !askConfirm && (
            <div className="flex justify-end">
              <NextButton onClick={() => send(null)} label="確認した" />
            </div>
          )}
          {fb && (
            <div className="space-y-3">
              <p className="rounded-xl bg-brand-soft px-3 py-2 text-sm">
                今回の推定原因: <b>{fb.error_label}</b>
                <span className="block text-xs text-muted-foreground">1回の結果では断定しません。回答が増えると「分析」に傾向が表示されます。</span>
              </p>
              {fb.transfer_next && (
                <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
                  <Repeat2 className="size-4" /> 次は、同じ表現を使った別の文を初めて聞きます。
                </p>
              )}
              <div className="flex justify-end">
                <NextButton onClick={next} />
              </div>
            </div>
          )}
        </motion.div>
      )}
    </div>
  );
}

/** Word indexes [start, end) of the first place where the phrase occurs as consecutive words. */
function phraseSpan(words: string[], expr?: string): [number, number] | null {
  if (!expr) return null;
  const want = expr.toLowerCase().split(/\s+/).map(clean).filter(Boolean);
  const have = words.map((w) => clean(w).toLowerCase());
  for (let i = 0; i + want.length <= have.length; i++) {
    if (want.every((w, j) => have[i + j] === w)) return [i, i + want.length];
  }
  return null;
}

function Row({ icon, label, children }: { icon: React.ReactNode; label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2.5">
      <span className="mt-0.5 shrink-0 text-muted-foreground">{icon}</span>
      <div className="min-w-0">
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className="leading-relaxed">{children}</div>
      </div>
    </div>
  );
}

function highlight(text: string, form?: string) {
  if (!form) return text;
  const i = text.toLowerCase().indexOf(form.toLowerCase());
  if (i < 0) return text;
  return (
    <>
      {text.slice(0, i)}
      <b className="text-primary">{text.slice(i, i + form.length)}</b>
      {text.slice(i + form.length)}
    </>
  );
}

/** Step 3: a sentence never heard before, in another situation and voice, that uses the practised expression. */
export function TransferView({ item, rate, submit, next }: Props<TransferItem>) {
  const ctl = useAudio(item.audio, { rate, autoplay: true });
  const [fb, setFb] = useState<Feedback | null>(null);
  const [choice, setChoice] = useState<number | null>(null);
  const shownAt = useRef(0);
  useEffect(() => {
    shownAt.current = performance.now();
  }, [item.item_id]);
  const choose = async (i: number) => {
    if (fb || choice !== null) return;
    setChoice(i);
    ctl.pause();
    const r = await submit({ choice: i, plays: ctl.listens, ms: Math.round(performance.now() - shownAt.current) });
    if (!r) return setChoice(null);
    setFb(r);
  };
  useEffect(() => {
    if (fb) return;
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e) || e.metaKey || e.ctrlKey) return;
      if (["1", "2", "3", "4"].includes(e.key)) choose(Number(e.key) - 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
  useEnter(!!fb, next);

  return (
    <div className="mx-auto max-w-2xl">
      <Header kind="応用：初めて聞く文" title="別の場面・別の話者" level={item.level} />
      <AudioPlayer ctl={ctl} label="初めて聞く1文（文字は答えた後に表示）" />
      <p className="mt-5 text-[15px] font-medium">{item.q}</p>
      <div className="mt-3 grid gap-2.5 sm:grid-cols-2">
        {item.choices.map((c, i) => {
          const isAns = fb && fb.answer === i;
          return (
            <button
              key={i}
              type="button"
              disabled={!!fb || choice !== null}
              onClick={() => choose(i)}
              className={cn(
                "flex min-h-14 items-center gap-3 rounded-2xl border bg-card px-4 py-3 text-left transition-colors",
                !fb && choice === null && "hover:bg-muted",
                !fb && choice === i && "border-primary bg-brand-soft",
                fb && isAns && "border-success bg-success-soft",
                fb && choice === i && !isAns && "border-destructive bg-danger-soft",
              )}
            >
              <span className="flex size-7 shrink-0 items-center justify-center rounded-md border text-sm tabular">{i + 1}</span>
              <span className="flex-1">{c}</span>
            </button>
          );
        })}
      </div>
      {!fb && <p className="mt-3 text-center text-xs text-muted-foreground">数字キー 1–4 で選択 · Space で再生/停止</p>}
      {fb && (
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="mt-4 space-y-3">
          <Verdict ok={!!fb.correct}>{fb.correct ? "正解 — 別の文でも聞き取れました" : "不正解 — 練習した文を近いうちに復習に出します"}</Verdict>
          <div className="space-y-2 rounded-2xl border bg-card p-4 text-sm">
            <p className="text-[15px] leading-relaxed">{highlight(fb.text ?? "", fb.form)}</p>
            <p className="text-muted-foreground">{fb.ja}</p>
            {fb.practice && <p className="border-t pt-2 text-xs text-muted-foreground">練習した文: {highlight(fb.practice, undefined)}</p>}
          </div>
          <div className="flex justify-end">
            <NextButton onClick={next} />
          </div>
        </motion.div>
      )}
    </div>
  );
}
