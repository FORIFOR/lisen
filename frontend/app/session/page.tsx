"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion } from "motion/react";
import { Coffee, Loader2, Pause, Play, Plus, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { ComprehensionView, DictationView, DiscriminationView, ShadowingView, VocabView } from "@/components/items";
import { SummaryView } from "@/components/summary";
import { WordQuestion } from "@/components/word-question";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { api, ApiError, type Feedback, type NextResult, type Profile, type Session, type Summary, type WordFeedback } from "@/lib/api";
import { cn } from "@/lib/utils";

/** Counts active seconds (tab visible and not paused); report() hands the delta to the server. */
function useActiveClock(paused: boolean) {
  const acc = useRef(0);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => {
      if (!paused && document.visibilityState === "visible") {
        acc.current += 1;
        setTick((t) => t + 1);
      }
    }, 1000);
    return () => clearInterval(id);
  }, [paused]);
  const report = useCallback(() => {
    const v = acc.current;
    acc.current = 0;
    return v;
  }, []);
  return { report, pending: () => acc.current, tick };
}

function mmss(sec: number) {
  const s = Math.max(0, Math.round(sec));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const r = s % 60;
  return h ? `${h}:${m.toString().padStart(2, "0")}:${r.toString().padStart(2, "0")}` : `${m}:${r.toString().padStart(2, "0")}`;
}

