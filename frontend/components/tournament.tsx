"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { money, pct, time } from "@/lib/api";
import { assetName, t } from "@/lib/labels";
import {
  Comparison,
  CurvePoint,
  Decision,
  Participant,
  Position,
  Standing,
  Tournament,
  signed,
  statusName,
} from "@/lib/tournament";
export function Countdown({ tournament: d }: { tournament: Tournament }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const seconds = d.ends_at
    ? Math.max(0, Math.floor((new Date(d.ends_at).getTime() - now) / 1000))
    : null;
  return (
    <div className="countdown">
      <span className="badge">{statusName(d.status)}</span>
      <strong>
        {d.status === "COMPLETED"
          ? `${d.duration_days}일 평가 완료`
          : d.status === "EXPIRED"
            ? "예정 기간 종료 · 거래 미시작"
            : seconds == null
            ? d.scheduled_start_at ? "예정 시작·시세 연결 대기" : "시장 데이터 준비 중"
            : `${Math.floor(seconds / 86400)}일 ${String(Math.floor((seconds % 86400) / 3600)).padStart(2, "0")}:${String(Math.floor((seconds % 3600) / 60)).padStart(2, "0")}:${String(seconds % 60).padStart(2, "0")}`}
      </strong>
      <span className="muted">
        {d.day
          ? `${d.day}일차 / ${d.duration_days}일`
          : d.scheduled_start_at ? `예정 ${time(d.scheduled_start_at)}` : "첫 유효 시장 상태에서 시작"}
      </span>
    </div>
  );
}
export function Leaderboard({ rows, id }: { rows: Standing[]; id: string }) {
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            {[
              "순위",
              "참가자",
              "평가액",
              "순손익",
              "수익률",
              "오늘 손익",
              "최대 낙폭",
              "승률",
              "손익비",
              "거래 수",
              "현재 노출",
            ].map((h) => (
              <th key={h}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.trader_id} className={r.rank === 1 ? "leading" : ""}>
              <td className="rank">{r.rank}</td>
              <td>
                <Link
                  href={`/traders/${r.trader_id}?tournament_id=${encodeURIComponent(id)}`}
                >
                  {r.name}
                </Link>
                <small className={`kind ${r.type}`}>
                  {r.type === "jev" ? "Jev 투자자" : "기준 전략"}
                </small>
              </td>
              <td>{money(r.current_equity)}</td>
              <td className={r.net_pnl >= 0 ? "positive" : "negative"}>
                {money(r.net_pnl)}
              </td>
              <td className={r.return_pct >= 0 ? "positive" : "negative"}>
                {signed(r.return_pct * 100, "%")}
              </td>
              <td>{money(r.today_pnl)}</td>
              <td>{pct(r.max_drawdown)}</td>
              <td>{pct(r.win_rate)}</td>
              <td>{r.profit_factor?.toFixed(2) ?? "—"}</td>
              <td>{r.trades}</td>
              <td>
                {pct(r.current_exposure)}
                {r.risk_halted && (
                  <small className="negative">리스크 중단</small>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
const colors = [
  "#b8e891",
  "#71c9dd",
  "#dea969",
  "#be9eed",
  "#f09bbf",
  "#eddf7b",
  "#8c9a96",
  "#577abb",
  "#c47661",
];
export function TournamentChart({
  points,
  participants,
}: {
  points: CurvePoint[];
  participants: Participant[];
}) {
  const [unit, setUnit] = useState("returns");
  const rows = points.map((p) => ({
    timestamp: p.timestamp,
    ...(unit === "returns" ? p.returns : p.equity),
  }));
  return (
    <>
      <div className="panel-head">
        <h2>자산곡선 · {unit === "returns" ? "수익률" : "원화 평가액"}</h2>
        <select
          aria-label="자산곡선 표시 단위"
          value={unit}
          onChange={(e) => setUnit(e.target.value)}
        >
          <option value="returns">수익률 %</option>
          <option value="equity">평가액 KRW</option>
        </select>
      </div>
      {rows.length ? (
        <div className="chart tournament-chart">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart
              data={rows}
              margin={{ top: 8, right: 12, left: 0, bottom: 0 }}
            >
              <CartesianGrid stroke="#2a3130" vertical={false} />
              <XAxis
                dataKey="timestamp"
                tickFormatter={(s) =>
                  new Date(s).toLocaleTimeString("ko-KR", {
                    hour: "2-digit",
                    minute: "2-digit",
                  })
                }
                stroke="#8c9a96"
                tick={{ fontSize: 10 }}
                minTickGap={75}
              />
              <YAxis
                domain={([low, high]: readonly [number, number]) => {
                  const padding =
                    low === high
                      ? unit === "returns"
                        ? 0.001
                        : Math.max(1000, Math.abs(low) * 0.001)
                      : (high - low) * 0.08;
                  return [low - padding, high + padding];
                }}
                tickFormatter={(n) =>
                  unit === "returns"
                    ? `${(Number(n) * 100).toFixed(3)}%`
                    : `${(Number(n) / 10000).toFixed(2)}만`
                }
                stroke="#8c9a96"
                width={66}
                tick={{ fontSize: 11 }}
              />
              <Tooltip
                contentStyle={{
                  background: "#181c1c",
                  border: "1px solid #3c4c36",
                  borderRadius: 6,
                }}
                labelFormatter={(s) => time(String(s))}
                formatter={(n) =>
                  unit === "returns" ? pct(Number(n)) : money(Number(n))
                }
              />
              <Legend wrapperStyle={{ fontSize: 11, paddingTop: 18 }} />
              {participants.map((r, i) => (
                <Line
                  key={r.trader_id}
                  dataKey={r.trader_id}
                  name={r.name}
                  stroke={colors[i % colors.length]}
                  strokeWidth={r.kind === "jev" ? 2 : 1.5}
                  strokeDasharray={r.kind === "baseline" ? "5 4" : undefined}
                  type="stepAfter"
                  dot={false}
                  isAnimationActive={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <div className="empty">
          대회가 시작되면 모든 참가자의 0.00% 지점부터 기록합니다.
        </div>
      )}
    </>
  );
}
export function ComparisonCards({ comparison: c }: { comparison: Comparison }) {
  return (
    <div className="stats">
      <div className="stat">
        <label>최고 Jev</label>
        <strong>{pct(c.best_jev?.return_pct)}</strong>
        <small>{c.best_jev?.name ?? "—"}</small>
      </div>
      <div className="stat">
        <label>최고 기준 전략</label>
        <strong>{pct(c.best_baseline?.return_pct)}</strong>
        <small>{c.best_baseline?.name ?? "—"}</small>
      </div>
      <div className="stat">
        <label>최고 Jev − 최고 기준 전략</label>
        <strong
          className={
            (c.jev_vs_best_baseline_pp ?? 0) >= 0 ? "positive" : "negative"
          }
        >
          {signed(c.jev_vs_best_baseline_pp, "%p")}
        </strong>
        <small>수익률 차이 · 손실도 그대로 표시</small>
      </div>
      <div className="stat">
        <label>Jev 평균 / 중앙 수익률</label>
        <strong>{pct(c.average_jev_return)}</strong>
        <small>중앙값 {pct(c.median_jev_return)}</small>
      </div>
    </div>
  );
}
export function Positions({
  rows,
  names,
}: {
  rows: Position[];
  names?: Record<string, string>;
}) {
  return rows.length ? (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            {[
              ...(names ? ["참가자"] : []),
              "종목",
              "방향",
              "진입가",
              "현재가",
              "수량",
              "손익",
              "손절 / 목표",
              "진입 시각",
            ].map((h) => (
              <th key={h}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((p) => (
            <tr key={p.id}>
              {names && <td>{names[p.trader_id]}</td>}
              <td>{assetName(p.symbol)}</td>
              <td>{t(p.side)}</td>
              <td>{money(p.entry_price)}</td>
              <td>{money(p.current_price)}</td>
              <td>{p.quantity.toFixed(6)}</td>
              <td className={p.pnl >= 0 ? "positive" : "negative"}>
                {money(p.pnl)}
              </td>
              <td>
                {money(p.stop_loss)} / {money(p.take_profit)}
              </td>
              <td>{time(p.timestamp)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ) : (
    <div className="empty">보유 포지션이 없습니다.</div>
  );
}
export function Decisions({ rows }: { rows: Decision[] }) {
  return rows.length ? (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            {[
              "시각",
              "종목",
              "판단 / 모델 상태",
              "확신도",
              "전략 행동",
              "체결 상태",
              "추론 시간",
            ].map((h) => (
              <th key={h}>{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((d) => (
            <tr key={d.id}>
              <td>
                {d.decision_id ? (
                  <Link href={`/decisions/${d.decision_id}`}>
                    {time(d.timestamp)}
                  </Link>
                ) : (
                  time(d.timestamp)
                )}
              </td>
              <td>{assetName(d.symbol)}</td>
              <td>
                {d.model_status ? t(d.model_status) : "규칙 기반"}
                <small className="muted">{t(d.reason)}</small>
              </td>
              <td>{pct(d.confidence)}</td>
              <td>{t(d.action)}</td>
              <td>{t(d.status)}</td>
              <td>
                {d.decision_latency_ms == null
                  ? "—"
                  : `${(d.decision_latency_ms / 1000).toFixed(1)}초`}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  ) : (
    <div className="empty">아직 판단 기록이 없습니다.</div>
  );
}
