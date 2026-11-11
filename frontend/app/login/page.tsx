"use client";

import { KeyRound, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { Logo } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";

function guessName() {
  const ua = navigator.userAgent;
  if (/iPhone/.test(ua)) return "iPhone";
  if (/iPad/.test(ua)) return "iPad";
  if (/Android/.test(ua)) return "Android";
  if (/Windows/.test(ua)) return "Windows";
  if (/Macintosh/.test(ua)) return "Mac";
  return "端末";
}

/** only paths inside this app, never another site */
function safeNext() {
  const n = new URLSearchParams(window.location.search).get("next") ?? "/";
  return n.startsWith("/") && !n.startsWith("//") && !n.startsWith("/login") ? n : "/";
}

export default function LoginPage() {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<{ signed_in: boolean }>("/auth/status")
      .then((s) => {
        if (s.signed_in) window.location.replace(safeNext());
      })
      .catch(() => {});
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/auth/login", { body: { code, name: name.trim() || guessName() } });
      window.location.replace(safeNext());
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto flex min-h-[70vh] max-w-sm flex-col justify-center gap-6 py-10">
      <Logo />
      <div>
        <h1 className="text-xl font-semibold tracking-tight">この端末を登録</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Mac で <code className="rounded bg-muted px-1">./start.sh</code> を実行したときに表示されるアクセスコードを入力してください。登録済みの端末の「設定 → 端末とアクセス」でも確認できます。
        </p>
      </div>
      <form onSubmit={submit} className="space-y-4">
        <label className="block space-y-1.5">
          <span className="text-sm font-medium">アクセスコード</span>
          <Input
            value={code}
            onChange={(e) => setCode(e.target.value)}
            inputMode="numeric"
            autoComplete="one-time-code"
            placeholder="0000-0000-0000"
            className="h-12 text-center font-mono text-lg tracking-widest"
            autoFocus
          />
        </label>
        <label className="block space-y-1.5">
          <span className="text-sm font-medium">端末の名前</span>
          <Input value={name} onChange={(e) => setName(e.target.value)} maxLength={60} placeholder="空欄なら機種名（iPhone など）" className="h-11" />
        </label>
        {error && <p className="text-sm text-destructive">{error}</p>}
        <Button type="submit" size="lg" className="h-12 w-full" disabled={busy || code.replace(/\D/g, "").length < 12}>
          {busy ? <Loader2 className="size-4 animate-spin" /> : <KeyRound className="size-4" />} 登録して始める
        </Button>
      </form>
      <p className="text-xs text-muted-foreground">一度登録すると、この端末のブラウザでは次から入力は不要です（約1年有効）。</p>
    </div>
  );
}
