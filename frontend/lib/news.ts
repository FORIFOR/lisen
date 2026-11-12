/** "11/12 13:18 公開（3時間前）" from an ISO time; `old` when it is not really today's news any more. */
export function published(iso: string | null): { text: string; old: boolean } {
  if (!iso) return { text: "公開日時不明", old: true };
  const h = (Date.now() - new Date(iso).getTime()) / 3_600_000;
  const at = new Date(iso).toLocaleString("ja-JP", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
  const ago = h < 1 ? "1時間以内" : h < 24 ? `${Math.floor(h)}時間前` : `${Math.floor(h / 24)}日前`;
  return { text: `${at} 公開（${ago}）`, old: h >= 36 };
}
