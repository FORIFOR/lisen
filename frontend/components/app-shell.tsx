"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTheme } from "next-themes";
import { BarChart3, BookOpen, Home, Moon, RotateCcw, Settings, Sun, Waves } from "lucide-react";
import { useSyncExternalStore } from "react";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "ホーム", icon: Home },
  { href: "/review", label: "復習", icon: RotateCcw },
  { href: "/analytics", label: "分析", icon: BarChart3 },
  { href: "/library", label: "教材", icon: BookOpen },
  { href: "/settings", label: "設定", icon: Settings },
];

export function ThemeToggle({ className }: { className?: string }) {
  const { resolvedTheme, setTheme } = useTheme();
  const mounted = useSyncExternalStore(
    () => () => {},
    () => true,
    () => false,
  );
  const dark = mounted && resolvedTheme === "dark";
  return (
    <button
      type="button"
      onClick={() => setTheme(dark ? "light" : "dark")}
      className={cn("inline-flex size-9 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground", className)}
      aria-label={dark ? "ライトモードに切り替え" : "ダークモードに切り替え"}
    >
      {dark ? <Sun className="size-4" /> : <Moon className="size-4" />}
    </button>
  );
}

export function Logo() {
  return (
    <span className="flex items-center gap-2 font-semibold tracking-tight">
      <span className="flex size-7 items-center justify-center rounded-lg bg-primary text-primary-foreground">
        <Waves className="size-4" />
      </span>
      ListenFlow
    </span>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const focusMode = pathname.startsWith("/session") || pathname.startsWith("/login") || pathname.startsWith("/onboarding") || pathname.startsWith("/vocab/");

  if (focusMode) {
    return <div className="min-h-dvh">{children}</div>;
  }

  return (
    <div className="min-h-dvh md:flex">
      {/* desktop sidebar */}
      <aside className="sticky top-0 hidden h-dvh w-56 shrink-0 flex-col border-r bg-sidebar px-3 py-5 md:flex">
        <Link href="/" className="px-2">
          <Logo />
        </Link>
        <nav className="mt-8 flex flex-col gap-1">
          {NAV.map(({ href, label, icon: Icon }) => {
            const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className={cn(
                  "flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors",
                  active ? "bg-brand-soft font-medium text-primary" : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
              >
                <Icon className="size-4" />
                {label}
              </Link>
            );
          })}
        </nav>
        <div className="mt-auto flex items-center justify-between px-2 text-xs text-muted-foreground">
          <span>キーボード操作対応</span>
          <ThemeToggle />
        </div>
      </aside>

      {/* mobile header */}
      <header className="sticky top-0 z-20 flex h-14 items-center justify-between border-b bg-background/85 px-4 backdrop-blur md:hidden" style={{ paddingTop: "env(safe-area-inset-top)" }}>
        <Logo />
        <div className="flex items-center">
          <ThemeToggle />
          <Link href="/settings" aria-label="設定" className="inline-flex size-9 items-center justify-center rounded-lg text-muted-foreground hover:bg-muted">
            <Settings className="size-4" />
          </Link>
        </div>
      </header>

      <main className="mx-auto w-full max-w-5xl flex-1 px-4 pb-28 pt-5 md:px-8 md:pb-12 md:pt-8">{children}</main>

      {/* mobile tab bar: fewer controls, study comes first */}
      <nav className="pb-safe fixed inset-x-0 bottom-0 z-20 grid grid-cols-4 border-t bg-background/95 pt-1.5 backdrop-blur md:hidden">
        {NAV.filter((n) => n.href !== "/settings").map(({ href, label, icon: Icon }) => {
          const active = href === "/" ? pathname === "/" : pathname.startsWith(href);
          return (
            <Link key={href} href={href} className={cn("flex flex-col items-center gap-0.5 py-1 text-[11px]", active ? "text-primary" : "text-muted-foreground")}>
              <Icon className="size-5" />
              {label}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
