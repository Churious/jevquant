"use client";
import { useState } from "react";
import { useApi, money, pct } from "@/lib/api";
import { t, strategies } from "@/lib/labels";
type Metrics = {
  total_return: number;
  annualized_return: number | null;
  sharpe_ratio: number | null;
  sortino_ratio: number | null;
  max_drawdown: number;
  profit_factor: number | null;
  win_rate: number | null;
  average_win: number | null;
  average_loss: number | null;
  expectancy: number | null;
  trade_count: number;
};
type Evaluation = {
  run_id: string;
  portfolios: Record<string, Metrics>;
  jev: {
    decision_count: number;
    decision_frequency: number | null;
    abstention_rate: number | null;
    false_positive_rate: number | null;
    false_positive_horizon_minutes: number;
    false_positive_definition: string;
    regimes: Record<
      string,
      { trade_count: number; net_pnl: number; win_rate: number | null }
    >;
  };
  limitations: string;
  config_hash: string;
  metadata: object;
  runs: { id: string; mode: string; metadata: object }[];
};
const rows: [keyof Metrics, string, "percent" | "money" | "number"][] = [
  ["total_return", "누적 수익률", "percent"],
  ["annualized_return", "연환산 수익률", "percent"],
  ["sharpe_ratio", "샤프 지수", "number"],
  ["sortino_ratio", "소르티노 지수", "number"],
  ["max_drawdown", "최대 낙폭", "percent"],
  ["profit_factor", "손익비", "number"],
  ["win_rate", "승률", "percent"],
  ["average_win", "평균 이익", "money"],
  ["average_loss", "평균 손실", "money"],
  ["expectancy", "거래당 기대 손익", "money"],
  ["trade_count", "거래 수", "number"],
];
export default function Evaluation() {
  const [run, setRun] = useState("");
  const { data: d, error } = useApi<Evaluation>(
    `/api/evaluation?run_id=${encodeURIComponent(run)}`,
  );
  return (
    <>
      <div className="topline">
        <div>
          <div className="eyebrow">연구 / 성과 평가</div>
          <h1>결론보다 증거를 먼저.</h1>
          <p>독립된 네 포트폴리오에 동일한 관측과 거래 비용을 적용합니다.</p>
        </div>
        <select
          aria-label="평가할 실험"
          value={run}
          onChange={(e) => setRun(e.target.value)}
        >
          <option value="">현재 실험</option>
          {d?.runs.map((r) => (
            <option key={r.id}>{r.id}</option>
          ))}
        </select>
      </div>
      {error && <p className="error">{error}</p>}
      <div className="panel">
        <h2>가상거래 성과</h2>
        <table>
          <thead>
            <tr>
              <th>지표</th>
              {["buy_hold", "ema", "rsi", "jev"].map((s) => (
                <th key={s}>{strategies.find(([key]) => key === s)?.[1]}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map(([key, label, format]) => (
              <tr key={key}>
                <td className="muted">{label}</td>
                {["buy_hold", "ema", "rsi", "jev"].map((s) => {
                  const v = d?.portfolios[s]?.[key];
                  return (
                    <td key={s}>
                      {format === "percent"
                        ? pct(v)
                        : format === "money"
                          ? money(v)
                          : v == null
                            ? "—"
                            : key === "trade_count"
                              ? v
                              : v.toFixed(3)}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="stats">
        {[
          ["Jev 판단 수", String(d?.jev.decision_count ?? 0)],
          ["방향성 신호 비율", pct(d?.jev.decision_frequency)],
          ["관망 비율", pct(d?.jev.abstention_rate)],
          [
            `오탐 비율 · ${d?.jev.false_positive_horizon_minutes ?? 5}분 수익률 기준`,
            pct(d?.jev.false_positive_rate),
          ],
        ].map(([label, value]) => (
          <div key={label} className="stat">
            <label>{label}</label>
            <strong>{value}</strong>
          </div>
        ))}
      </div>
      <div className="panel">
        <h2>진입 시 시장 국면별 Jev 성과</h2>
        <table>
          <thead>
            <tr>
              <th>시장 국면</th>
              <th>거래 수</th>
              <th>순손익</th>
              <th>상승 비율</th>
            </tr>
          </thead>
          <tbody>
            {Object.entries(d?.jev.regimes ?? {}).map(([r, m]) => (
              <tr key={r}>
                <td>{t(r)}</td>
                <td>{m.trade_count}</td>
                <td>{money(m.net_pnl)}</td>
                <td>{pct(m.win_rate)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="notice">
        {d?.limitations}
        <br />
        {d?.jev.false_positive_definition}
      </div>
      <div className="panel">
        <h2>실험 기록</h2>
        <p>
          개발 → 검증 → 미사용 테스트 데이터 순으로 분리합니다. 개발 구간에서
          선택한 파라미터를 검증 구간에 고정하여 적용하고, 최종 테스트는 기록된
          실험별로 한 번 평가합니다.
        </p>
        <pre>
          {JSON.stringify(
            {
              run_id: d?.run_id ?? run,
              config_hash: d?.config_hash,
              metadata: d?.metadata,
            },
            null,
            2,
          )}
        </pre>
        <p>
          과거 데이터 실험은 서버의 리플레이 명령으로 실행합니다. 결과는 고유
          실험 ID로 이 화면에서 조회할 수 있습니다.
        </p>
      </div>
    </>
  );
}
