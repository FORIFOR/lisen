"use client";

import { Pause, Play, RotateCcw, Snail } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { audioUrl } from "@/lib/api";
import { cn } from "@/lib/utils";

export function isTyping(e: KeyboardEvent) {
  const t = e.target as HTMLElement | null;
  return !!t && (t.tagName === "TEXTAREA" || t.tagName === "INPUT" || t.isContentEditable);
}

export type AudioCtl = ReturnType<typeof useAudio>;

/** One <audio> element per player; tracks plays so we can record how often a learner replayed. */
export function useAudio(src: string | null | undefined, opts?: { rate?: number; autoplay?: boolean }) {
  const ref = useRef<HTMLAudioElement | null>(null);
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [slow, setSlow] = useState(false);
  const [plays, setPlays] = useState(0);
  const [blocked, setBlocked] = useState(false);
  const baseRate = opts?.rate ?? 1;

  useEffect(() => {
    if (!src) return;
    const a = new Audio(audioUrl(src));
    a.preload = "auto";
    ref.current = a;
    const onTime = () => setTime(a.currentTime);
    const onMeta = () => setDuration(a.duration || 0);
    const onEnd = () => setPlaying(false);
    const onPause = () => setPlaying(false);
    const onPlay = () => setPlaying(true);
    a.addEventListener("timeupdate", onTime);
    a.addEventListener("loadedmetadata", onMeta);
    a.addEventListener("ended", onEnd);
    a.addEventListener("pause", onPause);
    a.addEventListener("play", onPlay);
    if (opts?.autoplay) {
      a.playbackRate = baseRate;
      a.play().then(() => setPlays(1)).catch(() => setBlocked(true)); // iOS needs a tap first
    }
    return () => {
      a.pause();
      a.removeEventListener("timeupdate", onTime);
      a.removeEventListener("loadedmetadata", onMeta);
      a.removeEventListener("ended", onEnd);
      a.removeEventListener("pause", onPause);
      a.removeEventListener("play", onPlay);
      ref.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [src]);

  const play = useCallback(
    (from?: number, useSlow?: boolean) => {
      const a = ref.current;
      if (!a) return;
      const s = useSlow ?? slow;
      a.playbackRate = s ? Math.min(baseRate, 1) * 0.75 : baseRate;
      if (from !== undefined) a.currentTime = from;
      else if (a.ended || a.currentTime >= (a.duration || Infinity) - 0.05) a.currentTime = 0;
      a.play().then(() => {
        setBlocked(false);
        setPlays((p) => p + 1);
      }).catch(() => setBlocked(true));
    },
    [slow, baseRate],
  );
  const pause = useCallback(() => ref.current?.pause(), []);
  const toggle = useCallback(() => (ref.current && !ref.current.paused ? pause() : play()), [play, pause]);
  const replay = useCallback(() => play(0), [play]);
  const toggleSlow = useCallback(() => {
    setSlow((s) => {
      const next = !s;
      if (ref.current && !ref.current.paused) ref.current.playbackRate = next ? Math.min(baseRate, 1) * 0.75 : baseRate;
      return next;
    });
  }, [baseRate]);
  const seek = useCallback((t: number) => {
    if (ref.current) ref.current.currentTime = t;
  }, []);

  return { ref, playing, time, duration, slow, plays, blocked, play, pause, toggle, replay, toggleSlow, seek };
}

function fmt(t: number) {
  if (!isFinite(t)) return "0:00";
  const m = Math.floor(t / 60);
  const s = Math.floor(t % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

/** Space: play/pause · R: replay · S: slow (outside text fields). Tab inside text fields replays. */
export function AudioPlayer({ ctl, className, compact, label }: { ctl: AudioCtl; className?: string; compact?: boolean; label?: string }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (isTyping(e)) {
        if (e.key === "Tab") {
          e.preventDefault();
          if (e.shiftKey) ctl.toggleSlow();
          else ctl.replay();
        }
        return;
      }
      if (e.code === "Space") {
        e.preventDefault();
        ctl.toggle();
      } else if (e.key === "r" || e.key === "R") ctl.replay();
      else if (e.key === "s" || e.key === "S") ctl.toggleSlow();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ctl]);

  const pct = ctl.duration ? Math.min(100, (ctl.time / ctl.duration) * 100) : 0;
  return (
    <div className={cn("rounded-2xl border bg-card p-3", className)}>
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={ctl.toggle}
          className={cn(
            "relative flex shrink-0 items-center justify-center rounded-full bg-primary text-primary-foreground shadow-sm transition-transform active:scale-95",
            compact ? "size-11" : "size-14",
            ctl.blocked && "ring-4 ring-primary/30 animate-pulse",
          )}
          aria-label={ctl.playing ? "一時停止 (Space)" : "再生 (Space)"}
        >
          {ctl.playing ? <Pause className="size-5" /> : <Play className="size-5 translate-x-0.5" />}
        </button>
        <div className="min-w-0 flex-1">
          {label && <div className="mb-1 truncate text-xs text-muted-foreground">{label}</div>}
          <div
            className="relative h-2 cursor-pointer rounded-full bg-muted"
            onClick={(e) => {
              const r = e.currentTarget.getBoundingClientRect();
              ctl.seek(((e.clientX - r.left) / r.width) * (ctl.duration || 0));
            }}
          >
            <div className="absolute inset-y-0 left-0 rounded-full bg-primary transition-[width] duration-150" style={{ width: `${pct}%` }} />
          </div>
          <div className="mt-1 flex justify-between text-[11px] text-muted-foreground tabular">
            <span>{fmt(ctl.time)}</span>
            <span>{ctl.blocked ? "タップして再生" : `再生 ${ctl.plays} 回`}</span>
            <span>{fmt(ctl.duration)}</span>
          </div>
        </div>
        <button type="button" onClick={ctl.replay} className="flex size-10 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground" aria-label="最初から (R)" title="最初から (R)">
          <RotateCcw className="size-4" />
        </button>
        <button
          type="button"
          onClick={ctl.toggleSlow}
          className={cn("flex h-10 items-center gap-1 rounded-lg px-2 text-xs", ctl.slow ? "bg-brand-soft text-primary" : "text-muted-foreground hover:bg-muted")}
          aria-pressed={ctl.slow}
          title="ゆっくり再生 (S)"
        >
          <Snail className="size-4" />
          <span className="hidden sm:inline">0.75x</span>
        </button>
      </div>
    </div>
  );
}

export function Kbd({ children }: { children: React.ReactNode }) {
  return <kbd className="hidden rounded border bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground md:inline">{children}</kbd>;
}
