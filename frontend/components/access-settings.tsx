"use client";

import { Eye, Loader2, LogOut, RefreshCw, ShieldCheck, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { api, type Backup, type Device } from "@/lib/api";

const fmt = (iso: string) => new Date(iso).toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });

export function AccessSettings() {
  const [devices, setDevices] = useState<Device[] | null>(null);
  const [code, setCode] = useState<string | null>(null);
  const [confirmRotate, setConfirmRotate] = useState(false);
  const [backups, setBackups] = useState<{ backups: Backup[]; keep: number } | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api<{ devices: Device[] }>("/auth/devices").then((r) => setDevices(r.devices)).catch(() => {});
    api<{ backups: Backup[]; keep: number }>("/backups").then(setBackups).catch(() => {});
  }, []);
  useEffect(load, [load]);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const revoke = (d: Device) =>
    run(async () => {
      await api(`/auth/devices/${d.id}`, { method: "DELETE" });
      if (d.current) window.location.replace("/login");
      else load();
    });
  const rotate = () =>
    run(async () => {
      const r = await api<{ code: string }>("/auth/code/rotate", { method: "POST" });
      setCode(r.code);
      setConfirmRotate(false);
      toast.success("新しいアクセスコードにしました。ほかの端末は登録し直してください");
      load();
    });
  const logout = () =>
    run(async () => {
      await api("/auth/logout", { method: "POST" });
      window.location.replace("/login");
    });
  const backupNow = () =>
    run(async () => {
      await api("/backups", { method: "POST" });
      toast.success("バックアップしました");
      load();
    });

  return (
    <section className="rounded-2xl border bg-card p-4 md:p-5">
      <h2 className="flex items-center gap-1.5 text-sm font-medium">
        <ShieldCheck className="size-4" /> 端末とアクセス
      </h2>
      <p className="mt-1 text-xs text-muted-foreground">
        学習データを開けるのは、アクセスコードで登録した端末だけです。新しい端末は、そのブラウザでアクセスコードを入力して登録します。
      </p>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {code ? (
          <code className="rounded bg-muted px-3 py-1.5 font-mono text-base tracking-widest">{code}</code>
        ) : (
          <Button variant="outline" size="sm" disabled={busy} onClick={() => run(async () => setCode((await api<{ code: string }>("/auth/code")).code))}>
            <Eye className="size-3.5" /> アクセスコードを表示
          </Button>
        )}
        {confirmRotate ? (
          <>
            <span className="text-xs text-muted-foreground">この端末以外はすべて登録が外れます。</span>
            <Button variant="destructive" size="sm" disabled={busy} onClick={rotate}>
              変更する
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setConfirmRotate(false)}>
              やめる
            </Button>
          </>
        ) : (
          <Button variant="ghost" size="sm" disabled={busy} onClick={() => setConfirmRotate(true)}>
            <RefreshCw className="size-3.5" /> コードを変更
          </Button>
        )}
      </div>

      <ul className="mt-4 divide-y text-sm">
        {devices === null && (
          <li className="py-2 text-muted-foreground">
            <Loader2 className="size-4 animate-spin" />
          </li>
        )}
        {devices?.length === 0 && <li className="py-2 text-xs text-muted-foreground">登録済みの端末はありません（この Mac から直接開いています）</li>}
        {devices?.map((d) => (
          <li key={d.id} className="flex items-center gap-2 py-2">
            <div className="min-w-0 flex-1">
              <div className="truncate">
                {d.name}
                {d.current && <span className="ml-1.5 text-xs text-primary">この端末</span>}
              </div>
              <div className="text-xs text-muted-foreground">
                登録 {fmt(d.created_at)} · 最終利用 {fmt(d.last_seen)}
              </div>
            </div>
            {d.current ? (
              <Button variant="ghost" size="sm" disabled={busy} onClick={logout}>
                <LogOut className="size-3.5" /> 登録を外す
              </Button>
            ) : (
              <Button variant="ghost" size="sm" disabled={busy} onClick={() => revoke(d)} aria-label={`${d.name} の登録を外す`}>
                <Trash2 className="size-3.5" /> 外す
              </Button>
            )}
          </li>
        ))}
      </ul>

      <div className="mt-4 border-t pt-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <div className="text-sm font-medium">自動バックアップ</div>
            <div className="text-xs text-muted-foreground">
              起動時と毎日1回、学習データを Mac の <code>data/backups</code> に保存します（最新 {backups?.keep ?? 14} 日分）
            </div>
          </div>
          <Button variant="outline" size="sm" disabled={busy} onClick={backupNow}>
            今すぐバックアップ
          </Button>
        </div>
        <div className="mt-2 text-xs text-muted-foreground">
          {backups && backups.backups.length > 0
            ? `最新: ${fmt(backups.backups[0].created_at)}（${backups.backups.length} 件）`
            : "まだバックアップはありません"}
        </div>
      </div>
    </section>
  );
}
