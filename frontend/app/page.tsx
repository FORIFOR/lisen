"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { AnimatePresence, motion } from "motion/react";
import {
  ArrowRight,
  Briefcase,
  ChevronDown,
  ClipboardCheck,
  Coffee,
  Cpu,
  FileAudio,
  FlaskConical,
  Flame,
  Loader2,
  MessagesSquare,
  Play,
  Plane,
  RotateCcw,
  Sparkles,
  Target,
  Users,
  Languages,
} from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { api, ApiError, LEVEL_LABEL, type Block, type GrowthEvent, type Profile, type Recommendation } from "@/lib/api";
import { cn } from "@/lib/utils";

const PRESETS = [15, 30, 60, 90, 120];
// commute mode: short rides, answered by tapping only
const COMMUTE_PRESETS = [5, 10, 15, 30];
type Style = "standard" | "commute";
const STYLE_KEY = "lf-style";
const TOPIC_ICON: Record<string, typeof Cpu> = {
  tech: Cpu,
  business: Briefcase,
  conversation: MessagesSquare,
  science: FlaskConical,
  travel: Plane,
  daily: Coffee,
  imported: FileAudio,
};
const KIND_LABEL: Record<string, string> = { dialogue: "会話", monologue: "トーク", announcement: "アナウンス", lecture: "講義", podcast: "ポッドキャスト" };

type Plan = { blocks: Block[]; study_minutes: number; break_minutes: number };

