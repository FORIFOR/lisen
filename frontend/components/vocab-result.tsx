"use client";

import { Ear, Volume2 } from "lucide-react";
import { Meter } from "@/components/charts";
import { playAudio } from "@/components/word-question";
import { MODALITY_LABEL, type DiagnosticResult, type Modality, type PlacementRoute } from "@/lib/api";
import { cn } from "@/lib/utils";

const ORDER: Modality[] = ["text", "audio", "context"];

export function WordRow({ lemma, meaning, audio, note }: { lemma: string; meaning: string; audio?: string | null; note?: string }) {
  return (
    <li className="flex items-center gap-3 border-b py-2 last:border-0">
      <button
        type="button"
        onClick={() => playAudio(audio)}
        disabled={!audio}
        aria-label={`${lemma} を再生`}
        className="flex size-8 shrink-0 items-center justify-center rounded-full border text-muted-foreground hover:bg-muted disabled:opacity-40"
      >
        <Volume2 className="size-4" />
      </button>
      <div className="min-w-0 flex-1">
        <div className="font-medium">{lemma}</div>
        <div className="truncate text-sm text-muted-foreground">{meaning}</div>
      </div>
      {note && <span className="shrink-0 rounded-full bg-warning-soft px-2 py-0.5 text-xs">{note}</span>}
    </li>
  );
}

