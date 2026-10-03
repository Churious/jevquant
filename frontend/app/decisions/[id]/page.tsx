"use client";
import { use } from "react";
import { useApi, pct, time } from "@/lib/api";
import { t } from "@/lib/labels";
type Detail = {
  id: number;
  timestamp: string;
  state: object;
  request: object;
  raw_response: object | null;
  attempts: object[];
  strategies: object[];
  forward_returns: {
    horizon_minutes: number;
    return_value: number;
    timestamp: string;
  }[];
  status: string;
  model_version: string | null;
  latency_ms: number;
  estimated_cost: number | null;
};
export default function Detail({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const { data: d, error } = useApi<Detail>(`/api/decisions/${id}`);
  if (!d) return <p className="loading">{error || "판단 기록 조회 중…"}</p>;
  return (
    <>
      <div className="eyebrow">판단 기록 / 관찰 {id}</div>
      <h1>판단 과정을 재구성합니다.</h1>
      <p>
        {time(d.timestamp)} · {d.model_version ?? "모델 응답 없음"} ·{" "}
        {t(d.status)} · {d.latency_ms.toFixed(0)} ms
      </p>
      <div className="grid-two">
        <div className="panel">
          <h2>시장 상태</h2>
          <pre>{JSON.stringify(d.state, null, 2)}</pre>
        </div>
        <div className="panel">
          <h2>이후 수익률</h2>
          {d.forward_returns.length ? (
            <table>
              <thead>
                <tr>
                  <th>관찰 기간</th>
                  <th>수익률</th>
                  <th>관측 시각</th>
                </tr>
              </thead>
              <tbody>
                {d.forward_returns.map((r) => (
                  <tr key={r.horizon_minutes}>
                    <td>{r.horizon_minutes}분</td>
                    <td>{pct(r.return_value)}</td>
                    <td>{time(r.timestamp)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="empty">
              아직 후속 수익률의 관찰 기간이 지나지 않았습니다.
            </div>
          )}
          <h2 style={{ marginTop: 24 }}>전략 판단</h2>
          <pre>{JSON.stringify(d.strategies, null, 2)}</pre>
        </div>
      </div>
      <div className="metric-row">
        <div className="panel">
          <h2>질문 · 원본 요청</h2>
          <pre>{JSON.stringify(d.request, null, 2)}</pre>
        </div>
        <div className="panel">
          <h2>응답 · 원본 데이터</h2>
          <pre>{JSON.stringify(d.raw_response, null, 2)}</pre>
          <details>
            <summary>요청 시도 기록</summary>
            <pre>{JSON.stringify(d.attempts, null, 2)}</pre>
          </details>
        </div>
      </div>
    </>
  );
}