export default function Home() {
  const router = useRouter();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [minutes, setMinutes] = useState(30);
  const [style, setStyle] = useState<Style>("standard");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [showPlan, setShowPlan] = useState(false);
  const [recs, setRecs] = useState<{ items: Recommendation[]; topics: Record<string, string>; interests: string[] } | null>(null);
  const [growth, setGrowth] = useState<GrowthEvent[] | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Profile>("/profile")
      .then((p) => {
        if (!p.onboarded) router.replace("/onboarding");
        setProfile(p);
        let saved: string | null = null;
        try {
          saved = window.localStorage.getItem(STYLE_KEY);
        } catch {}
        if (saved === "commute") {
          setStyle("commute");
          setMinutes(10);
        } else setMinutes((m) => (p.settings.daily_goal && PRESETS.includes(p.settings.daily_goal) ? p.settings.daily_goal : m));
      })
      .catch((e) => setError(e.message));
    api<{ items: Recommendation[]; topics: Record<string, string>; interests: string[] }>("/v1/recommendations?limit=6").then(setRecs).catch(() => {});
    api<{ items: GrowthEvent[] }>("/v1/growth?days=7").then((r) => setGrowth(r.items)).catch(() => setGrowth([]));
  }, [router]);

  useEffect(() => {
    api<Plan>("/plan/preview", { body: { minutes, mode: style } })
      .then(setPlan)
      .catch(() => {});
  }, [minutes, style]);

  const chooseStyle = (v: Style) => {
    setStyle(v);
    try {
      window.localStorage.setItem(STYLE_KEY, v);
    } catch {}
    const list = v === "commute" ? COMMUTE_PRESETS : PRESETS;
    if (!list.includes(minutes)) setMinutes(v === "commute" ? 10 : 30);
  };

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
        <p className="mt-2 text-sm text-muted-foreground">
          Mac で <code className="rounded bg-muted px-1">./start.sh</code> を実行し、Mac がスリープしていないか確認してください。
        </p>
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
  const active = profile.active_session;
  const dueTest = profile.tests.find((t) => t.available && !t.taken);
  const lp = profile.listening_profile;
  const weakLabel = lp.priority ? lp.abilities[lp.priority].label : null;
  const firstInterest = recs?.interests?.[0] ? recs.topics[recs.interests[0]] : null;
  const pitch = weakLabel
    ? `${firstInterest ? `${firstInterest}の音声と、` : ""}あなたが苦手な「${weakLabel}」の訓練を組み合わせました。`
    : `${firstInterest ? `${firstInterest}を中心に、` : ""}まだ聞いたことのない音声と、忘れかけた表現の復習を組み合わせました。`;
  const adapted = plan?.blocks.find((b) => b.adapted);
  const todayPct = Math.min(100, (profile.today_minutes / goal) * 100);

  return (
    <div className="space-y-6">
      {active && (
        <motion.button
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          onClick={() => router.push("/session")}
          className="flex w-full items-center gap-4 rounded-2xl border border-primary/40 bg-brand-soft p-4 text-left"
        >
          <span className="flex size-11 items-center justify-center rounded-full bg-primary text-primary-foreground">
            <Play className="size-5" />
          </span>
          <span className="min-w-0 flex-1">
            <span className="block font-medium">
              {active.mode === "profile" ? "リスニング診断の続きから" : active.mode === "test" ? "テストの続きから再開" : "続きから再開"}
            </span>
            <span className="block truncate text-sm text-muted-foreground">
              {Math.round(active.study_seconds / 60)}分 学習済み · {active.blocks[active.block_index]?.label ?? ""}
            </span>
          </span>
          <ArrowRight className="size-5 text-primary" />
        </motion.button>
      )}

      {/* hero: today's training */}
      <section className="relative overflow-hidden rounded-3xl bg-primary p-5 text-primary-foreground md:p-7">
        <div aria-hidden className="pointer-events-none absolute -right-16 -top-16 size-64 rounded-full bg-primary-foreground/10" />
        <div aria-hidden className="pointer-events-none absolute -bottom-24 right-24 size-56 rounded-full bg-primary-foreground/5" />
        <div className="relative">
          <p className="text-[11px] font-semibold tracking-[0.18em] opacity-75">YOUR DAILY LISTENING</p>
          <h1 className="mt-2 text-2xl font-semibold leading-snug tracking-tight md:text-3xl">今日も、英語が聞こえる自分へ。</h1>
          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
            <span className="rounded-full bg-primary-foreground/15 px-2.5 py-1 font-medium tabular">
              DAY {Math.min(profile.day, 999)} / {profile.curriculum_days}
            </span>
            <span className="inline-flex items-center gap-1 rounded-full bg-primary-foreground/15 px-2.5 py-1 tabular">
              <Flame className="size-3.5" /> {profile.streak}日連続
            </span>
            <span className="rounded-full bg-primary-foreground/15 px-2.5 py-1 tabular">
              今日 {Math.round(profile.today_minutes)} / {goal}分
            </span>
          </div>
          <div className="mt-2 h-1 w-full max-w-xs overflow-hidden rounded-full bg-primary-foreground/20">
            <div className="h-full rounded-full bg-primary-foreground" style={{ width: `${todayPct}%` }} />
          </div>

          <div className="mt-6 rounded-2xl bg-background/95 p-4 text-foreground md:p-5">
            <div className="flex items-baseline justify-between gap-2">
              <h2 className="text-lg font-semibold">今日のトレーニング</h2>
              <span className="text-xs font-semibold tracking-wider text-primary tabular">{minutes} MIN</span>
            </div>
            <p className="mt-1 text-sm text-muted-foreground">
              {style === "commute" ? "文字入力なし。会話を聞いて答え、1文ずつ「どこまで分かったか」をタップで答え、意味と場面を確かめてから、同じ表現を別の文で聞きます。" : pitch}
            </p>
            <div className="mt-3 grid grid-cols-2 gap-1 rounded-xl border p-1" role="radiogroup" aria-label="学習の形">
              {(
                [
                  ["standard", "集中トレーニング", "書き取りあり"],
                  ["commute", "移動リスニング", "タップだけ"],
                ] as const
              ).map(([v, label, hint]) => (
                <button
                  key={v}
                  type="button"
                  role="radio"
                  aria-checked={style === v}
                  onClick={() => chooseStyle(v)}
                  className={cn("rounded-lg px-2 py-1.5 text-sm transition-colors", style === v ? "bg-primary text-primary-foreground" : "hover:bg-muted")}
                >
                  {label}
                  <span className={cn("block text-[11px]", style === v ? "opacity-80" : "text-muted-foreground")}>{hint}</span>
                </button>
              ))}
            </div>
            <div className="mt-3 flex gap-1.5 overflow-x-auto pb-1" role="radiogroup" aria-label="学習時間">
              {(style === "commute" ? COMMUTE_PRESETS : PRESETS).map((m) => (
                <button
                  key={m}
                  type="button"
                  role="radio"
                  aria-checked={minutes === m}
                  onClick={() => setMinutes(m)}
                  className={cn(
                    "h-9 shrink-0 rounded-full border px-3.5 text-sm tabular transition-colors",
                    minutes === m ? "border-primary bg-primary text-primary-foreground" : "hover:bg-muted",
                  )}
                >
                  {m}分
                </button>
              ))}
            </div>
            {adapted?.adapted && (
              <p className="mt-2 flex items-start gap-1.5 text-xs text-muted-foreground">
                <Target className="mt-px size-3.5 shrink-0 text-primary" /> 診断にもとづき「{adapted.label}」を {adapted.adapted}
              </p>
            )}
            <Button size="lg" className="mt-4 h-12 w-full text-base" disabled={starting} onClick={() => start({ minutes, mode: style })}>
              {starting ? <Loader2 className="size-4 animate-spin" /> : <Play className="size-4" />}
              学習を始める
            </Button>
            <button
              type="button"
              onClick={() => setShowPlan((v) => !v)}
              className="mt-2 flex w-full items-center justify-center gap-1 text-xs text-muted-foreground hover:text-foreground"
              aria-expanded={showPlan}
            >
              プランの内訳（学習 {plan?.study_minutes ?? minutes}分{plan?.break_minutes ? ` + 休憩 ${plan.break_minutes}分` : ""}）
              <ChevronDown className={cn("size-3.5 transition-transform", showPlan && "rotate-180")} />
            </button>
            <AnimatePresence initial={false}>
              {showPlan && plan && (
                <motion.ol initial={{ height: 0, opacity: 0 }} animate={{ height: "auto", opacity: 1 }} exit={{ height: 0, opacity: 0 }} className="mt-2 space-y-1 overflow-hidden">
                  {plan.blocks.map((b, i) => (
                    <li key={`${minutes}-${i}`} className={cn("flex items-center gap-3 rounded-lg px-3 py-1.5 text-sm", b.kind === "break" ? "bg-warning-soft" : "bg-muted/60")}>
                      <span className="flex-1">
                        {b.label}
                        {b.adapted && <Target className="ml-1 inline size-3 text-primary" aria-label="診断にもとづき増やした時間" />}
                      </span>
                      <span className="tabular text-muted-foreground">{b.minutes}分</span>
                    </li>
                  ))}
                </motion.ol>
              )}
            </AnimatePresence>
          </div>
        </div>
      </section>

      {!lp.diagnosed && !active && (
        <div className="flex flex-col gap-3 rounded-2xl border border-primary/30 bg-brand-soft p-4 sm:flex-row sm:items-center">
          <Target className="size-6 shrink-0 text-primary" />
          <div className="flex-1 text-sm">
            <div className="font-medium">リスニング診断（4つの能力）を受ける</div>
            <div className="text-muted-foreground">音声識別・語彙認識・構文理解・会話理解のどこでつまずくかを測り、学習時間の配分に反映します（約20〜25分）</div>
          </div>
          <Button onClick={() => start({ mode: "profile", minutes: 0 })} disabled={starting}>
            診断する
          </Button>
        </div>
      )}

      {dueTest && (
        <div className="flex flex-col gap-3 rounded-2xl border bg-card p-4 sm:flex-row sm:items-center">
          <ClipboardCheck className="size-6 shrink-0 text-primary" />
          <div className="flex-1 text-sm">
            <div className="font-medium">{dueTest.name}を受けられます</div>
            <div className="text-muted-foreground">一度も聞いていない同じ構成の音声で、本当の伸びを測ります（約10分）</div>
          </div>
          <Button variant="outline" onClick={() => start({ mode: "test", form: dueTest.form, minutes: 0 })} disabled={starting}>
            受ける
          </Button>
        </div>
      )}

      {/* recommendations */}
      {recs && recs.items.length > 0 && (
        <section>
          <div className="mb-2 flex items-baseline justify-between">
            <h2 className="text-base font-semibold">あなたにおすすめ</h2>
            <Link href="/library" className="text-xs text-muted-foreground hover:text-foreground">
              すべて見る
            </Link>
          </div>
          <div className="-mx-4 flex snap-x gap-3 overflow-x-auto px-4 pb-2 sm:mx-0 sm:grid sm:grid-cols-3 sm:overflow-visible sm:px-0">
            {recs.items.map((r) => {
              const Icon = TOPIC_ICON[r.topic] ?? Coffee;
              return (
                <button
                  key={r.id}
                  type="button"
                  disabled={starting}
                  onClick={() => start({ minutes, focus_clip: r.id })}
                  className="group w-60 shrink-0 snap-start overflow-hidden rounded-2xl border bg-card text-left transition-colors hover:border-primary/40 sm:w-auto"
                >
                  <div className="relative flex h-24 items-end bg-brand-soft p-3">
                    <Icon className="absolute right-3 top-3 size-9 text-primary/30 transition-transform group-hover:scale-110" />
                    <span className="rounded-full bg-background/90 px-2 py-0.5 text-[11px] font-medium">{r.topic_label}</span>
                  </div>
                  <div className="p-3">
                    <div className="line-clamp-2 text-sm font-medium">{r.title}</div>
                    <div className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
                      {KIND_LABEL[r.kind] ?? r.kind} · {LEVEL_LABEL[r.level]} · {Math.max(1, Math.round(r.duration / 60))}分
                      {r.speakers > 1 && <Users className="size-3" />}
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
          <p className="mt-1 text-xs text-muted-foreground">選んだ音声から「{minutes}分」の学習を始めます。内容は聞き終えるまで表示されません。</p>
        </section>
      )}

      {/* growth */}
      <section className="rounded-2xl border bg-card p-4 md:p-5">
        <h2 className="flex items-center gap-2 text-base font-semibold">
          <Sparkles className="size-4 text-success" /> 今日の成長
        </h2>
        {growth && growth.length > 0 ? (
          <ul className="mt-2 divide-y">
            {growth.slice(0, 5).map((g) => (
              <li key={g.id} className="flex items-start gap-3 py-2 text-sm">
                <span className="mt-0.5 rounded-full bg-success-soft px-2 py-0.5 text-[11px] text-success">{g.date === new Date().toLocaleDateString("sv") ? "今日" : g.date.slice(5)}</span>
                <span className="min-w-0 flex-1">
                  <span className="font-medium">{g.title}</span>
                  <span className="block text-xs text-muted-foreground">{g.detail}</span>
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="mt-1 text-sm text-muted-foreground">以前間違えた単語や文の構造を、初めて聞く音声の中で正しく聞き取れたときに、ここに表示します。</p>
        )}
      </section>

      <section className="grid gap-3 sm:grid-cols-2">
        <Link href="/vocab" className="flex items-center gap-4 rounded-2xl border bg-card p-4 hover:bg-muted/50 sm:col-span-2">
          <Languages className="size-5 text-primary" />
          <div className="flex-1">
            <div className="font-medium">単語を覚え直す</div>
            <div className="text-sm text-muted-foreground">語彙診断 → 苦手な単語 → 例文を音で確認 → 初見の文で確認（5〜30分）</div>
          </div>
          <ArrowRight className="size-4 text-muted-foreground" />
        </Link>
        <Link href="/review" className="flex items-center gap-4 rounded-2xl border bg-card p-4 hover:bg-muted/50">
          <RotateCcw className="size-5 text-primary" />
          <div className="flex-1">
            <div className="font-medium">復習だけする</div>
            <div className="text-sm text-muted-foreground">
              今日の復習 {profile.review_due} 件{profile.vocab.due ? `（うち単語 ${profile.vocab.due}）` : ""}
            </div>
          </div>
          <ArrowRight className="size-4 text-muted-foreground" />
        </Link>
        <Link href="/analytics" className="flex items-center gap-4 rounded-2xl border bg-card p-4 hover:bg-muted/50">
          <Target className="size-5 text-primary" />
          <div className="flex-1">
            <div className="font-medium">Listening Profile</div>
            <div className="text-sm text-muted-foreground">
              {weakLabel ? `いま優先：${weakLabel}` : "4つの能力と、初見会話の理解率"}
            </div>
          </div>
          <ArrowRight className="size-4 text-muted-foreground" />
        </Link>
      </section>
    </div>
  );
}
