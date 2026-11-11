"use client";

import { useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { Download, Laptop, Loader2, Monitor, Moon, Smartphone, Sun } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { AccessSettings } from "@/components/access-settings";
import { GeminiSettings } from "@/components/gemini-settings";
import { InterestPicker } from "@/components/interest-picker";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { api, type Profile, type Settings } from "@/lib/api";
import { cn } from "@/lib/utils";

type System = { urls: { label: string; url: string }[]; tailscale: string | null; hostname: string };

function Row({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2 border-b py-4 last:border-0 sm:flex-row sm:items-center sm:justify-between">
      <div>
        <div className="text-sm font-medium">{label}</div>
        {hint && <div className="text-xs text-muted-foreground">{hint}</div>}
      </div>
      <div className="flex flex-wrap gap-1.5">{children}</div>
    </div>
  );
}

function Seg<T extends string | number>({ value, options, onChange, fmt }: { value: T; options: T[]; onChange: (v: T) => void; fmt?: (v: T) => string }) {
  return (
    <div className="inline-flex rounded-lg border p-0.5">
      {options.map((o) => (
        <button key={String(o)} type="button" onClick={() => onChange(o)} className={cn("rounded-md px-3 py-1.5 text-sm tabular", value === o ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted")}>
          {fmt ? fmt(o) : String(o)}
        </button>
      ))}
    </div>
  );
}

export default function SettingsPage() {
  const router = useRouter();
  const { theme, setTheme } = useTheme();
  const [p, setP] = useState<Profile | null>(null);
  const [sys, setSys] = useState<System | null>(null);
  useEffect(() => {
    api<Profile>("/profile").then(setP).catch(() => {});
    api<System>("/system").then(setSys).catch(() => {});
  }, []);

  const update = async (patch: Partial<Settings>) => {
    try {
      const s = await api<Settings>("/settings", { method: "PUT", body: patch });
      setP((prev) => (prev ? { ...prev, settings: s } : prev));
    } catch (e) {
      toast.error((e as Error).message);
    }
  };
  const takeTest = async (form: string) => {
    try {
      await api("/sessions", { body: { mode: "test", form, minutes: 0, replace: true } });
      router.push("/session");
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  if (!p)
    return (
      <div className="flex justify-center py-24 text-muted-foreground">
        <Loader2 className="size-6 animate-spin" />
      </div>
    );
  const s = p.settings;

  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-semibold tracking-tight">設定</h1>

      <section className="rounded-2xl border bg-card px-4 md:px-5">
        <Row label="毎日の目標" hint="ホームの達成リングに使います">
          <Seg value={s.daily_goal} options={[15, 30, 45, 60]} onChange={(v) => update({ daily_goal: v })} fmt={(v) => `${v}分`} />
        </Row>
        <Row label="長時間学習での休憩" hint="60分以上のプランに休憩を自動で挟みます">
          <Switch checked={s.break_enabled} onCheckedChange={(v) => update({ break_enabled: v })} />
        </Row>
        {s.break_enabled && (
          <>
            <Row label="休憩の長さ">
              <Seg value={s.break_minutes} options={[5, 7, 10]} onChange={(v) => update({ break_minutes: v })} fmt={(v) => `${v}分`} />
            </Row>
            <Row label="休憩の間隔">
              <Seg value={s.break_every} options={[30, 45, 60]} onChange={(v) => update({ break_every: v })} fmt={(v) => `${v}分ごと`} />
            </Row>
          </>
        )}
        <Row label="標準の再生速度" hint="ゆっくり再生ボタンはこの速度の 0.75 倍">
          <Seg value={s.playback_rate} options={[0.9, 1, 1.1, 1.25]} onChange={(v) => update({ playback_rate: v })} fmt={(v) => `${v}x`} />
        </Row>
        <Row label="苦手語彙の音声教材を自動で作る" hint="語彙診断や学習のあと、未学習の教材がなければ1本だけ作ります">
          <Switch checked={s.auto_lessons} onCheckedChange={(v) => update({ auto_lessons: v })} />
        </Row>
        <div className="border-b py-4">
          <div className="text-sm font-medium">興味のある分野</div>
          <div className="mb-2 text-xs text-muted-foreground">初見リスニングとホームの「おすすめ」で優先します（選んだ順）</div>
          <InterestPicker value={s.interests ?? []} onChange={(v) => update({ interests: v })} />
        </div>
        <Row label="表示テーマ">
          <Seg
            value={(theme ?? "system") as string}
            options={["system", "light", "dark"]}
            onChange={setTheme}
            fmt={(v) => ({ system: "自動", light: "ライト", dark: "ダーク" })[v] ?? v}
          />
        </Row>
      </section>

      <GeminiSettings settings={s} update={update} />

      <section className="rounded-2xl border bg-card p-4 md:p-5">
        <h2 className="text-sm font-medium">ほかの端末から使う</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          学習データはこの Mac に1か所で保存されます。Windows や iPhone のブラウザで下のアドレスを開き、アクセスコードで端末を登録すると、同じ学習履歴の続きから学習できます（Mac がスリープ中は接続できません）。
        </p>
        <ul className="mt-3 space-y-2 text-sm">
          {sys?.urls.map((u) => (
            <li key={u.url} className="flex items-center gap-2">
              <span className="w-28 shrink-0 text-xs text-muted-foreground">{u.label}</span>
              <code className="truncate rounded bg-muted px-2 py-1">{u.url}</code>
            </li>
          ))}
        </ul>
        <div className="mt-4 grid gap-2 text-xs text-muted-foreground sm:grid-cols-3">
          <div className="flex gap-2 rounded-xl bg-muted/60 p-3">
            <Laptop className="size-4 shrink-0" /> Mac: http://localhost:3210
          </div>
          <div className="flex gap-2 rounded-xl bg-muted/60 p-3">
            <Monitor className="size-4 shrink-0" /> Windows: 同じ Wi-Fi か Tailscale のアドレスをブラウザで開く
          </div>
          <div className="flex gap-2 rounded-xl bg-muted/60 p-3">
            <Smartphone className="size-4 shrink-0" /> iPhone: Safari で開き「共有 → ホーム画面に追加」でアプリのように使えます
          </div>
        </div>
      </section>

      <AccessSettings />

      <section className="rounded-2xl border bg-card px-4 md:px-5">
        {p.tests.map((t) => (
          <Row key={t.form} label={t.name} hint={t.taken ? `受験済み · スコア ${Math.round(t.score ?? 0)}` : `Day ${t.day} から受験できます`}>
            <Button variant="outline" size="sm" onClick={() => takeTest(t.form)}>
              {t.taken ? "もう一度受ける" : t.available ? "受ける" : "前倒しで受ける"}
            </Button>
          </Row>
        ))}
        <Row label="学習データのエクスポート" hint="すべての学習履歴を JSON で保存（バックアップ用）">
          <a href="/api/export" download className="inline-flex h-7 items-center gap-1 rounded-lg border px-2.5 text-[0.8rem] hover:bg-muted">
            <Download className="size-3.5" /> ダウンロード
          </a>
        </Row>
      </section>
      <p className="text-center text-xs text-muted-foreground">
        <Sun className="mr-1 inline size-3" />
        <Moon className="mr-1 inline size-3" />
        キーボード: Space 再生 / R 最初から / S ゆっくり / 1–4 選択 / Enter 決定・次へ / P 一時停止 / 入力中は Tab で再生
      </p>
    </div>
  );
}
