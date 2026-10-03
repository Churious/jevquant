"use client";
import { useCallback, useEffect, useState } from "react";
export function useApi<T>(url: string) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const refresh = useCallback(async () => {
    try {
      const r = await fetch(url, { cache: "no-store" });
      if (!r.ok) throw new Error(`조회 실패 (${r.status})`);
      setData(await r.json());
      setError("");
    } catch (e) {
      setError(
        e instanceof TypeError
          ? "서버 연결이 끊겼습니다. 잠시 후 자동으로 다시 조회합니다."
          : e instanceof Error
            ? e.message
            : "서버 연결 오류",
      );
    }
  }, [url]);
  useEffect(() => {
    setData(null);
    void refresh();
    const timer = setInterval(refresh, 15000);
    return () => clearInterval(timer);
  }, [refresh]);
  return { data, error, refresh };
}
export const money = (n: number | null | undefined) =>
  n == null
    ? "—"
    : new Intl.NumberFormat("ko-KR", {
        style: "currency",
        currency: "KRW",
        maximumFractionDigits: 0,
      }).format(n);
export const usd = (n: number | null | undefined) =>
  n == null ? "—" : `$${n.toFixed(6)} USD`;
export const pct = (n: number | null | undefined) =>
  n == null ? "—" : `${(n * 100).toFixed(2)}%`;
export const time = (s: string) =>
  new Date(s).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" });
