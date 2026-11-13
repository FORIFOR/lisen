"use client";

import Link from "next/link";
import { ArrowRight, CheckCircle2, Headphones, Loader2, Newspaper, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { api, NEWS_CATEGORY_LABEL, NEWS_LEVEL_LABEL, type NewsCard, type NewsOverview, type NewsStatus } from "@/lib/api";
import { published } from "@/lib/news";
import { cn } from "@/lib/utils";

function NewsCardLink({ c }: { c: NewsCard }) {
  const p = published(c.source.published_at);
  return (
    <Link href={`/news/lesson?id=${c.id}`} className="block rounded-2xl border bg-card p-4 hover:bg-muted/40">
      <div className="flex flex-wrap items-center gap-1.5 text-[11px] text-muted-foreground">
        <span className="rounded-full bg-brand-soft px-2 py-0.5 font-medium text-primary">{c.source.category_label}</span>
        <span>{c.source.publisher}</span>
        {c.source.kind !== "press" && <span className="rounded-full border px-1.5">{c.source.kind_label}</span>}
        {c.corroborated.length > 0 && <span className="rounded-full border border-success/40 px-1.5">{c.corroborated.join("・")} も報道</span>}
      </div>
      <div className="mt-2 flex items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="font-medium leading-snug">{c.title_en}</div>
          {c.title_ja && <div className="mt-0.5 text-sm text-muted-foreground">{c.title_ja}</div>}
        </div>
        {c.state === "done" ? <CheckCircle2 className="mt-0.5 size-5 shrink-0 text-success" /> : <ArrowRight className="mt-0.5 size-4 shrink-0 text-muted-foreground" />}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground tabular">
        <span>約{c.minutes}分</span>
        <span>{c.levels.map((l) => NEWS_LEVEL_LABEL[l]).join("・")}</span>
        <span className={cn(p.old && "rounded bg-warning-soft px-1 text-foreground")}>{p.text}</span>
        {c.state === "listened" && <span>内容問題まで済み</span>}
      </div>
    </Link>
  );
}

function RunStatus({ st, onRun }: { st: NewsStatus; onRun: () => void }) {
  const last = st.last_run;
  const label: Record<string, string> = {
    ok: "完了", partial: "一部のみ作成", failed: "失敗", no_stories: "新しいニュースなし", no_key: "Gemini キー未設定", running: "作成中",
  };
  const rejected = last?.detail?.lessons.filter((l) => l.status === "rejected" || l.status === "insufficient").length ?? 0;
  const retrying = last?.detail?.lessons.filter((l) => l.status === "retry").length ?? 0;
  return (
    <section className="rounded-2xl border bg-card p-4 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="text-xs text-muted-foreground">
          {st.auto && st.next_run ? `毎朝 ${st.settings.hour}時以降に自動で作成（Mac が起動しているとき）` : "自動作成はオフです（設定で変更）"}
          {" · "}
          {st.settings.topics.map((t) => NEWS_CATEGORY_LABEL[t]).join("・")} · 1日{st.settings.per_day}本
        </div>
        <Button size="sm" variant="outline" onClick={onRun} disabled={st.running || !st.gemini}>
          {st.running ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />} 今すぐ作る
        </Button>
      </div>
      {last && (
        <p className="mt-2 text-xs text-muted-foreground">
          前回: {new Date(last.started_at).toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })}{" "}
          {label[last.status] ?? last.status}
          {rejected > 0 && ` · 事実確認や音声の確認に通らなかった ${rejected} 本は表示していません`}
          {retrying > 0 && ` · ${retrying} 本は次回の実行でもう一度試します`}
          {last.detail?.error && ` · ${last.detail.error}`}
        </p>
      )}
      {!st.gemini && <p className="mt-2 text-xs text-muted-foreground">作成には Gemini の API キーが必要です（設定 → 教材の生成）。</p>}
      {st.gemini && !st.budget.can_call && (
        <p className="mt-2 rounded-lg bg-warning-soft px-2 py-1 text-xs">今月の上限に達したため、来月まで新しいニュース教材は作りません（設定 → 今日のニュース で変更できます）。</p>
      )}
      <p className="mt-2 text-[11px] text-muted-foreground">
        今月の Gemini 利用: {st.usage_month.calls}回（入力 {st.usage_month.prompt.toLocaleString()}・出力 {st.usage_month.output.toLocaleString()} トークン、推定 ${st.usage_month.usd.toFixed(2)} / 上限 ${st.budget.monthly_usd.toFixed(2)}）。「今すぐ作る」も Gemini を使います（足りない本数だけ作成）。
      </p>
    </section>
  );
}

