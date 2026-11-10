"use client";

import { AnimatePresence, motion } from "motion/react";
import { ArrowRight, Check, CircleAlert, Lightbulb, Mic, Square, Volume2, X } from "lucide-react";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";

const noopSubscribe = () => () => {};
import { AudioPlayer, isTyping, Kbd, useAudio } from "@/components/audio-player";
import { Button } from "@/components/ui/button";
import type {
  ComprehensionItem,
  DictationItem,
  DiscriminationItem,
  Feedback,
  ShadowingItem,
  Token,
  VocabItem,
} from "@/lib/api";
import { audioUrl, LEVEL_LABEL } from "@/lib/api";
import { cn } from "@/lib/utils";

type Props<T> = {
  item: T;
  rate: number;
  submit: (body: Record<string, unknown>) => Promise<Feedback | null>;
  next: () => void;
};

const ERROR_TIPS: Record<string, string> = {
  weak_forms: "a / the / to / can などの機能語は、弱く短く「ə」に近い音になります。文の意味の骨組みから、入るはずの語を予測しながら聞きましょう。",
  contractions: "I'll / don't / we'd などの短縮形は1音節に縮みます。'll（未来）と 'd（過去・仮定）の違いは文脈で確かめましょう。",
  linking: "子音で終わる語の次に母音で始まる語が来ると、2語がつながって1語のように聞こえます（例: get it →「ゲリッ」）。",
  numbers: "13 と 30、15 と 50 などは強勢の位置で聞き分けます。時刻・金額は単位とセットで聞き取りましょう。",
  endings: "-s / -ed は弱くほとんど聞こえないことがあります。主語や時制（いつの話か）から文法的に補いましょう。",
  vocabulary: "聞き慣れない内容語です。意味だけでなく、音（強勢の位置）と一緒に覚えると次から聞き取れます。",
};

function useEnter(enabled: boolean, fn: () => void) {
  const ref = useRef(fn);
  useEffect(() => {
    ref.current = fn;
  });
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Enter" && !isTyping(e) && !e.isComposing) {
        e.preventDefault();
        ref.current();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [enabled]);
}

function NextButton({ onClick, label = "次へ" }: { onClick: () => void; label?: string }) {
  return (
    <Button size="lg" className="h-12 w-full text-base sm:w-auto sm:px-8" onClick={onClick} autoFocus>
      {label} <ArrowRight className="size-4" /> <Kbd>Enter</Kbd>
    </Button>
  );
}

function Header({ kind, title, level, reason }: { kind: string; title?: string; level: number; reason?: string }) {
  return (
    <div className="mb-4 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
      <span className="rounded-full bg-brand-soft px-2.5 py-1 font-medium text-primary">{kind}</span>
      <span className="rounded-full border px-2 py-0.5">{LEVEL_LABEL[level]}</span>
      {title && <span className="truncate">{title}</span>}
      {reason && <span className="ml-auto rounded-full bg-warning-soft px-2 py-0.5 text-foreground/80">{reason}</span>}
    </div>
  );
}

function Verdict({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      className={cn("flex items-center gap-2 rounded-xl px-3 py-2 text-sm font-medium", ok ? "bg-success-soft text-success" : "bg-danger-soft text-destructive")}
    >
      {ok ? <Check className="size-4" /> : <X className="size-4" />}
      {children}
    </motion.div>
  );
}

function SrsNote({ fb }: { fb: Feedback }) {
  if (!fb.srs) return null;
  const due = new Date(fb.srs.due);
  const mins = Math.round((due.getTime() - Date.now()) / 60000);
  const when = mins < 60 ? `${Math.max(1, mins)}分後` : mins < 60 * 24 ? `${Math.round(mins / 60)}時間後` : `${Math.round(mins / 1440)}日後`;
  return <p className="text-xs text-muted-foreground">次の復習: {when}（FSRS）</p>;
}

// ---------------------------------------------------------------- comprehension

