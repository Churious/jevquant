"use client";
import Link from "next/link";
import { useApi, time, pct } from "@/lib/api";
import { assetName, t } from "@/lib/labels";
type Decision = {
  id: number;
  timestamp: string;
  symbol: string;
  status: string;
  model_version: string | null;
  long_probability: number | null;
  action: string;
  regime: string;
};
export default function Decisions() {
  const { data, error } = useApi<Decision[]>("/api/decisions");
  return (
    <>
      <div className="eyebrow">연구 / 판단 기록</div>
      <h1>모든 판단을 기록합니다.</h1>
      <p>관망한 판단도 이후 수익률 검증을 위해 보존합니다.</p>
      {error && <p className="error">{error}</p>}
      <div className="panel">
        <h2>모델 판단 관찰</h2>
        {data?.length ? (
          <table>
            <thead>
              <tr>
                {[
                  "시각",
                  "종목",
                  "매수 확률",
                  "결정",
                  "시장 국면",
                  "모델",
                  "상태",
                ].map((x) => (
                  <th key={x}>{x}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {data.map((d) => (
                <tr key={d.id}>
                  <td>
                    <Link href={`/decisions/${d.id}`}>{time(d.timestamp)}</Link>
                  </td>
                  <td>{assetName(d.symbol)}</td>
                  <td>{pct(d.long_probability)}</td>
                  <td>{t(d.action)}</td>
                  <td className="muted">{t(d.regime)}</td>
                  <td className="mono">{d.model_version ?? "응답 없음"}</td>
                  <td>{t(d.status)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <div className="empty">
            아직 모델 판단 기록이 없습니다.
            <br />
            서버에서 TypeSafe API 또는 로컬 판단 서버를 설정하면 수집이
            시작됩니다.
          </div>
        )}
      </div>
    </>
  );
}