export function DiagnosticResultView({ result }: { result: DiagnosticResult }) {
  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-2xl border bg-card p-4">
          <div className="text-xs text-muted-foreground">推定の語彙レベル</div>
          <div className="mt-1 text-2xl font-semibold tabular">難易度 {result.estimated_tier} / 4</div>
          <div className="text-xs text-muted-foreground">70% 以上を確実に答えられた最も高い難易度</div>
        </div>
        <div className="rounded-2xl border bg-card p-4">
          <div className="text-xs text-muted-foreground">出題</div>
          <div className="mt-1 text-2xl font-semibold tabular">{result.items}問</div>
          <div className="text-xs text-muted-foreground">{result.words}語を診断</div>
        </div>
        <div className="rounded-2xl border bg-card p-4">
          <div className="text-xs text-muted-foreground">これから覚え直す単語</div>
          <div className="mt-1 text-2xl font-semibold tabular">{result.weak.length}語</div>
          <div className="text-xs text-muted-foreground">単語の学習で覚え直してから FSRS の復習に入ります</div>
        </div>
      </div>

      <section className="rounded-2xl border bg-card p-4 md:p-5">
        <h2 className="text-sm font-medium">能力別の正答率</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">勘で正解した問題は半分、時間がかかった正解は 0.8 として計算</p>
        <ul className="mt-4 space-y-3">
          {ORDER.filter((m) => result.modality[m]).map((m) => (
            <li key={m}>
              <div className="mb-1 flex justify-between text-sm">
                <span>{MODALITY_LABEL[m]}</span>
                <span className="tabular text-muted-foreground">
                  {Math.round(result.modality[m]!.accuracy * 100)}%<span className="ml-1 text-xs">（{result.modality[m]!.n}問）</span>
                </span>
              </div>
              <Meter value={result.modality[m]!.accuracy} />
            </li>
          ))}
        </ul>
      </section>

      {result.sound_gap.length > 0 && (
        <section className="rounded-2xl border border-primary/30 bg-brand-soft p-4 md:p-5">
          <h2 className="flex items-center gap-2 text-sm font-medium">
            <Ear className="size-4 text-primary" /> 文字では分かるのに、音で認識できなかった単語
          </h2>
          <p className="mt-0.5 text-xs text-muted-foreground">意味は知っているので、音と結びつける訓練（音声・会話）を優先します</p>
          <ul className="mt-2">
            {result.sound_gap.map((w) => (
              <WordRow key={w.id} lemma={w.lemma} meaning={w.meaning} audio={w.audio} />
            ))}
          </ul>
        </section>
      )}

      {result.weak.length > 0 && (
        <section className="rounded-2xl border bg-card p-4 md:p-5">
          <h2 className="text-sm font-medium">覚え直す単語</h2>
          <ul className="mt-2">
            {result.weak.map((w) => (
              <WordRow key={w.id} lemma={w.lemma} meaning={w.meaning} audio={w.audio} note={w.failed.map((m) => MODALITY_LABEL[m]).join("・")} />
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

const ROUTE_ORDER: PlacementRoute[] = ["basic", "sound", "context", "solid", "text_only"];
const ROUTE_TITLE: Record<PlacementRoute, string> = {
  basic: "意味から覚える",
  sound: "音声付きで覚え直す",
  context: "例文で聞き取る",
  solid: "初見の例文まで理解",
  text_only: "文字で正解（音声は未確認）",
};

/** The word book's placement result: level, where each tested word goes next, and an honest size estimate. */
export function PlacementResultView({ result }: { result: DiagnosticResult }) {
  const text = result.modality.text;
  const routes = result.routes;
  return (
    <div className="space-y-5">
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-2xl border bg-card p-4">
          <div className="text-xs text-muted-foreground">推定の語彙レベル</div>
          <div className="mt-1 text-2xl font-semibold tabular">難易度 {result.estimated_tier} / 4</div>
          <div className="text-xs text-muted-foreground">文字の問題で 70% 以上正解した最も高い難易度</div>
        </div>
        <div className="rounded-2xl border bg-card p-4">
          <div className="text-xs text-muted-foreground">文字で分かる単語（目安）</div>
          <div className="mt-1 text-2xl font-semibold tabular">
            {result.known_estimate != null ? `約 ${result.known_estimate} 語` : "—"}
            <span className="ml-1 text-sm font-normal text-muted-foreground">/ {result.total_words ?? "—"}</span>
          </div>
          <div className="text-xs text-muted-foreground">難易度別の正答率からの推定。個々の単語は学習しながら確かめます</div>
        </div>
        <div className="rounded-2xl border bg-card p-4">
          <div className="text-xs text-muted-foreground">出題</div>
          <div className="mt-1 text-2xl font-semibold tabular">{result.items}問</div>
          <div className="text-xs text-muted-foreground">
            {result.words}語 · 分からない {result.unknown ?? 0} · 勘 {result.guesses}
            {result.median_ms ? ` · 回答 ${(result.median_ms / 1000).toFixed(1)}秒（中央値）` : ""}
          </div>
        </div>
      </div>

      <section className="rounded-2xl border bg-card p-4 md:p-5">
        <h2 className="text-sm font-medium">文字と音声の差</h2>
        <p className="mt-0.5 text-xs text-muted-foreground">音声・例文は、文字で正解した単語の一部だけを確認しています</p>
        <ul className="mt-4 space-y-3">
          {ORDER.filter((m) => result.modality[m]).map((m) => (
            <li key={m}>
              <div className="mb-1 flex justify-between text-sm">
                <span>{m === "text" ? "文字の語彙力" : m === "audio" ? "音声の語彙力" : "文脈での理解"}</span>
                <span className="tabular text-muted-foreground">
                  {Math.round(result.modality[m]!.accuracy * 100)}%<span className="ml-1 text-xs">（{result.modality[m]!.n}問）</span>
                </span>
              </div>
              <Meter value={result.modality[m]!.accuracy} />
            </li>
          ))}
        </ul>
        {!text && <p className="mt-2 text-xs text-muted-foreground">文字の問題がまだありません</p>}
      </section>

      {routes &&
        ROUTE_ORDER.filter((k) => routes[k]?.length).map((k) => (
          <section key={k} className={cn("rounded-2xl border p-4 md:p-5", k === "sound" ? "border-primary/30 bg-brand-soft" : "bg-card")}>
            <h2 className="flex items-center gap-2 text-sm font-medium">
              {k === "sound" && <Ear className="size-4 text-primary" />}
              {ROUTE_TITLE[k]}
              <span className="tabular text-xs font-normal text-muted-foreground">{routes[k].length}語</span>
            </h2>
            {result.route_labels?.[k] && <p className="mt-0.5 text-xs text-muted-foreground">{result.route_labels[k]}</p>}
            <ul className="mt-2">
              {routes[k].slice(0, 12).map((w) => (
                <WordRow key={w.id} lemma={w.lemma} meaning={w.meaning} audio={w.audio} />
              ))}
            </ul>
          </section>
        ))}
    </div>
  );
}