/** Today's news as listening lessons. */
export default function NewsPage() {
  const [ov, setOv] = useState<NewsOverview | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const loadRef = useRef<() => void>(() => {});

  const load = useCallback(async () => {
    try {
      const o = await api<NewsOverview>("/v1/news");
      setOv(o);
      if (timer.current) clearTimeout(timer.current);
      if (o.status.running) timer.current = setTimeout(() => loadRef.current(), 5000);    // follow a run in progress
    } catch (e) {
      toast.error((e as Error).message);
    }
  }, []);

  useEffect(() => {
    loadRef.current = load;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- the first load on mount
    load();
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [load]);

  const runNow = async () => {
    try {
      await api("/v1/news/run", { body: {} });
      toast.success("ニュース教材を作っています（1本あたり1〜2分）");
      setTimeout(load, 1500);
    } catch (e) {
      toast.error((e as Error).message);
    }
  };

  if (!ov)
    return (
      <div className="flex justify-center py-24 text-muted-foreground">
        <Loader2 className="size-6 animate-spin" />
      </div>
    );

  const pr = ov.progress;
  return (
    <div className="mx-auto max-w-3xl space-y-5 pb-10">
      <header>
        <h1 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
          <Newspaper className="size-6 text-primary" /> 今日のニュース
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          ニュースを字幕なしで聞き、内容の問題 → 英文と訳 → 知らなかった単語 → 別の文で確認、の順に進みます。
        </p>
      </header>

      {ov.today.length > 0 ? (
        <section className="space-y-3">
          {ov.today.map((c) => (
            <NewsCardLink key={c.id} c={c} />
          ))}
        </section>
      ) : (
        <section className="rounded-2xl border border-dashed p-6 text-center text-sm text-muted-foreground">
          <Headphones className="mx-auto mb-2 size-6" />
          {ov.status.running ? "今日のニュース教材を作っています…" : "今日のニュース教材はまだありません。"}
        </section>
      )}

      <RunStatus st={ov.status} onRun={runNow} />

      {pr.lessons > 0 && (
        <section className="rounded-2xl border bg-card p-4 text-sm">
          <h2 className="font-medium">成績（別々に集計）</h2>
          <ul className="mt-2 divide-y">
            {(["first", "transfer"] as const).map((k) => (
              <li key={k} className="flex justify-between py-2">
                <span>{pr[k].label}</span>
                <span className="tabular text-muted-foreground">{pr[k].accuracy != null ? `${Math.round(pr[k].accuracy! * 100)}%（${pr[k].n}問）` : "—"}</span>
              </li>
            ))}
          </ul>
          <p className="mt-1 text-xs text-muted-foreground">
            {pr.lessons}本を学習 · 単語帳に追加した単語 {pr.words}語（次の
            <Link href="/vocab" className="text-primary underline-offset-4 hover:underline">単語の学習</Link>
            で最初に出します）
          </p>
        </section>
      )}

      {ov.recent.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-sm font-medium text-muted-foreground">これまでのニュース</h2>
          {ov.recent.map((c) => (
            <NewsCardLink key={c.id} c={c} />
          ))}
        </section>
      )}

      <p className="text-[11px] leading-relaxed text-muted-foreground">
        各社の RSS の見出しと要約だけをもとに、Gemini が英語学習用に書き直した文です（NASA の発表は本文も使用）。数字・人名・日付・引用が元の要約にあるかはプログラムで確認し、通らないものは表示しません。元記事は各カードの出典から確認できます。企業の発表は「企業自身の発表」と表示します。
      </p>
    </div>
  );
}
