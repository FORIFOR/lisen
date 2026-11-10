"use client";

import { Check, Loader2, Lock, Plus, Trash2, Users } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { AudioPlayer, useAudio } from "@/components/audio-player";
import { Button } from "@/components/ui/button";
import { ImportPanel } from "@/components/import-panel";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { api, LEVEL_LABEL, type ClipPublic } from "@/lib/api";
import { cn } from "@/lib/utils";

type Item = {
  id: string; title: string; level: number; kind: string; speakers: number; lines: number; duration: number;
  source: string; has_audio: boolean; heard: boolean; times: number; best: number | null; locked: boolean;
};
type Lib = { items: Item[]; tts: { available: boolean; message: string }; voices: string[]; level?: number };

function ClipDialog({ id, onClose }: { id: string | null; onClose: () => void }) {
  const [clip, setClip] = useState<(ClipPublic & { heard: boolean; vocab: { word: string; ja: string }[] }) | null>(null);
  useEffect(() => {
    if (id) api<typeof clip>(`/library/${id}`).then(setClip).catch((e) => toast.error(e.message));
  }, [id]);
  const ctl = useAudio(clip?.heard ? clip.audio : null);
  return (
    <Dialog open={!!id} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[85dvh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>{clip?.title ?? "…"}</DialogTitle>
          <DialogDescription>{clip && `レベル ${LEVEL_LABEL[clip.level]} · ${clip.segments.length} 文`}</DialogDescription>
        </DialogHeader>
        {clip && !clip.heard && <p className="text-sm text-muted-foreground">まだ聞いていない教材です。初見の状態を保つため、トレーニングで出題されるまで内容は表示されません。</p>}
        {clip?.heard && (
          <div className="space-y-3">
            <AudioPlayer ctl={ctl} compact />
            <ol className="space-y-1">
              {clip.segments.map((s) => (
                <li key={s.id}>
                  <button type="button" onClick={() => ctl.play(s.start)} className="flex w-full gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-muted">
                    {clip.speakers > 1 && <span className="text-xs font-semibold text-primary">{s.speaker}</span>}
                    {s.text}
                  </button>
                </li>
              ))}
            </ol>
            {clip.vocab.length > 0 && (
              <div className="flex flex-wrap gap-2">
                {clip.vocab.map((v) => (
                  <span key={v.word} className="rounded-full bg-muted px-2.5 py-1 text-xs">
                    <strong>{v.word}</strong> {v.ja}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function AddDialog({ open, onClose, lib, onAdded }: { open: boolean; onClose: () => void; lib: Lib; onAdded: () => void }) {
  const [title, setTitle] = useState("");
  const [level, setLevel] = useState(3);
  const [script, setScript] = useState("A: \nB: ");
  const [voices, setVoices] = useState<Record<string, string>>({ A: "Samantha", B: "Daniel", C: "Karen", D: "Moira" });
  const [q, setQ] = useState({ q: "", c0: "", c1: "", c2: "", c3: "", answer: 0, explain: "" });
  const [vocab, setVocab] = useState("");
  const [busy, setBusy] = useState(false);

  const lines = script
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => {
      const m = l.match(/^([A-Da-d])\s*[:：]\s*(.+)$/);
      return m ? { speaker: m[1].toUpperCase(), text: m[2].trim() } : { speaker: "A", text: l };
    })
    .filter((l) => l.text);
  const used = Array.from(new Set(lines.map((l) => l.speaker))).sort();

  const save = async () => {
    setBusy(true);
    try {
      const choices = [q.c0, q.c1, q.c2, q.c3].filter((c) => c.trim());
      const body = {
        title,
        level,
        kind: used.length > 1 ? "dialogue" : "monologue",
        voices: Object.fromEntries(used.map((s) => [s, voices[s]])),
        lines,
        questions: q.q.trim() && choices.length >= 2 ? [{ q: q.q, choices, answer: Math.min(q.answer, choices.length - 1), explain: q.explain }] : [],
        vocab: vocab
          .split("\n")
          .map((l) => l.split(/[,，=:：]/).map((x) => x.trim()))
          .filter((p) => p.length >= 2 && p[0] && p[1])
          .map((p) => [p[0], p[1]]),
      };
      await api("/library", { body });
      toast.success("教材を追加しました。次の初見リスニングで優先的に出題されます");
      onAdded();
      onClose();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const field = "w-full rounded-lg border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40";
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>教材を追加</DialogTitle>
          <DialogDescription>英文スクリプトから、この Mac の音声合成で音声を作ります。スクリプトがそのまま採点の正解になります。</DialogDescription>
        </DialogHeader>
        {!lib.tts.available && <p className="rounded-lg bg-danger-soft px-3 py-2 text-sm">{lib.tts.message}</p>}
        <div className="space-y-3">
          <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
            <input className={field} placeholder="タイトル（例: 会議の日程調整）" value={title} onChange={(e) => setTitle(e.target.value)} />
            <select className={field} value={level} onChange={(e) => setLevel(Number(e.target.value))}>
              {[1, 2, 3, 4, 5].map((l) => (
                <option key={l} value={l}>
                  レベル {LEVEL_LABEL[l]}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className="text-xs text-muted-foreground">スクリプト（1行1文。会話は「A: 」「B: 」で話者を指定）</label>
            <textarea className={cn(field, "mt-1 font-mono")} rows={7} value={script} onChange={(e) => setScript(e.target.value)} />
          </div>
          {used.length > 0 && (
            <div className="flex flex-wrap gap-3">
              {used.map((s) => (
                <label key={s} className="flex items-center gap-2 text-sm">
                  話者 {s}
                  <select className={cn(field, "w-auto")} value={voices[s]} onChange={(e) => setVoices({ ...voices, [s]: e.target.value })}>
                    {lib.voices.map((v) => (
                      <option key={v}>{v}</option>
                    ))}
                  </select>
                </label>
              ))}
            </div>
          )}
          <details className="rounded-lg border p-3">
            <summary className="cursor-pointer text-sm">内容理解の問題（任意）</summary>
            <div className="mt-3 space-y-2">
              <input className={field} placeholder="質問（日本語可）" value={q.q} onChange={(e) => setQ({ ...q, q: e.target.value })} />
              {[0, 1, 2, 3].map((i) => (
                <label key={i} className="flex items-center gap-2">
                  <input type="radio" name="ans" checked={q.answer === i} onChange={() => setQ({ ...q, answer: i })} aria-label={`選択肢${i + 1}を正解にする`} />
                  <input className={field} placeholder={`選択肢 ${i + 1}`} value={q[`c${i}` as "c0"]} onChange={(e) => setQ({ ...q, [`c${i}`]: e.target.value })} />
                </label>
              ))}
              <input className={field} placeholder="解説（任意）" value={q.explain} onChange={(e) => setQ({ ...q, explain: e.target.value })} />
            </div>
          </details>
          <details className="rounded-lg border p-3">
            <summary className="cursor-pointer text-sm">重要語彙（任意・1行に「単語, 意味」）</summary>
            <textarea className={cn(field, "mt-2")} rows={3} value={vocab} onChange={(e) => setVocab(e.target.value)} placeholder={"schedule, 予定\ndeadline, 締め切り"} />
          </details>
        </div>
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            キャンセル
          </Button>
          <Button onClick={save} disabled={busy || !title.trim() || !lines.length || !lib.tts.available}>
            {busy && <Loader2 className="size-4 animate-spin" />}
            音声を作成して追加
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

export default function LibraryPage() {
  const [lib, setLib] = useState<Lib | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [level, setLevel] = useState(2);
  useEffect(() => {
    api<{ level: number }>("/profile").then((p) => setLevel(p.level)).catch(() => {});
  }, []);
  const load = () => api<Lib>("/library").then(setLib).catch((e) => toast.error(e.message));
  useEffect(() => {
    load();
  }, []);
  if (!lib)
    return (
      <div className="flex justify-center py-24 text-muted-foreground">
        <Loader2 className="size-6 animate-spin" />
      </div>
    );

  const heard = lib.items.filter((i) => i.heard).length;
  return (
    <div className="space-y-5">
      <div className="flex items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">教材</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {lib.items.length} 本中 {heard} 本を学習済み · 未聴 {lib.items.length - heard} 本
          </p>
        </div>
        <Button onClick={() => setAdding(true)}>
          <Plus className="size-4" /> 教材を追加
        </Button>
      </div>
      <ImportPanel level={level} onDone={load} />
      {[1, 2, 3, 4, 5].map((lv) => {
        const items = lib.items.filter((i) => i.level === lv);
        if (!items.length) return null;
        return (
          <section key={lv}>
            <h2 className="mb-2 text-sm font-medium text-muted-foreground">
              レベル {LEVEL_LABEL[lv]}
            </h2>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {items.map((i) => (
                <button key={i.id} type="button" onClick={() => setOpen(i.id)} className="flex items-center gap-3 rounded-xl border bg-card px-3 py-2.5 text-left hover:bg-muted/50">
                  <span className={cn("flex size-7 shrink-0 items-center justify-center rounded-full", i.heard ? "bg-success-soft text-success" : "bg-muted text-muted-foreground")}>
                    {i.heard ? <Check className="size-4" /> : i.locked ? <Lock className="size-3.5" /> : <span className="text-[10px]">新</span>}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-medium">{i.title}</span>
                    <span className="flex items-center gap-1 text-xs text-muted-foreground">
                      {i.speakers > 1 && <Users className="size-3" />}
                      {i.lines}文 · {Math.round(i.duration)}秒{i.source === "user" && " · 追加教材"}
                      {i.best != null && ` · 理解 ${Math.round(i.best * 100)}%`}
                    </span>
                  </span>
                  {i.source === "user" && (
                    <span
                      role="button"
                      tabIndex={0}
                      aria-label="削除"
                      onClick={async (e) => {
                        e.stopPropagation();
                        await api(`/library/${i.id}`, { method: "DELETE" }).catch((err) => toast.error(err.message));
                        load();
                      }}
                      className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-destructive"
                    >
                      <Trash2 className="size-4" />
                    </span>
                  )}
                </button>
              ))}
            </div>
          </section>
        );
      })}
      <ClipDialog key={open ?? "none"} id={open} onClose={() => setOpen(null)} />
      <AddDialog open={adding} onClose={() => setAdding(false)} lib={lib} onAdded={load} />
    </div>
  );
}
