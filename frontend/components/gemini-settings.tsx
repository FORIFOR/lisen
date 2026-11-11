"use client";

import { CheckCircle2, ExternalLink, KeyRound, Loader2, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, type GeneratorStatus, type Settings } from "@/lib/api";
import { cn } from "@/lib/utils";

const MODELS = [
  { id: "gemini-flash-latest", label: "Flash（最新）", hint: "標準" },
  { id: "gemini-flash-lite-latest", label: "Flash-Lite（最新）", hint: "安い・速い" },
  { id: "gemini-pro-latest", label: "Pro（最新）", hint: "高品質・高い" },
];
const ENGINES: { id: Settings["lesson_engine"]; label: string; hint: string }[] = [
  { id: "auto", label: "自動", hint: "Gemini を使い、失敗したらローカル生成" },
  { id: "gemini", label: "Gemini のみ", hint: "失敗したら教材を作らない" },
  { id: "local", label: "ローカルのみ", hint: "外部に送信しない" },
];

/** Gemini API key (write-only: the server keeps it and only returns the last 4 characters), model and engine. */
export function GeminiSettings({ settings, update }: { settings: Settings; update: (patch: Partial<Settings>) => Promise<void> }) {
  const [st, setSt] = useState<GeneratorStatus | null>(null);
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState<"save" | "test" | "delete" | null>(null);
  const [tested, setTested] = useState<string | null>(null);

  useEffect(() => {
    api<GeneratorStatus>("/v1/integrations/gemini").then(setSt).catch(() => {});
  }, []);

  const run = async (kind: "save" | "test" | "delete", fn: () => Promise<GeneratorStatus & { display_name?: string }>) => {
    setBusy(kind);
    setTested(null);
    try {
      const r = await fn();
      setSt(r);
      return r;
    } catch (e) {
      toast.error((e as Error).message);
      return null;
    } finally {
      setBusy(null);
    }
  };
  const test = () =>
    run("test", () => api("/v1/integrations/gemini/test", { body: {} })).then((r) => {
      if (r) setTested(`接続できました（${r.display_name ?? r.gemini_model}）`);
    });
  const save = async () => {
    const r = await run("save", () => api("/v1/integrations/gemini", { method: "PUT", body: { api_key: key.trim() } }));
    if (r) {
      setKey("");
      toast.success("API キーを保存しました");
      test();
    }
  };
  const remove = () => run("delete", () => api("/v1/integrations/gemini", { method: "DELETE" }));
  const setModel = async (m: string) => {
    await update({ gemini_model: m });
    setSt((s) => (s ? { ...s, gemini_model: m } : s));
    setTested(null);
  };
  const setEngine = async (m: Settings["lesson_engine"]) => {
    await update({ lesson_engine: m });
    api<GeneratorStatus>("/v1/integrations/gemini").then(setSt).catch(() => {});
  };

  const model = st?.gemini_model ?? settings.gemini_model ?? "gemini-flash-latest";

  return (
    <section className="rounded-2xl border bg-card p-4 md:p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-medium">Gemini（音声教材の会話文）</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            苦手な単語を使った自然な会話文を Gemini に書かせます。送信するのは対象の単語と難易度だけで、学習履歴は送りません。音声は Mac で合成します。料金は Google の従量課金（無料枠あり）です。
          </p>
        </div>
        {st && (
          <span className={cn("shrink-0 rounded-full px-2 py-0.5 text-xs", st.engine === "gemini" ? "bg-success-soft" : "bg-muted text-muted-foreground")}>
            {st.engine === "gemini" ? "Gemini で生成" : "ローカル生成"}
          </span>
        )}
      </div>

      {!st ? (
        <Loader2 className="mt-4 size-5 animate-spin text-muted-foreground" />
      ) : (
        <div className="mt-4 space-y-4">
          <div>
            <div className="mb-1.5 text-xs font-medium text-muted-foreground">API キー</div>
            {st.gemini_configured ? (
              <div className="flex flex-wrap items-center gap-2">
                <span className="inline-flex items-center gap-1.5 rounded-lg bg-muted px-2.5 py-1.5 text-sm">
                  <KeyRound className="size-3.5" /> 登録済み <code className="tabular">{st.key_hint}</code>
                  {st.key_source === "env" && <span className="text-xs text-muted-foreground">（環境変数）</span>}
                </span>
                <Button variant="outline" size="sm" onClick={test} disabled={!!busy}>
                  {busy === "test" && <Loader2 className="size-3.5 animate-spin" />} 接続テスト
                </Button>
                {st.key_source === "file" && (
                  <Button variant="ghost" size="sm" onClick={remove} disabled={!!busy}>
                    <Trash2 className="size-3.5" /> 削除
                  </Button>
                )}
              </div>
            ) : null}
            {st.key_source !== "env" && (
              <form
                className={cn("flex gap-2", st.gemini_configured && "mt-2")}
                onSubmit={(e) => {
                  e.preventDefault();
                  if (key.trim()) save();
                }}
              >
                <Input
                  type="password"
                  autoComplete="off"
                  spellCheck={false}
                  value={key}
                  onChange={(e) => setKey(e.target.value)}
                  placeholder={st.gemini_configured ? "新しいキーに置き換える" : "AIza… で始まるキーを貼り付け"}
                  className="h-9 font-mono"
                  aria-label="Gemini API キー"
                />
                <Button type="submit" className="h-9" disabled={!key.trim() || !!busy}>
                  {busy === "save" && <Loader2 className="size-3.5 animate-spin" />} 保存
                </Button>
              </form>
            )}
            {tested && (
              <p className="mt-2 flex items-center gap-1.5 text-xs text-success">
                <CheckCircle2 className="size-3.5" /> {tested}
              </p>
            )}
            <p className="mt-2 text-xs text-muted-foreground">
              キーは{" "}
              <a href="https://aistudio.google.com/apikey" target="_blank" rel="noreferrer" className="inline-flex items-center gap-0.5 underline underline-offset-2">
                Google AI Studio <ExternalLink className="size-3" />
              </a>{" "}
              で作成できます。保存したキーはこの Mac の data/secrets に保存され、画面やエクスポートには表示されません（末尾4文字のみ）。
            </p>
          </div>

          <div>
            <div className="mb-1.5 text-xs font-medium text-muted-foreground">モデル</div>
            <div className="flex flex-wrap gap-1.5">
              {MODELS.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  onClick={() => setModel(m.id)}
                  className={cn("rounded-lg border px-3 py-1.5 text-left text-sm", model === m.id ? "border-primary bg-brand-soft" : "hover:bg-muted")}
                >
                  {m.label} <span className="text-xs text-muted-foreground">{m.hint}</span>
                </button>
              ))}
            </div>
          </div>

          <div>
            <div className="mb-1.5 text-xs font-medium text-muted-foreground">教材の音声</div>
            <div className="grid gap-1.5 sm:grid-cols-2">
              {([
                ["mac", "Mac の音声合成", "無料・オフライン。6種類の声"],
                ["gemini", "Gemini TTS", `男性1人＋女性1人の会話（${st.gemini_tts_model}）。1行ごとに呼び出し`],
              ] as const).map(([id, label, hint]) => (
                <button
                  key={id}
                  type="button"
                  disabled={id === "gemini" && !st.gemini_configured}
                  onClick={async () => {
                    await update({ lesson_voice: id });
                    api<GeneratorStatus>("/v1/integrations/gemini").then(setSt).catch(() => {});
                  }}
                  className={cn("rounded-lg border px-3 py-2 text-left text-sm disabled:opacity-50", (st.voice_setting ?? "mac") === id ? "border-primary bg-brand-soft" : "hover:bg-muted")}
                >
                  <div className="font-medium">{label}</div>
                  <div className="text-xs text-muted-foreground">{hint}</div>
                </button>
              ))}
            </div>
            {(st.voice_setting ?? "mac") === "gemini" && (
              <div className="mt-2 grid grid-cols-2 gap-2">
                {([
                  ["gemini_voice_male", "男性の声", st.gemini_voice_male],
                  ["gemini_voice_female", "女性の声", st.gemini_voice_female],
                ] as const).map(([field, label, value]) => (
                  <label key={field} className="text-xs text-muted-foreground">
                    {label}
                    <select
                      value={value}
                      onChange={async (e) => {
                        const v = e.target.value;
                        await update({ [field]: v } as Partial<Settings>);
                        setSt((s) => (s ? { ...s, [field]: v } : s));
                      }}
                      className="mt-1 block h-9 w-full rounded-lg border bg-background px-2 text-sm text-foreground"
                    >
                      {st.gemini_voices.map((v) => (
                        <option key={v} value={v}>
                          {v}
                        </option>
                      ))}
                    </select>
                  </label>
                ))}
              </div>
            )}
            <p className="mt-1.5 text-xs text-muted-foreground">
              どちらの音声も、音声認識で台本どおりか・指定語が聞き取れるかを確認してから採用します。Gemini TTS が使えないときは、「自動」なら Mac の音声に切り替えます。
            </p>
          </div>

          <div>
            <div className="mb-1.5 text-xs font-medium text-muted-foreground">教材の作り方</div>
            <div className="grid gap-1.5 sm:grid-cols-3">
              {ENGINES.map((m) => (
                <button
                  key={m.id}
                  type="button"
                  onClick={() => setEngine(m.id)}
                  className={cn("rounded-lg border px-3 py-2 text-left text-sm", (st.mode ?? "auto") === m.id ? "border-primary bg-brand-soft" : "hover:bg-muted")}
                >
                  <div className="font-medium">{m.label}</div>
                  <div className="text-xs text-muted-foreground">{m.hint}</div>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
