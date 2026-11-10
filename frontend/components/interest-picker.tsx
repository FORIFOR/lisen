"use client";

import { Check } from "lucide-react";
import { cn } from "@/lib/utils";

export const INTEREST_OPTIONS: { id: string; label: string }[] = [
  { id: "tech", label: "AI・テクノロジー" },
  { id: "business", label: "ビジネス" },
  { id: "conversation", label: "自然な会話" },
  { id: "science", label: "科学・教養" },
  { id: "travel", label: "旅行" },
  { id: "daily", label: "日常" },
];
export const DEFAULT_INTERESTS = ["tech", "business", "conversation"];

/** Multi-select chips; the order of selection is the order of preference. */
export function InterestPicker({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  return (
    <div className="flex flex-wrap gap-2">
      {INTEREST_OPTIONS.map((o) => {
        const on = value.includes(o.id);
        return (
          <button
            key={o.id}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(on ? value.filter((v) => v !== o.id) : [...value, o.id])}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm transition-colors",
              on ? "border-primary bg-brand-soft text-foreground" : "text-muted-foreground hover:bg-muted",
            )}
          >
            {on && <Check className="size-3.5 text-primary" />}
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
