"use client";

import { useRouter } from "next/navigation";
import { motion } from "motion/react";
import { ClipboardCheck, Headphones, Loader2, RotateCcw, Target } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Logo } from "@/components/app-shell";
import { Button } from "@/components/ui/button";
import { api, LEVEL_LABEL } from "@/lib/api";
import { cn } from "@/lib/utils";

const LEVELS = [
  [1, "ゆっくりした日常会話なら分かる"],
  [2, "日常会話・アナウンスの要点が分かる"],
  [3, "職場・旅行の会話はだいたい分かる"],
  [4, "ニュースや議論についていける"],
  [5, "講義・専門的な話もほぼ分かる"],
] as const;

export default function Onboarding() {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [manual, setManual] = useState(false);

  const startDiagnostic = async () => {
    setBusy(true);
    try {
      await api("/sessions", { body: { mode: "test", form: "A", minutes: 0, replace: true } });
      router.push("/session");
    } catch (e) {
      toast.error((e as Error).message);
      setBusy(false);
    }
  };
  const choose = async (level: number) => {
    setBusy(true);
    try {
      await api("/onboarding/skip", { body: { level } });
      router.push("/");
    } catch (e) {
      toast.error((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto flex min-h-dvh max-w-xl flex-col px-5 py-8">
      <Logo />
      <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }} className="mt-12 flex-1">
        <p className="text-sm font-medium text-primary">LISTENFLOW</p>
        <h1 className="mt-2 text-3xl font-semibold leading-tight tracking-tight md:text-4xl">英語が聞こえる毎日へ。</h1>
        <p className="mt-4 text-muted-foreground">毎日30分から、好きなだけ。あなたが決めるのは学習時間だけ。何をどの順で聞くかは、ListenFlow が聞き取りミスの傾向から組み立てます。</p>
        <ul className="mt-8 space-y-4 text-sm">
          {[
            [Headphones, "初見の音声で理解 → 1文ずつ書き取り", "正解スクリプトと音声区間が完全に対応した教材で、聞き取れなかった音を特定します"],
            [Target, "弱点別の集中訓練", "弱形・短縮形・音の連結・数字・語尾など、ミスの種類ごとに鍛えます"],
            [RotateCcw, "FSRS による自動復習", "間違えた文は忘れかけたタイミングで再出題されます"],
            [ClipboardCheck, "初見テストで成果を測定", "Day 1・30・60 に、一度も聞いていない音声で伸びを測ります"],
          ].map(([Icon, t, d], i) => {
            const I = Icon as typeof Headphones;
            return (
              <li key={i} className="flex gap-3">
                <span className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-brand-soft">
                  <I className="size-4 text-primary" />
                </span>
                <span>
                  <span className="font-medium">{t as string}</span>
                  <span className="block text-muted-foreground">{d as string}</span>
                </span>
              </li>
            );
          })}
        </ul>

        {!manual ? (
          <div className="mt-10 space-y-3">
            <Button size="lg" className="h-13 w-full text-base" onClick={startDiagnostic} disabled={busy}>
              {busy && <Loader2 className="size-4 animate-spin" />}
              レベル診断を始める（約10分）
            </Button>
            <p className="text-center text-xs text-muted-foreground">イヤホン推奨 · 5本の音声を聞いて、質問と書き取りに答えます</p>
            <button type="button" className="w-full text-center text-sm text-muted-foreground underline-offset-4 hover:underline" onClick={() => setManual(true)}>
              診断せずに自分でレベルを選ぶ
            </button>
          </div>
        ) : (
          <div className="mt-10 space-y-2">
            <p className="mb-3 text-sm font-medium">いまの聞き取り力に近いものは？</p>
            {LEVELS.map(([lv, d]) => (
              <button
                key={lv}
                type="button"
                disabled={busy}
                onClick={() => choose(lv)}
                className={cn("flex w-full items-center gap-3 rounded-xl border bg-card px-4 py-3 text-left text-sm hover:bg-muted")}
              >
                <span className="w-10 font-semibold text-primary">{LEVEL_LABEL[lv]}</span>
                {d}
              </button>
            ))}
            <p className="pt-2 text-xs text-muted-foreground">後からいつでも診断を受けられます。成績に応じてレベルは自動で上下します。</p>
          </div>
        )}
      </motion.div>
    </div>
  );
}
