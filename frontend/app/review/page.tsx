"use client";

import { useRouter } from "next/navigation";
import { Loader2, RotateCcw } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { BarChart } from "@/components/charts";
import { Button } from "@/components/ui/button";
import { api, ApiError, type Profile } from "@/lib/api";

type Forecast = { review_forecast: { date: string; count: number }[]; cards: number };

export default function ReviewPage() {
  const router = useRouter();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [fc, setFc] = useState<Forecast | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api<Profile>("/profile").then(setProfile).catch(() => {});
    api<Forecast>("/analytics").then(setFc).catch(() => {});
  }, []);

  const start = async (minutes: number, replace = false) => {
    setBusy(true);
    try {
      await api("/sessions", { body: { minutes, mode: "review", replace } });
      router.push("/session");
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        toast("進行中の学習があります", { action: { label: "破棄して開始", onClick: () => start(minutes, true) } });
      } else toast.error((e as Error).message);
      setBusy(false);
    }
  };

  if (!profile)
    return (
      <div className="flex justify-center py-24 text-muted-foreground">
        <Loader2 className="size-6 animate-spin" />
      </div>
    );

  return (
    <div className="space-y-5">
      <h1 className="text-2xl font-semibold tracking-tight">復習</h1>
      <section className="rounded-3xl border bg-card p-6 text-center">
        <RotateCcw className="mx-auto size-8 text-primary" />
        <div className="mt-3 text-5xl font-semibold tabular">{profile.review_due}</div>
        <p className="mt-1 text-muted-foreground">今日復習すべき文・語彙</p>
        <p className="mx-auto mt-3 max-w-md text-sm text-muted-foreground">
          聞き取れなかった文は、FSRS が「忘れかけた頃」を予測して再出題します。毎日の学習プランの最初にも自動で含まれます。
        </p>
        <div className="mt-5 flex flex-col justify-center gap-2 sm:flex-row">
          <Button size="lg" className="h-12 px-8" disabled={busy} onClick={() => start(10)}>
            復習だけ 10分
          </Button>
          <Button size="lg" variant="outline" className="h-12 px-8" disabled={busy} onClick={() => start(20)}>
            20分
          </Button>
        </div>
        {profile.review_due === 0 && <p className="mt-3 text-xs text-muted-foreground">期限の来た復習がない場合は、聞き分けのウォームアップになります</p>}
      </section>
      {fc && (
        <section className="rounded-2xl border bg-card p-5">
          <h2 className="text-sm font-medium">今後14日間の復習予定</h2>
          <p className="text-xs text-muted-foreground">登録済みカード {fc.cards} 枚</p>
          <div className="mt-4">
            <BarChart ariaLabel="日ごとの復習予定数" unit="件" data={fc.review_forecast.map((f, i) => ({ label: i === 0 ? "今日" : `${Number(f.date.slice(5, 7))}/${Number(f.date.slice(8))}`, value: f.count }))} height={110} />
          </div>
        </section>
      )}
    </div>
  );
}
