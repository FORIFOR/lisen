"use client";

import { Switch } from "@/components/ui/switch";
import { NEWS_CATEGORY_LABEL, NEWS_LEVEL_LABEL, type NewsCategory, type NewsLevel, type Settings } from "@/lib/api";
import { cn } from "@/lib/utils";

const CATS = Object.keys(NEWS_CATEGORY_LABEL) as NewsCategory[];
const LEVELS: NewsLevel[] = ["A2", "B1", "B2"];

function Chip({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button type="button" aria-pressed={on} onClick={onClick}
      className={cn("h-9 rounded-lg border px-3 text-sm", on ? "border-primary bg-brand-soft font-medium text-primary" : "text-muted-foreground hover:bg-muted")}>
      {children}
    </button>
  );
}

/** Daily English News: topics, how many a day, the morning run and the level a lesson opens with. */
export function NewsSettings({ settings: s, update }: { settings: Settings; update: (patch: Partial<Settings>) => Promise<void> }) {
  const topics = s.news_topics ?? ["technology"];
  const toggle = (c: NewsCategory) => {
    const next = topics.includes(c) ? topics.filter((t) => t !== c) : [...topics, c];
    if (next.length) update({ news_topics: next });
  };
  return (
    <section className="rounded-2xl border bg-card p-4 md:p-5">
      <h2 className="text-sm font-medium">今日のニュース</h2>
      <p className="mt-0.5 text-xs text-muted-foreground">
        RSS の見出しと要約から Gemini で教材を作ります（API キーの Google アカウントに課金されます。1本あたり Gemini 1〜2回）。
      </p>
      <div className="mt-4 space-y-4 text-sm">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="font-medium">毎朝自動で作る</div>
            <div className="text-xs text-muted-foreground">Mac で LISEN が起動しているときだけ。スリープ中だった日は、起動後に作ります</div>
          </div>
          <Switch checked={s.news_auto} onCheckedChange={(v) => update({ news_auto: v })} />
        </div>
        <div>
          <div className="mb-1.5 font-medium">作り始める時刻</div>
          <div className="flex flex-wrap gap-1.5">
            {[5, 6, 7, 8].map((h) => (
              <Chip key={h} on={s.news_hour === h} onClick={() => update({ news_hour: h })}>{h}時</Chip>
            ))}
          </div>
        </div>
        <div>
          <div className="mb-1.5 font-medium">分野</div>
          <div className="flex flex-wrap gap-1.5">
            {CATS.map((c) => (
              <Chip key={c} on={topics.includes(c)} onClick={() => toggle(c)}>{NEWS_CATEGORY_LABEL[c]}</Chip>
            ))}
          </div>
        </div>
        <div>
          <div className="mb-1.5 font-medium">1日の本数</div>
          <div className="flex flex-wrap gap-1.5">
            {[1, 2, 3, 5].map((n) => (
              <Chip key={n} on={s.news_per_day === n} onClick={() => update({ news_per_day: n })}>{n}本</Chip>
            ))}
          </div>
        </div>
        <div>
          <div className="mb-1.5 font-medium">作るレベル</div>
          <div className="mb-1.5 text-xs text-muted-foreground">少ないほど Gemini の出力（料金）が減ります</div>
          <div className="flex flex-wrap gap-1.5">
            {LEVELS.map((l) => {
              const on = (s.news_levels ?? LEVELS).includes(l);
              const next = on ? (s.news_levels ?? LEVELS).filter((x) => x !== l) : LEVELS.filter((x) => x === l || (s.news_levels ?? LEVELS).includes(x));
              return (
                <Chip key={l} on={on} onClick={() => next.length && update({ news_levels: next })}>{NEWS_LEVEL_LABEL[l]}（{l}）</Chip>
              );
            })}
          </div>
        </div>
        <div>
          <div className="mb-1.5 font-medium">最初に開くレベル</div>
          <div className="flex flex-wrap gap-1.5">
            {LEVELS.map((l) => (
              <Chip key={l} on={s.news_level === l} onClick={() => update({ news_level: l })}>{NEWS_LEVEL_LABEL[l]}（{l}）</Chip>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