export default function SessionPage() {
  const router = useRouter();
  const [session, setSession] = useState<Session | null>(null);
  const [current, setCurrent] = useState<NextResult | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [loading, setLoading] = useState(true);
  const [none, setNone] = useState(false);
  const [paused, setPaused] = useState(false);
  const [extendOpen, setExtendOpen] = useState(false);
  const [endOpen, setEndOpen] = useState(false);
  const [rate, setRate] = useState(1);
  const clock = useActiveClock(paused || !!summary || none);
  const [itemKey, setItemKey] = useState(0);

  const finishWith = useCallback(async (id: string) => {
    const s = await api<Summary>(`/sessions/${id}/summary`);
    setSummary(s);
    setCurrent(null);
  }, []);

  const loadNext = useCallback(
    async (sid?: string) => {
      const id = sid ?? session?.id;
      if (!id) return;
      setLoading(true);
      try {
        const r = await api<NextResult>(`/sessions/${id}/next`, { body: { elapsed: clock.report() } });
        setSession(r.session);
        if (r.type === "done") await finishWith(id);
        else {
          setCurrent(r);
          setItemKey((k) => k + 1);
        }
      } catch (e) {
        toast.error((e as Error).message);
      } finally {
        setLoading(false);
      }
    },
    [session?.id, clock, finishWith],
  );

  useEffect(() => {
    (async () => {
      try {
        const p = await api<Profile>("/profile");
        setRate(p.settings.playback_rate || 1);
        const s = p.active_session;
        if (!s) {
          setNone(true);
          setLoading(false);
          return;
        }
        setSession(s);
        if (s.status === "paused") setPaused(true);
        await loadNext(s.id);
      } catch (e) {
        toast.error((e as Error).message);
        setLoading(false);
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // periodic heartbeat so time studied is saved even if the tab is closed
  useEffect(() => {
    if (!session || summary) return;
    const id = setInterval(() => {
      const e = clock.report();
      if (e > 0) api<Session>(`/sessions/${session.id}/heartbeat`, { body: { elapsed: e } }).then(setSession).catch(() => {});
    }, 20000);
    return () => clearInterval(id);
  }, [session, summary, clock]);

  // break countdown: advance automatically when the break is over
  const breakLeft = current?.type === "break" ? current.seconds_left - clock.pending() : null;
  useEffect(() => {
    if (breakLeft === null || breakLeft > 0 || loading) return;
    const t = setTimeout(() => loadNext(), 0);
    return () => clearTimeout(t);
  }, [breakLeft, loading, loadNext]);

  const submit = useCallback(
    async (body: Record<string, unknown>): Promise<Feedback | null> => {
      if (!session || current?.type !== "item") return null;
      try {
        const fb = await api<Feedback>(`/sessions/${session.id}/answer`, {
          body: { item_id: current.item.item_id, elapsed: clock.report(), ...body },
        });
        setSession(fb.session);
        fb.events?.forEach((ev) =>
          ev.event === "level_up"
            ? toast.success(`レベルアップ！ 新しい教材（レベル ${ev.to}）が解放されました`)
            : toast(`教材の難易度をレベル ${ev.to} に調整しました`),
        );
        return fb;
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) {
          toast("この問題は別の端末で回答済みです。次へ進みます");
          loadNext();
        } else toast.error((e as Error).message);
        return null;
      }
    },
    [session, current, clock, loadNext],
  );

  const setPause = async (p: boolean) => {
    setPaused(p);
    if (session) api<Session>(`/sessions/${session.id}/heartbeat`, { body: { elapsed: clock.report(), status: p ? "paused" : "active" } }).then(setSession).catch(() => {});
  };

  const extend = async (minutes: number) => {
    if (!session) return;
    try {
      const s = await api<Session>(`/sessions/${session.id}/extend`, { body: { minutes } });
      setSession(s);
      setExtendOpen(false);
      toast.success(`${minutes}分延長しました`);
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  const endNow = async () => {
    if (!session) return;
    setEndOpen(false);
    await api(`/sessions/${session.id}/heartbeat`, { body: { elapsed: clock.report() } }).catch(() => {});
    const s = await api<Summary>(`/sessions/${session.id}/finish`, { body: {} });
    setSummary(s);
    setCurrent(null);
  };

  const skipBreak = async () => {
    if (!session) return;
    await api(`/sessions/${session.id}/skip-block`, { body: {} });
    loadNext();
  };

  // keyboard: P = pause
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.tagName === "TEXTAREA" || t.tagName === "INPUT") return;
      if (e.key === "p" || e.key === "P") setPause(!paused);
      if (e.key === "Escape" && paused) setPause(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (none) {
    return (
      <div className="flex min-h-dvh flex-col items-center justify-center gap-4 p-6 text-center">
        <p className="text-muted-foreground">進行中の学習はありません。</p>
        <Button onClick={() => router.push("/")}>ホームへ</Button>
      </div>
    );
  }

  if (summary) {
    return (
      <div className="mx-auto max-w-3xl px-4 py-8">
        <SummaryView summary={summary} />
      </div>
    );
  }

  const blocks = session?.blocks ?? [];
  const bi = session?.block_index ?? 0;
  const block = blocks[bi];
  const studied = (session?.study_seconds ?? 0) + clock.pending();
  const isTest = session?.mode === "test";
  const blockPct = block && block.minutes ? Math.min(100, (((block.elapsed ?? 0) + (block.kind !== "break" ? clock.pending() : 0)) / (block.minutes * 60)) * 100) : 0;

  return (
    <div className="min-h-dvh">
      <header className="sticky top-0 z-20 border-b bg-background/90 backdrop-blur" style={{ paddingTop: "env(safe-area-inset-top)" }}>
        <div className="mx-auto flex h-14 max-w-5xl items-center gap-3 px-4">
          <Link href="/" className="flex size-9 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted" aria-label="ホームへ（学習は保存され、続きから再開できます）" title="ホームへ（続きから再開できます）">
            <X className="size-5" />
          </Link>
          <div className="min-w-0 flex-1">
            <div className="truncate text-sm font-medium">{block?.label ?? "…"}</div>
            {!isTest && (
              <div className="mt-1 flex gap-1">
                {blocks.map((b, i) => (
                  <div key={i} className={cn("h-1.5 overflow-hidden rounded-full bg-muted", b.kind === "break" ? "bg-warning-soft" : "")} style={{ flex: b.minutes }} title={`${b.label} ${b.minutes}分`}>
                    <div className={cn("h-full rounded-full", b.kind === "break" ? "bg-warning" : "bg-primary")} style={{ width: i < bi ? "100%" : i === bi ? `${blockPct}%` : "0%" }} />
                  </div>
                ))}
              </div>
            )}
          </div>
          <div className="text-right text-xs tabular text-muted-foreground">
            <div className="text-sm font-medium text-foreground">{mmss(studied)}</div>
            {!isTest && <div>/ {session?.planned_study_minutes ?? 0}分</div>}
          </div>
          {!isTest && (
            <Button variant="outline" size="icon-lg" onClick={() => setExtendOpen(true)} aria-label="延長" title="学習を延長">
              <Plus className="size-4" />
            </Button>
          )}
          <Button variant="outline" size="icon-lg" onClick={() => setPause(true)} aria-label="一時停止 (P)" title="一時停止 (P)">
            <Pause className="size-4" />
          </Button>
        </div>
      </header>

      <main className="mx-auto max-w-5xl px-4 pb-16 pt-6">
        {session?.notes?.length ? <p className="mb-4 rounded-lg bg-warning-soft px-3 py-2 text-xs">{session.notes[session.notes.length - 1]}</p> : null}
        {loading && !current ? (
          <div className="flex justify-center py-24 text-muted-foreground">
            <Loader2 className="size-6 animate-spin" />
          </div>
        ) : current?.type === "break" ? (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="mx-auto flex max-w-md flex-col items-center gap-5 py-16 text-center">
            <div className="flex size-16 items-center justify-center rounded-full bg-warning-soft">
              <Coffee className="size-7 text-warning" />
            </div>
            <h2 className="text-2xl font-semibold">休憩しましょう</h2>
            <p className="text-muted-foreground">集中力が落ちた状態で続けるより、少し休んだほうが学習の質が上がります。画面から目を離し、肩の力を抜きましょう。</p>
            <div className="text-5xl font-semibold tabular">{mmss(breakLeft ?? 0)}</div>
            <Button variant="outline" onClick={skipBreak}>
              休憩を終えて再開
            </Button>
          </motion.div>
        ) : current?.type === "item" ? (
          <AnimatePresence mode="wait">
            <motion.div key={itemKey} initial={{ opacity: 0, x: 16 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: -16 }} transition={{ duration: 0.18 }}>
              {current.item.type === "comprehension" && <ComprehensionView item={current.item} rate={rate} submit={submit} next={() => loadNext()} />}
              {current.item.type === "dictation" && <DictationView item={current.item} rate={rate} submit={submit} next={() => loadNext()} />}
              {current.item.type === "discrimination" && <DiscriminationView item={current.item} rate={rate} submit={submit} next={() => loadNext()} />}
              {current.item.type === "vocab" && <VocabView item={current.item} rate={rate} submit={submit} next={() => loadNext()} />}
              {current.item.type === "shadowing" && <ShadowingView item={current.item} rate={rate} submit={submit} next={() => loadNext()} />}
              {current.item.type === "word" && (
                <WordQuestion item={current.item} rate={rate} submit={(b) => submit(b) as unknown as Promise<WordFeedback | null>} next={() => loadNext()} />
              )}
            </motion.div>
          </AnimatePresence>
        ) : null}
      </main>

      {/* pause overlay */}
      <AnimatePresence>
        {paused && (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="fixed inset-0 z-40 flex items-center justify-center bg-background/[0.97] p-6 backdrop-blur">
            <div className="flex w-full max-w-sm flex-col gap-3 text-center">
              <h2 className="text-2xl font-semibold">一時停止中</h2>
              <p className="text-sm text-muted-foreground">学習時間は止まっています。Mac・Windows・iPhone のどれからでも続きを再開できます。</p>
              <Button size="lg" className="mt-2 h-12" onClick={() => setPause(false)}>
                <Play className="size-4" /> 再開する
              </Button>
              {!isTest && (
                <Button variant="outline" size="lg" className="h-12" onClick={() => setExtendOpen(true)}>
                  <Plus className="size-4" /> 学習を延長する
                </Button>
              )}
              <Button variant="ghost" size="lg" className="h-12" onClick={() => setEndOpen(true)}>
                今日はここで終える
              </Button>
              <Link href="/" className="text-sm text-muted-foreground underline-offset-4 hover:underline">
                ホームに戻る（後で再開）
              </Link>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <Dialog open={extendOpen} onOpenChange={setExtendOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>学習を延長</DialogTitle>
            <DialogDescription>延長分は、新しい音声・弱点訓練・長めのリスニングで構成されます（同じ問題の水増しはしません）。</DialogDescription>
          </DialogHeader>
          <div className="grid grid-cols-3 gap-2">
            {[15, 30, 60].map((m) => (
              <Button key={m} variant="outline" size="lg" className="h-14 text-base" onClick={() => extend(m)}>
                +{m}分
              </Button>
            ))}
          </div>
        </DialogContent>
      </Dialog>

      <Dialog open={endOpen} onOpenChange={setEndOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>今日の学習を終えますか？</DialogTitle>
            <DialogDescription>ここまでの結果と学習時間は保存されます。</DialogDescription>
          </DialogHeader>
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setEndOpen(false)}>
              続ける
            </Button>
            <Button onClick={endNow}>終了して結果を見る</Button>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