export function ComprehensionView({ item, rate, submit, next }: Props<ComprehensionItem>) {
  const ctl = useAudio(item.clip.audio, { rate });
  const [answers, setAnswers] = useState<(number | null)[]>(item.questions.map(() => null));
  const [fb, setFb] = useState<Feedback | null>(null);
  const [busy, setBusy] = useState(false);
  const [cursor, setCursor] = useState(0);
  const ready = answers.every((a) => a !== null);

  const send = async () => {
    if (!ready || busy) return;
    setBusy(true);
    ctl.pause();
    const r = await submit({ answers, replays: ctl.plays });
    setBusy(false);
    if (r) setFb(r);
  };

  useEffect(() => {
    if (fb) return;
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e) || e.metaKey || e.ctrlKey) return;
      const n = Number(e.key);
      if (n >= 1 && n <= 4 && n <= item.questions[cursor].choices.length) {
        setAnswers((a) => a.map((v, i) => (i === cursor ? n - 1 : v)));
        setCursor((c) => Math.min(item.questions.length - 1, c + 1));
      } else if (e.key === "Enter" && ready) {
        e.preventDefault();
        send();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
  useEnter(!!fb, next);

  const segs = fb?.transcript?.segments ?? [];
  const activeSeg = segs.findIndex((s) => ctl.time >= s.start && ctl.time <= s.end + 0.3);

  return (
    <div>
      <Header kind={item.block === "long" ? "実践リスニング" : item.block === "multi" ? "複数話者" : item.block === "test" ? "テスト" : "初見リスニング"} title={item.title} level={item.level} />
      {item.relisten && <p className="mb-3 rounded-lg bg-warning-soft px-3 py-2 text-xs">未聴の教材を使い切ったため、以前の音声を再聴しています。</p>}
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="space-y-3">
          <AudioPlayer ctl={ctl} label={`${item.clip.speakers > 1 ? `${item.clip.speakers}人の会話` : "1人の話"} · まず文字を見ずに聞きましょう`} />
          <AnimatePresence>
            {fb && (
              <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="rounded-2xl border bg-card p-4">
                <div className="mb-2 flex items-center justify-between text-sm font-medium">
                  スクリプト
                  <span className="text-xs font-normal text-muted-foreground">行をタップでその文を再生</span>
                </div>
                <ol className="space-y-1.5">
                  {segs.map((s, i) => (
                    <li key={s.id}>
                      <button
                        type="button"
                        onClick={() => ctl.play(s.start)}
                        className={cn("flex w-full gap-2 rounded-lg px-2 py-1.5 text-left text-[15px] leading-relaxed transition-colors hover:bg-muted", i === activeSeg && ctl.playing && "bg-brand-soft")}
                      >
                        {item.clip.speakers > 1 && <span className="mt-0.5 shrink-0 text-xs font-semibold text-primary">{s.speaker}</span>}
                        <span>{s.text}</span>
                      </button>
                    </li>
                  ))}
                </ol>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
        <div className="space-y-3">
          {item.questions.map((q, qi) => {
            const r = fb?.results?.[qi];
            return (
              <div key={qi} className={cn("rounded-2xl border bg-card p-4", !fb && qi === cursor && "ring-2 ring-primary/30")} onClick={() => !fb && setCursor(qi)}>
                <p className="mb-3 text-[15px] font-medium">
                  Q{qi + 1}. {q.q}
                </p>
                <div className="grid gap-2">
                  {q.choices.map((c, ci) => {
                    const chosen = answers[qi] === ci;
                    const isAns = r && r.answer === ci;
                    return (
                      <button
                        key={ci}
                        type="button"
                        disabled={!!fb}
                        onClick={() => {
                          setAnswers((a) => a.map((v, i) => (i === qi ? ci : v)));
                          setCursor(Math.min(item.questions.length - 1, qi + 1));
                        }}
                        className={cn(
                          "flex min-h-11 items-center gap-3 rounded-xl border px-3 py-2 text-left text-sm transition-colors",
                          !fb && (chosen ? "border-primary bg-brand-soft" : "hover:bg-muted"),
                          fb && isAns && "border-success bg-success-soft",
                          fb && chosen && !isAns && "border-destructive bg-danger-soft",
                        )}
                      >
                        <span className="flex size-6 shrink-0 items-center justify-center rounded-md border text-xs tabular">{ci + 1}</span>
                        {c}
                      </button>
                    );
                  })}
                </div>
                {r && (
                  <p className="mt-3 flex gap-2 text-sm text-muted-foreground">
                    <Lightbulb className="mt-0.5 size-4 shrink-0 text-warning" />
                    {r.explain}
                  </p>
                )}
              </div>
            );
          })}
          {!fb ? (
            <Button size="lg" className="h-12 w-full text-base" disabled={!ready || busy} onClick={send}>
              {item.questions.length ? "答え合わせ" : "聞き終えた"} <Kbd>Enter</Kbd>
            </Button>
          ) : (
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              {fb.score == null ? (
                <span className="text-sm text-muted-foreground">この音声には問題がありません · 続けて1文ずつ書き取ります</span>
              ) : (
                <Verdict ok={fb.score >= 0.99}>
                  {Math.round(fb.score * 100)}% 正解 {item.block !== "test" && "· 続けてこの音声を1文ずつ書き取ります"}
                </Verdict>
              )}
              <NextButton onClick={next} />
            </div>
          )}
          {!fb && <p className="text-center text-xs text-muted-foreground">数字キー 1–4 で選択 · Space で再生/停止</p>}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- dictation

function TokenDiff({ tokens, extra }: { tokens: Token[]; extra: string[] }) {
  // group expanded contractions back to the surface word
  const groups: Token[][] = [];
  tokens.forEach((t, i) => {
    const prev = tokens[i - 1];
    if (prev && prev.shown === t.shown && t.shown.includes("'") && groups.length) groups[groups.length - 1].push(t);
    else groups.push([t]);
  });
  return (
    <p className="flex flex-wrap gap-x-1.5 gap-y-2 text-lg leading-relaxed">
      {groups.map((g, i) => {
        const bad = g.some((t) => t.status === "wrong" || t.status === "missed");
        const typo = g.some((t) => t.status === "typo");
        const typed = g.map((t) => t.typed).filter(Boolean).join(" ");
        const word = g[0].shown.includes("'") ? g[0].shown : g.map((t) => t.shown).join(" ");
        return (
          <span key={i} className="inline-flex flex-col items-start">
            <span
              className={cn(
                "rounded px-0.5",
                bad && "bg-danger-soft font-medium text-destructive",
                typo && "underline decoration-warning decoration-wavy underline-offset-4",
              )}
            >
              {word}
            </span>
            {bad && <span className="px-0.5 text-[11px] text-muted-foreground line-through">{typed || "（聞き落とし）"}</span>}
          </span>
        );
      })}
      {extra.length > 0 && <span className="self-start text-sm text-muted-foreground">（余分: {extra.join(", ")}）</span>}
    </p>
  );
}

export function DictationView({ item, rate, submit, next }: Props<DictationItem>) {
  const ctl = useAudio(item.segment.audio, { rate, autoplay: true });
  const [text, setText] = useState("");
  const [fb, setFb] = useState<Feedback | null>(null);
  const [busy, setBusy] = useState(false);
  const ta = useRef<HTMLTextAreaElement>(null);
  const usedSlow = useRef(false);
  useEffect(() => {
    if (ctl.slow) usedSlow.current = true;
  }, [ctl.slow]);
  useEffect(() => {
    // don't steal focus on phones (keyboard would cover the player)
    if (window.matchMedia("(pointer: fine)").matches) ta.current?.focus();
  }, []);

  const send = async (giveUp = false) => {
    if (busy || fb || (!giveUp && !text.trim())) return;
    setBusy(true);
    const r = await submit({ text: giveUp ? "" : text, replays: ctl.plays, slow: usedSlow.current });
    setBusy(false);
    if (r) setFb(r);
  };
  useEnter(!!fb, next);
  const acc = fb?.accuracy ?? 0;

  return (
    <div className="mx-auto max-w-2xl">
      <Header kind={item.review ? "復習ディクテーション" : "ディクテーション"} title={item.clip_title} level={item.level} reason={item.reason} />
      <AudioPlayer ctl={ctl} label={`${item.segment.speakers && item.segment.speakers > 1 ? `話者 ${item.segment.speaker} · ` : ""}約 ${item.word_count} 語`} />
      {item.context.length > 0 && !fb && (
        <p className="mt-3 px-1 text-sm text-muted-foreground">
          <span className="mr-1 text-xs">直前:</span>
          {item.context.map((c) => c.text).join(" ")}
        </p>
      )}
      {!fb ? (
        <div className="mt-4 space-y-3">
          <textarea
            ref={ta}
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault();
                send();
              }
            }}
            rows={3}
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
            placeholder="聞こえた英文をそのまま入力"
            className="w-full resize-none rounded-2xl border bg-card px-4 py-3 text-lg outline-none focus:ring-2 focus:ring-primary/40"
          />
          <div className="flex items-center justify-between gap-3">
            <p className="text-xs text-muted-foreground">
              <span className="hidden md:inline">Tab: もう一度 · Shift+Tab: ゆっくり · </span>数字は算用数字でも英単語でもOK
            </p>
            <div className="flex shrink-0 gap-2">
              <Button size="lg" variant="ghost" className="h-12" onClick={() => send(true)} disabled={busy}>
                分からない
              </Button>
              <Button size="lg" className="h-12 px-6 text-base" onClick={() => send()} disabled={busy || !text.trim()}>
                採点 <Kbd>Enter</Kbd>
              </Button>
            </div>
          </div>
        </div>
      ) : (
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="mt-4 space-y-4">
          <div className="rounded-2xl border bg-card p-4">
            <div className="mb-3 flex items-center justify-between">
              <Verdict ok={acc >= 0.999}>{acc >= 0.999 ? "完璧です" : `${Math.round(acc * 100)}% 聞き取れました`}</Verdict>
              <SrsNote fb={fb} />
            </div>
            <TokenDiff tokens={fb.tokens ?? []} extra={fb.extra ?? []} />
            <p className="mt-3 border-t pt-3 text-sm text-muted-foreground">{fb.reference}</p>
          </div>
          {fb.errors && Object.keys(fb.errors).length > 0 && (
            <div className="space-y-2">
              {Object.entries(fb.errors).map(([k, n]) => (
                <div key={k} className="flex gap-3 rounded-2xl border bg-card p-3 text-sm">
                  <CircleAlert className="mt-0.5 size-4 shrink-0 text-warning" />
                  <div>
                    <div className="font-medium">
                      {fb.error_labels?.[k]} <span className="text-muted-foreground">× {n}</span>
                    </div>
                    <p className="mt-0.5 text-muted-foreground">{ERROR_TIPS[k]}</p>
                  </div>
                </div>
              ))}
            </div>
          )}
          {fb.moving_on && <p className="text-sm text-success">この音声は十分に聞き取れています。新しい音声へ進みます。</p>}
          <div className="flex justify-end">
            <NextButton onClick={next} />
          </div>
        </motion.div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- discrimination

export function DiscriminationView({ item, rate, submit, next }: Props<DiscriminationItem>) {
  const ctl = useAudio(item.segment.audio, { rate, autoplay: true });
  const [fb, setFb] = useState<Feedback | null>(null);
  const [choice, setChoice] = useState<number | null>(null);

  const choose = async (i: number) => {
    if (fb || choice !== null) return;
    setChoice(i);
    const r = await submit({ choice: i, replays: ctl.plays });
    if (r) setFb(r);
    else setChoice(null);
  };
  useEffect(() => {
    if (fb) return;
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e) || e.metaKey || e.ctrlKey) return;
      if (e.key === "1" || e.key === "2") choose(Number(e.key) - 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
  useEnter(!!fb, next);

  return (
    <div className="mx-auto max-w-2xl">
      <Header kind="聞き分け" title={item.clip_title} level={item.level} reason={item.reason ?? item.focus} />
      <AudioPlayer ctl={ctl} label="どちらの文が聞こえましたか？" />
      <div className="mt-4 grid gap-3">
        {item.options.map((o, i) => {
          const isAns = fb && fb.answer === i;
          return (
            <button
              key={i}
              type="button"
              disabled={!!fb}
              onClick={() => choose(i)}
              className={cn(
                "flex min-h-14 items-center gap-3 rounded-2xl border bg-card px-4 py-3 text-left text-lg transition-colors",
                !fb && "hover:bg-muted",
                fb && isAns && "border-success bg-success-soft",
                fb && choice === i && !isAns && "border-destructive bg-danger-soft",
              )}
            >
              <span className="flex size-7 shrink-0 items-center justify-center rounded-md border text-sm tabular">{i + 1}</span>
              {o}
            </button>
          );
        })}
      </div>
      {fb && (
        <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mt-4 space-y-3">
          <Verdict ok={!!fb.correct}>
            {fb.correct ? "正解" : "不正解"} — 「{fb.target}」と「{fb.distractor}」の聞き分け（{fb.category}）
          </Verdict>
          <div className="flex justify-end">
            <NextButton onClick={next} />
          </div>
        </motion.div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- vocab

export function VocabView({ item, rate, submit, next }: Props<VocabItem>) {
  const ctl = useAudio(item.segment.audio, { rate, autoplay: true });
  const [text, setText] = useState("");
  const [fb, setFb] = useState<Feedback | null>(null);
  const send = async (giveUp = false) => {
    if (fb || (!giveUp && !text.trim())) return;
    const r = await submit({ text: giveUp ? "" : text, replays: ctl.plays });
    if (r) setFb(r);
  };
  useEnter(!!fb, next);
  return (
    <div className="mx-auto max-w-2xl">
      <Header kind={item.review ? "語彙の復習" : "語彙の聞き取り"} level={item.level} />
      <AudioPlayer ctl={ctl} label="空欄に入る語を聞き取って入力" />
      <p className="mt-4 rounded-2xl border bg-card px-4 py-4 text-lg leading-relaxed">{item.masked}</p>
      {!fb ? (
        <div className="mt-3 flex gap-2">
          <input
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.nativeEvent.isComposing) {
                e.preventDefault();
                send();
              }
            }}
            autoCapitalize="off"
            autoCorrect="off"
            spellCheck={false}
            autoFocus
            placeholder={`${item.first_letter}…（${item.letters}文字）`}
            className="h-12 min-w-0 flex-1 rounded-xl border bg-card px-4 text-lg outline-none focus:ring-2 focus:ring-primary/40"
          />
          <Button size="lg" variant="ghost" className="h-12" onClick={() => send(true)}>
            分からない
          </Button>
          <Button size="lg" className="h-12 px-6" onClick={() => send()} disabled={!text.trim()}>
            確認
          </Button>
        </div>
      ) : (
        <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} className="mt-4 space-y-3">
          <Verdict ok={!!fb.correct}>{fb.correct ? "正解" : `正解は「${fb.word}」`}</Verdict>
          <div className="rounded-2xl border bg-card p-4">
            <div className="text-2xl font-semibold tracking-tight">{fb.word}</div>
            <div className="mt-1 text-muted-foreground">{fb.ja}</div>
            <p className="mt-3 text-sm">{fb.reference}</p>
          </div>
          <div className="flex items-center justify-between">
            <SrsNote fb={fb} />
            <NextButton onClick={next} />
          </div>
        </motion.div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- shadowing

function useRecorder() {
  // getUserMedia only exists in secure contexts (https / localhost)
  const supported = useSyncExternalStore(
    noopSubscribe,
    () => !!navigator.mediaDevices?.getUserMedia && typeof MediaRecorder !== "undefined",
    () => false,
  );
  const [recording, setRecording] = useState(false);
  const [url, setUrl] = useState<string | null>(null);
  const rec = useRef<MediaRecorder | null>(null);
  useEffect(() => () => {
    if (url) URL.revokeObjectURL(url);
  }, [url]);
  const start = async () => {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const r = new MediaRecorder(stream);
    const chunks: Blob[] = [];
    r.ondataavailable = (e) => chunks.push(e.data);
    r.onstop = () => {
      stream.getTracks().forEach((t) => t.stop());
      setUrl(URL.createObjectURL(new Blob(chunks, { type: r.mimeType })));
    };
    r.start();
    rec.current = r;
    setRecording(true);
  };
  const stop = () => {
    rec.current?.stop();
    setRecording(false);
  };
  return { supported, recording, url, start, stop };
}

export function ShadowingView({ item, rate, submit, next }: Props<ShadowingItem>) {
  const ctl = useAudio(item.segment.audio, { rate });
  const recorder = useRecorder();
  const [done, setDone] = useState(false);
  const rateIt = async (r: number) => {
    if (done) return;
    setDone(true);
    const res = await submit({ rating: r });
    if (res) next();
    else setDone(false);
  };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e) || e.metaKey || e.ctrlKey) return;
      if (["1", "2", "3"].includes(e.key)) rateIt(Number(e.key));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
  return (
    <div className="mx-auto max-w-2xl">
      <Header kind="シャドーイング" title={item.clip_title} level={item.level} />
      <p className="mb-4 rounded-2xl border bg-card px-4 py-5 text-xl leading-relaxed">{item.text}</p>
      <AudioPlayer ctl={ctl} label="音声に少し遅れて、同じリズムで声に出す" />
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {recorder.supported ? (
          recorder.recording ? (
            <Button variant="destructive" onClick={recorder.stop}>
              <Square className="size-4" /> 録音を止める
            </Button>
          ) : (
            <Button variant="outline" onClick={() => { recorder.start().catch(() => {}); ctl.replay(); }}>
              <Mic className="size-4" /> 音声と一緒に録音
            </Button>
          )
        ) : (
          <span className="text-xs text-muted-foreground">録音は HTTPS 接続（Tailscale Serve など）またはこの Mac 上でのみ使えます</span>
        )}
        {recorder.url && (
          <Button variant="outline" onClick={() => new Audio(recorder.url!).play()}>
            <Volume2 className="size-4" /> 自分の声を聞く
          </Button>
        )}
      </div>
      <p className="mt-6 mb-2 text-sm font-medium">どのくらい音声についていけましたか？</p>
      <div className="grid grid-cols-3 gap-2">
        {[
          [1, "難しい", "復習に追加"],
          [2, "まあまあ", ""],
          [3, "できた", ""],
        ].map(([v, l, sub]) => (
          <button
            key={v}
            type="button"
            disabled={done}
            onClick={() => rateIt(v as number)}
            className="flex min-h-14 flex-col items-center justify-center rounded-2xl border bg-card px-2 py-2 text-sm hover:bg-muted"
          >
            <span className="font-medium">
              <Kbd>{v}</Kbd> {l}
            </span>
            {sub && <span className="text-[11px] text-muted-foreground">{sub}</span>}
          </button>
        ))}
      </div>
    </div>
  );
}

export function playUrl(rel: string) {
  new Audio(audioUrl(rel)).play().catch(() => {});
}
