"use client";

import Link from "next/link";
import { ArrowRight, ClipboardCheck, Loader2, RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { buttonVariants } from "@/components/ui/button";
import { api, type Phase, type VocabOverview } from "@/lib/api";
import { cn } from "@/lib/utils";

const MINUTES_KEY = "lf-vocab-minutes";
const PHASE_ORDER: Phase[] = ["placement", "first", "transfer", "review"];

function readMinutes(): number {
  try {
    const v = Number(localStorage.getItem(MINUTES_KEY));
    return [5, 10, 15, 30].includes(v) ? v : 10;
  } catch {
    return 10;
  }
}

/** The word book: what is learned / due / not yet studied, the placement test, and 5-30 minute lessons. */
export default function VocabHomePage() {
  const [ov, setOv] = useState<VocabOverview | null>(null);
  const [minutes, setMinutes] = useState(10);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- restore the last choice once on mount
    setMinutes(readMinutes());
    api<VocabOverview>("/v1/vocabulary/overview")
      .then(setOv)
      .catch((e) => toast.error((e as Error).message));
  }, []);

  const pick = (m: number) => {
    setMinutes(m);
    try {
      localStorage.setItem(MINUTES_KEY, String(m));
    } catch {}
  };

  if (!ov)
    return (
      <div className="flex justify-center py-24 text-muted-foreground">
        <Loader2 className="size-6 animate-spin" />
      </div>
    );

  const placed = ov.placement?.status === "completed";
  const placing = ov.placement?.status === "active";
  const tiles = [
    { label: "学習済み", value: ov.acquired, note: "文字・音声・例文で正解し、別の日にも確認" },
    { label: "復習待ち", value: ov.due, note: "今日 FSRS が出す復習" },
    { label: "学習中", value: ov.learning, note: "確認の途中・忘れかけ" },
    { label: "未学習", value: ov.untested, note: `全 ${ov.total.toLocaleString()} 語のうち` },
  ];

  return (
    <div className="mx-auto max-w-3xl space-y-5 pb-10">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">単語</h1>
        <p className="mt-1 text-sm text-muted-foreground">TOEIC 頻出語と基礎語を覚え直し、その単語を使った英文を音だけで聞いて確かめます。</p>
      </header>

      <section className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        {tiles.map((t) => (
          <div key={t.label} className="rounded-2xl border bg-card p-4">
            <div className="text-xs text-muted-foreground">{t.label}</div>
            <div className="mt-1 text-2xl font-semibold tabular">{t.value.toLocaleString()}</div>
            <div className="mt-0.5 text-[11px] leading-snug text-muted-foreground">{t.note}</div>
          </div>
        ))}
      </section>

      {!placed && (
        <Link href="/vocab/placement" className="flex items-center gap-4 rounded-2xl border border-primary/30 bg-brand-soft p-4 hover:bg-brand-soft/70">
          <ClipboardCheck className="size-6 shrink-0 text-primary" />
          <div className="flex-1">
            <div className="font-medium">{placing ? "語彙診断の続き" : "まず語彙診断（約30問・8〜10分）"}</div>
            <div className="text-sm text-muted-foreground">知っている単語に時間をかけないよう、覚え直す単語をテストで選びます</div>
          </div>
          <ArrowRight className="size-4 text-muted-foreground" />
        </Link>
      )}

      {placed && ov.placement?.tested_words != null && (
        <section className="rounded-2xl border bg-card p-4 text-sm">
          <div className="flex flex-wrap gap-x-6 gap-y-2">
            <div>
              <div className="text-xs text-muted-foreground">診断で確認した単語</div>
              <div className="font-medium tabular">{ov.placement.tested_words}語</div>
            </div>
            <div>
              <div className="text-xs text-muted-foreground">レベルからの推定（文字で分かる語）</div>
              <div className="font-medium tabular">{ov.placement.estimated_known != null ? `約${ov.placement.estimated_known}語` : "—"}</div>
            </div>
          </div>
          <p className="mt-2 text-xs text-muted-foreground">推定は目安です。1語ずつの状態には使わず、学習と復習で確かめた単語だけを「学習済み」にします。</p>
        </section>
      )}

      <section className="rounded-2xl border bg-card p-4 md:p-5">
        <h2 className="text-sm font-medium">単語の学習</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">覚え直す → 単語を音で確認 → 例文を音で確認 → 初めて聞く文で確認。復習が溜まっていれば最初に出します。</p>
        <div className="mt-4 grid grid-cols-4 gap-2" role="radiogroup" aria-label="学習時間">
          {ov.minutes.map((m) => (
            <button
              key={m}
              type="button"
              role="radio"
              aria-checked={minutes === m}
              onClick={() => pick(m)}
              className={cn("h-12 rounded-xl border text-sm tabular", minutes === m ? "border-primary bg-brand-soft font-medium text-primary" : "hover:bg-muted")}
            >
              {m}分
            </button>
          ))}
        </div>
        <div className="mt-4 flex flex-col gap-2 sm:flex-row">
          {ov.active_lesson ? (
            <>
              <Link href={`/vocab/learn?id=${ov.active_lesson.id}`} className={cn(buttonVariants({ size: "lg" }), "h-12 flex-1")}>
                続きから（{ov.active_lesson.minutes}分・{Math.floor(ov.active_lesson.spent_s / 60)}分経過） <ArrowRight className="size-4" />
              </Link>
              <Link href={`/vocab/learn?m=${minutes}&new=1`} className={cn(buttonVariants({ size: "lg", variant: "outline" }), "h-12")}>
                <RotateCcw className="size-4" /> {minutes}分で始め直す
              </Link>
            </>
          ) : (
            <Link href={`/vocab/learn?m=${minutes}`} className={cn(buttonVariants({ size: "lg" }), "h-12 flex-1")}>
              {minutes}分 学習する <ArrowRight className="size-4" />
            </Link>
          )}
        </div>
        {ov.due > 0 && (
          <Link href="/vocab/review" className="mt-3 inline-flex text-sm text-primary underline-offset-4 hover:underline">
            復習だけする（{ov.due}件）
          </Link>
        )}
      </section>

      <section className="rounded-2xl border bg-card p-4 md:p-5">
        <h2 className="text-sm font-medium">成績（別々に集計）</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">初めて聞く文の成績と、復習・学習直後の成績は混ぜずに表示します</p>
        <ul className="mt-3 divide-y text-sm">
          {PHASE_ORDER.map((p) => {
            const v = ov.phases[p];
            return (
              <li key={p} className="flex items-center justify-between py-2">
                <span>{ov.phase_labels[p]}</span>
                <span className="tabular text-muted-foreground">{v ? `${Math.round(v.accuracy * 100)}%（${v.n}問）` : "まだありません"}</span>
              </li>
            );
          })}
        </ul>
        <div className="mt-3 flex flex-wrap gap-3 text-sm">
          {placed && (
            <Link href="/vocab/placement" className="text-primary underline-offset-4 hover:underline">
              語彙診断をもう一度
            </Link>
          )}
          <Link href="/analytics/vocabulary" className="text-primary underline-offset-4 hover:underline">
            語彙の分析
          </Link>
        </div>
      </section>

      <p className="text-[11px] leading-relaxed text-muted-foreground">
        単語リスト: {ov.datasets.map((d) => `${d.file}（${d.words}語・${d.license}）`).join("、")}。NGSL 1.2（Browne, Culligan &amp; Phillips）/ TSL 1.2（Browne &amp; Culligan）, newgeneralservicelist.com, CC BY-SA 4.0の見出し語・順位を使い、日本語訳と例文はこのアプリ用に作成しました。
      </p>
    </div>
  );
}
