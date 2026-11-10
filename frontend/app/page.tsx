"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion } from "motion/react";
import { ArrowRight, ClipboardCheck, Flame, Languages, Loader2, Minus, Play, Plus, RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { api, ApiError, type Block, type Profile } from "@/lib/api";
import { cn } from "@/lib/utils";

const PRESETS = [15, 30, 60, 90, 120];

function GoalRing({ value, goal }: { value: number; goal: number }) {
  const pct = Math.min(1, value / goal);
  const r = 34;
  const c = 2 * Math.PI * r;
  return (
    <div className="relative size-24 shrink-0">
      <svg viewBox="0 0 80 80" className="size-24 -rotate-90">
        <circle cx="40" cy="40" r={r} className="fill-none stroke-muted" strokeWidth="7" />
        <motion.circle
          cx="40"
          cy="40"
          r={r}
          className="fill-none stroke-primary"
          strokeWidth="7"
          strokeLinecap="round"
          strokeDasharray={c}
          initial={{ strokeDashoffset: c }}
          animate={{ strokeDashoffset: c * (1 - pct) }}
          transition={{ duration: 0.8, ease: "easeOut" }}
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-xl font-semibold tabular">{Math.round(value)}</span>
        <span className="text-[10px] text-muted-foreground">/ {goal}分</span>
      </div>
    </div>
  );
}

export default function Home() {
  const router = useRouter();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [minutes, setMinutes] = useState(30);
  const [plan, setPlan] = useState<{ blocks: Block[]; study_minutes: number; break_minutes: number } | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Profile>("/profile")
      .then((p) => {
        if (!p.onboarded) router.replace("/onboarding");
        setProfile(p);
      })
      .catch((e) => setError(e.message));
  }, [router]);

  useEffect(() => {
    api<{ blocks: Block[]; study_minutes: number; break_minutes: number }>("/plan/preview", { body: { minutes } })
      .then(setPlan)
      .catch(() => {});
  }, [minutes]);

  const start = async (body: Record<string, unknown>, replace = false) => {
    setStarting(true);
    try {
      await api("/sessions", { body: { ...body, replace } });
      router.push("/session");
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        toast("進行中の学習があります", {
          description: "続きから再開するか、破棄して新しく始められます",
          action: { label: "破棄して開始", onClick: () => start(body, true) },
        });
      } else toast.error((e as Error).message);
      setStarting(false);
    }
  };

  if (error) {
    return (
      <div className="rounded-2xl border bg-card p-6">
        <h1 className="text-lg font-semibold">サーバーに接続できません</h1>
        <p className="mt-2 text-sm text-muted-foreground">{error}</p>
        <p className="mt-2 text-sm text-muted-foreground">Mac で <code className="rounded bg-muted px-1">./start.sh</code> を実行し、Mac がスリープしていないか確認してください。</p>
      </div>
    );
  }
  if (!profile) {
    return (
      <div className="flex justify-center py-24 text-muted-foreground">
        <Loader2 className="size-6 animate-spin" />
      </div>
    );
  }

  const goal = profile.settings.daily_goal;
  const done = profile.today_minutes >= goal;
  const active = profile.active_session;
  const dueTest = profile.tests.find((t) => t.available && !t.taken);

  return (
    <div className="space-y-6">
      <section className="flex items-center justify-between gap-4">
        <div>
          <p className="text-sm text-muted-foreground">
            Day {Math.min(profile.day, 999)} / {profile.curriculum_days} · {profile.phase.name}
          </p>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight md:text-3xl">{done ? "今日の目標を達成しました" : profile.today_minutes > 0 ? "その調子です。" : "今日も、少しずつ。"}</h1>
          <p className="mt-2 hidden max-w-md text-sm text-muted-foreground sm:block">{profile.phase.description}</p>
          <div className="mt-3 flex flex-wrap gap-2 text-xs">
            <span className="inline-flex items-center gap-1 rounded-full bg-warning-soft px-2.5 py-1">
              <Flame className="size-3.5 text-warning" /> {profile.streak}日連続
            </span>
            <span className="rounded-full border px-2.5 py-1">レベル {profile.level} · {profile.level_name}</span>
          </div>
        </div>
        <GoalRing value={profile.today_minutes} goal={goal} />
      </section>

      {active && (
        <motion.button
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          onClick={() => router.push("/session")}
          className="flex w-full items-center gap-4 rounded-2xl bg-primary p-4 text-left text-primary-foreground shadow-sm"
        >
          <span className="flex size-11 items-center justify-center rounded-full bg-primary-foreground/15">
            <Play className="size-5" />
          </span>
          <span className="flex-1">
            <span className="block font-medium">{active.mode === "test" ? "テストの続きから再開" : "続きから再開"}</span>
            <span className="text-sm opacity-80">
              {Math.round(active.study_seconds / 60)}分 学習済み · {active.blocks[active.block_index]?.label ?? ""}（別の端末で始めた学習も再開できます）
            </span>
          </span>
          <ArrowRight className="size-5" />
        </motion.button>
      )}

      {dueTest && (
        <div className="flex items-center gap-4 rounded-2xl border border-primary/30 bg-brand-soft p-4">
          <ClipboardCheck className="size-6 shrink-0 text-primary" />
          <div className="flex-1 text-sm">
            <div className="font-medium">{dueTest.name}を受けられます</div>
            <div className="text-muted-foreground">一度も聞いていない音声で、伸びを測定します（約10分）</div>
          </div>
          <Button onClick={() => start({ mode: "test", form: dueTest.form, minutes: 0 })} disabled={starting}>
            受ける
          </Button>
        </div>
      )}

      {!profile.vocab.diagnosed && !dueTest && (
        <Link href="/vocab/diagnostic" className="flex items-center gap-4 rounded-2xl border border-primary/30 bg-brand-soft p-4 hover:bg-brand-soft/70">
          <Languages className="size-6 shrink-0 text-primary" />
          <div className="flex-1 text-sm">
            <div className="font-medium">{profile.vocab.active_diagnostic ? "TOEIC 語彙診断の続きから" : "TOEIC 語彙診断を受ける"}</div>
            <div className="text-muted-foreground">忘れている単語と、音で聞き取れない単語を特定します（約8分）</div>
          </div>
          <ArrowRight className="size-4 text-muted-foreground" />
        </Link>
      )}

      <section className="rounded-3xl border bg-card p-5 md:p-6">
        <h2 className="text-lg font-semibold">今日はどれくらい学習しますか？</h2>
        <p className="mt-1 text-sm text-muted-foreground">30分が毎日の目安。時間がある日は、そのぶん新しい音声と訓練が増えます。</p>
        <div className="mt-4 grid grid-cols-5 gap-2">
          {PRESETS.map((m) => (
            <button
              key={m}
              type="button"
              onClick={() => setMinutes(m)}
              className={cn(
                "relative h-14 rounded-xl border text-sm font-medium transition-colors",
                minutes === m ? "border-primary bg-brand-soft text-primary" : "hover:bg-muted",
              )}
            >
              <span className="text-base tabular">{m}</span>
              <span className="text-xs">分</span>
              {m === 30 && <span className="absolute -top-2 left-1/2 -translate-x-1/2 whitespace-nowrap rounded-full bg-primary px-1.5 text-[10px] text-primary-foreground">標準</span>}
            </button>
          ))}
        </div>
        <div className="mt-3 flex items-center justify-center gap-3 text-sm text-muted-foreground">
          <button type="button" className="flex size-8 items-center justify-center rounded-lg border hover:bg-muted" onClick={() => setMinutes((m) => Math.max(5, m - 5))} aria-label="5分減らす">
            <Minus className="size-4" />
          </button>
          <span className="w-24 text-center tabular text-foreground">{minutes} 分</span>
          <button type="button" className="flex size-8 items-center justify-center rounded-lg border hover:bg-muted" onClick={() => setMinutes((m) => Math.min(300, m + 5))} aria-label="5分増やす">
            <Plus className="size-4" />
          </button>
        </div>

        <div className="mt-5">
          <div className="mb-2 flex items-baseline justify-between text-sm">
            <span className="font-medium">今日の学習プラン</span>
            {plan && (
              <span className="text-xs text-muted-foreground">
                学習 {plan.study_minutes}分{plan.break_minutes ? ` + 休憩 ${plan.break_minutes}分` : ""}
              </span>
            )}
          </div>
          <ol className="space-y-1.5">
            <AnimatePresence initial={false}>
              {plan?.blocks.map((b, i) => (
                <motion.li
                  key={`${minutes}-${i}`}
                  initial={{ opacity: 0, y: 4 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: i * 0.025 }}
                  className={cn("flex items-center gap-3 rounded-xl px-3 py-2 text-sm", b.kind === "break" ? "bg-warning-soft" : "bg-muted/60")}
                >
                  <span className="flex-1">
                    <span className="font-medium">{b.label}</span>
                    <span className="ml-2 hidden text-xs text-muted-foreground md:inline">{b.description}</span>
                  </span>
                  <span className="tabular text-muted-foreground">{b.minutes}分</span>
                </motion.li>
              ))}
            </AnimatePresence>
          </ol>
        </div>

        <Button size="lg" className="mt-5 h-13 w-full text-base" disabled={starting} onClick={() => start({ minutes })}>
          {starting ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />}
          このプランで学習を始める
        </Button>
        <p className="mt-2 text-center text-xs text-muted-foreground">途中での延長・一時停止・別端末での再開ができます</p>
      </section>

      <section className="grid gap-3 sm:grid-cols-2">
        <Link href="/review" className="flex items-center gap-4 rounded-2xl border bg-card p-4 hover:bg-muted/50">
          <RotateCcw className="size-5 text-primary" />
          <div className="flex-1">
            <div className="font-medium">復習</div>
            <div className="text-sm text-muted-foreground">
              今日の復習 {profile.review_due} 件{profile.vocab.due ? `（うち単語 ${profile.vocab.due}）` : ""}
            </div>
          </div>
          <ArrowRight className="size-4 text-muted-foreground" />
        </Link>
        <Link href="/analytics" className="flex items-center gap-4 rounded-2xl border bg-card p-4 hover:bg-muted/50">
          <ClipboardCheck className="size-5 text-primary" />
          <div className="flex-1">
            <div className="font-medium">成果を見る</div>
            <div className="text-sm text-muted-foreground">弱点・習熟度・テスト結果</div>
          </div>
          <ArrowRight className="size-4 text-muted-foreground" />
        </Link>
      </section>
    </div>
  );
}
