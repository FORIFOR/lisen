"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";

const TABS = [
  { href: "/analytics", label: "リスニング" },
  { href: "/analytics/vocabulary", label: "語彙 Vocabulary" },
];

export function AnalyticsTabs() {
  const pathname = usePathname();
  return (
    <nav className="inline-flex rounded-xl border p-0.5" aria-label="分析の種類">
      {TABS.map((t) => (
        <Link
          key={t.href}
          href={t.href}
          aria-current={pathname === t.href ? "page" : undefined}
          className={cn("rounded-lg px-3 py-1.5 text-sm", pathname === t.href ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted")}
        >
          {t.label}
        </Link>
      ))}
    </nav>
  );
}
