"use client";

import { CircleAlert, FileAudio, Loader2, ShieldCheck, Trash2, Upload } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { api, LEVEL_LABEL, type ImportJob } from "@/lib/api";
import { cn } from "@/lib/utils";

type Out = { imports: ImportJob[]; asr: { available: boolean; message: string; model: string } };

const STATUS: Record<ImportJob["status"], string> = { queued: "待機中", processing: "処理中", done: "完了", failed: "失敗" };

/** Import a NotebookLM Audio Overview (or any English audio) as listening material. */
export function ImportPanel({ level, onDone }: { level: number; onDone: () => void }) {
  const [data, setData] = useState<Out | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [lv, setLv] = useState(level);
  const [script, setScript] = useState("");
  const [showScript, setShowScript] = useState(false);
  const [uploading, setUploading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const wasRunning = useRef(false);

  const load = useCallback(() => api<Out>("/v1/imports").then(setData).catch(() => {}), []);
  useEffect(() => {
    load();
  }, [load]);
  const running = !!data?.imports.some((j) => j.status === "queued" || j.status === "processing");
  useEffect(() => {
    if (wasRunning.current && !running) onDone();
    wasRunning.current = running;
    if (!running) return;
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [running, load, onDone]);

  const upload = async () => {
    if (!file) return;
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      fd.append("title", title.trim() || file.name.replace(/\.[^.]+$/, "").slice(0, 60) || "NotebookLM");
      fd.append("level", String(lv));
      if (script.trim()) fd.append("script", script);
      const res = await fetch("/api/v1/imports/notebooklm", { method: "POST", body: fd });
      const j = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(typeof j.detail === "string" ? j.detail : `アップロードに失敗しました (${res.status})`);
      setFile(null);
      setTitle("");
      setScript("");
      if (inputRef.current) inputRef.current.value = "";
      toast.success("取り込みを始めました。文字起こしには音声の長さの数分の1ほどかかります");
      load();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setUploading(false);
    }
  };

  const remove = async (id: string) => {
    try {
      await api(`/v1/imports/${id}`, { method: "DELETE" });
      load();
      onDone();
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  return (
    <section className="rounded-2xl border bg-card p-4 md:p-5">
      <h2 className="flex items-center gap-2 text-sm font-medium">
        <FileAudio className="size-4 text-primary" /> NotebookLM の音声を取り込む
      </h2>
      <p className="mt-1 text-xs text-muted-foreground">
        NotebookLM の「音声解説」をダウンロードしたファイル（m4a・wav・mp3 など、60分・300MB まで）を、約90秒ずつの教材に分けて取り込みます。
        Mac 上の音声認識（faster-whisper）で文字起こしと区切りを作り、外部には送信しません。確信度の低い部分は書き取りの正解が誤るおそれがあるため除外します。
      </p>

      {data && !data.asr.available ? (
        <p className="mt-3 rounded-xl bg-warning-soft p-3 text-sm">{data.asr.message}</p>
      ) : (
        <div className="mt-4 space-y-3">
          <div className="flex flex-col gap-2 sm:flex-row">
            <input
              ref={inputRef}
              type="file"
              accept="audio/*,.m4a,.wav,.mp3,.aac,.ogg,.webm,.mp4"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              className="text-sm file:mr-3 file:rounded-lg file:border file:bg-transparent file:px-3 file:py-1.5 file:text-sm hover:file:bg-muted"
            />
          </div>
          {file && (
            <>
              <div className="grid gap-2 sm:grid-cols-[1fr_auto]">
                <Input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={60} placeholder={`タイトル（省略時: ${file.name}）`} className="h-9" />
                <div className="inline-flex rounded-lg border p-0.5">
                  {[1, 2, 3, 4, 5].map((l) => (
                    <button key={l} type="button" onClick={() => setLv(l)} className={cn("rounded-md px-2.5 py-1 text-xs", lv === l ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted")}>
                      {LEVEL_LABEL[l]}
                    </button>
                  ))}
                </div>
              </div>
              {showScript ? (
                <Textarea
                  value={script}
                  onChange={(e) => setScript(e.target.value)}
                  rows={5}
                  placeholder="台本（英文）を貼り付けると、文字は台本を使い、音声認識は区切りの時刻だけに使います。音声と一致しない台本は取り込みません。"
                />
              ) : (
                <button type="button" onClick={() => setShowScript(true)} className="text-xs text-primary underline-offset-2 hover:underline">
                  台本（英文）がある場合は貼り付ける
                </button>
              )}
              <Button onClick={upload} disabled={uploading}>
                {uploading ? <Loader2 className="size-4 animate-spin" /> : <Upload className="size-4" />} 取り込む
              </Button>
            </>
          )}
        </div>
      )}

      {data && data.imports.length > 0 && (
        <ul className="mt-4 divide-y border-t">
          {data.imports.map((j) => (
            <li key={j.id} className="py-3 text-sm">
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <div className="truncate font-medium">{j.title}</div>
                  <div className="text-xs text-muted-foreground">
                    {j.info.filename} · {Math.round(j.info.duration / 60)}分{Math.round(j.info.duration % 60)}秒 · レベル {LEVEL_LABEL[j.level]}
                    {j.info.has_script && " · 台本あり"}
                  </div>
                </div>
                <span
                  className={cn(
                    "inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-xs",
                    j.status === "done" ? "bg-success-soft" : j.status === "failed" ? "bg-danger-soft" : "bg-muted",
                  )}
                >
                  {(j.status === "queued" || j.status === "processing") && <Loader2 className="size-3 animate-spin" />}
                  {j.status === "processing" ? j.info.step : STATUS[j.status]}
                </span>
                {j.status !== "queued" && j.status !== "processing" && (
                  <button type="button" onClick={() => remove(j.id)} aria-label="取り込みと教材を削除" className="rounded p-1 text-muted-foreground hover:bg-muted hover:text-destructive">
                    <Trash2 className="size-4" />
                  </button>
                )}
              </div>
              {j.error && <p className="mt-1 text-xs text-destructive">{j.error}</p>}
              {j.status === "done" && (
                <p className="mt-1 text-xs text-muted-foreground">
                  {j.info.clips.length}本の教材を作成（問題 {j.info.clips.reduce((n, c) => n + c.questions, 0)}問
                  {j.info.clips.some((c) => c.words.length) && ` · TOEIC 語彙: ${[...new Set(j.info.clips.flatMap((c) => c.words))].slice(0, 8).join(", ")}`}）。次の学習の「初見リスニング」で出題されます。
                </p>
              )}
              {(j.info.checks.length > 0 || j.info.skipped.length > 0) && (
                <ul className="mt-1 space-y-0.5 text-xs">
                  {j.info.checks.map((c) => (
                    <li key={c.check} className={cn("flex gap-1.5", c.ok ? "text-muted-foreground" : "text-destructive")}>
                      {c.ok ? <ShieldCheck className="mt-px size-3.5 shrink-0 text-success" /> : <CircleAlert className="mt-px size-3.5 shrink-0" />}
                      {c.check}（{c.detail}）
                    </li>
                  ))}
                  {j.info.skipped.map((s) => (
                    <li key={s.part} className="flex gap-1.5 text-muted-foreground">
                      <CircleAlert className="mt-px size-3.5 shrink-0 text-warning" />
                      {s.range}: {s.reason}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
