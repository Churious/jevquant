"use client";
import { useState } from "react";
import Link from "next/link";
import { EquityChart, EquityPoint } from "@/components/charts";
import { useApi, money, pct, time, usd } from "@/lib/api";
import { assetName, strategies, t } from "@/lib/labels";
type Overview = {
  decision_runtime: { provider: string; model: string; concurrency: number };
  decision_timeframe: string;
  max_holding_minutes: number;
  status: string;
  starting_capital: number;
  portfolio_value: number;
  total_pnl: number;
  today_pnl: number;
  win_rate: number | null;
  max_drawdown: number;
  open_positions: number;
  total_trades: number;
  jev_calls: number;
  jev_calls_today: number;
  average_latency_ms: number | null;
  jev_errors_today: number;
  estimated_api_cost: number | null;
  timestamp: string;
  websocket: string;
};
type Answer = {
  choice?: string;
  score?: number;
  confidence?: number;
  noul?: number;
};
type Market = {
  symbol: string;
  price: number | null;
  change_24h: number | null;
  rsi: number | null;
  trend: string | null;
  jev: Record<string, Answer>;
  decision: string;
  status: string;
  timestamp: string | null;
};
type Position = {
  id: number;
  symbol: string;
  side: string;
  entry_price: number;
  current_price: number;
  quantity: number;
  pnl: number;
  stop_loss: number | null;
  take_profit: number | null;
  duration_minutes: number;
};
type Trade = {
  id: number;
  symbol: string;
  side: string;
  closed_at: string;
  entry_price: number;
  exit_price: number;
  pnl: number;
  pnl_percent: number;
  jev_confidence: number | null;
  reason: string;
  decision_id: number | null;
};
export default function Home() {
  const { data: o, error } = useApi<Overview>("/api/overview");
  const { data: markets } = useApi<Market[]>("/api/market");
  const { data: curve } = useApi<EquityPoint[]>("/api/equity");
  const [strategy, setStrategy] = useState("jev");
  const { data: positions } = useApi<Position[]>(
    `/api/positions?strategy=${strategy}`,
  );
  const { data: trades } = useApi<Trade[]>(`/api/trades?strategy=${strategy}`);
  const stats = o
    ? [
        ["가상 자산 평가액", money(o.portfolio_value), "Jev 전략 · 원화 기준"],
        ["누적 손익", money(o.total_pnl), "수수료·슬리피지 반영"],
        ["오늘의 손익", money(o.today_pnl), "한국 시간 기준"],
        ["승률", pct(o.win_rate), "청산된 거래 기준"],
        ["최대 낙폭", pct(o.max_drawdown), "관측 자산 곡선 기준"],
        ["보유 포지션", String(o.open_positions), "Jev 전략"],
        ["완료 거래", String(o.total_trades), "진입부터 청산까지"],
        ["판단 호출", String(o.jev_calls), "실제 API 요청 횟수"],
      ]
    : [];
  return (
    <>
      <div className="topline">
        <div>
          <div className="eyebrow">가상거래 연구실 / 현황</div>
          <h1>판단의 가치를 데이터로.</h1>
          <p style={{ marginBottom: 0 }}>
            한국 주식·ETF와 원화 가상자산 시장을 함께 관찰합니다.
          </p>
        </div>
        <span className={`badge ${o?.status !== "RUNNING" ? "warn" : ""}`}>
          {t(o?.status ?? "CONNECTING")}
        </span>
      </div>
      <div className="notice">
        전략마다 {money(o?.starting_capital ?? 1000000)}의 가상 자금으로
        시작합니다. 확정된 1분봉마다 판단하고, 거래 비용을 반영해 자동으로
        가상거래를 실행합니다. 이 화면은 조회만 가능합니다.
      </div>
      {error && <p className="error">{error}</p>}
      <div className="stats">
        {stats.map(([label, value, note]) => (
          <div className="stat" key={label}>
            <label>{label}</label>
            <strong>{value}</strong>
            <small>{note}</small>
          </div>
        ))}
      </div>
      <div className="grid-two">
        <div className="panel">
          <div className="panel-head">
            <h2>자산 곡선 · 동일 데이터, 네 가지 전략</h2>
            <span className="eyebrow">비용 차감 후</span>
          </div>
          {curve?.length ? (
            <EquityChart points={curve} />
          ) : (
            <div className="empty">자산 평가 기록을 기다리고 있습니다.</div>
          )}
        </div>
        <div className="panel">
          <div className="panel-head">
            <h2>연구 운영 현황</h2>
            <span className="badge">
              {o?.decision_timeframe === "1m" ? "1분 단타" : "15분 전략"}
            </span>
          </div>
          <table>
            <tbody>
              {[
                [
                  "판단 실행 방식",
                  o?.decision_runtime.provider === "local"
                    ? "로컬 · Jev 호환"
                    : "TypeSafe · Jev API",
                ],
                ["사용 모델", o?.decision_runtime.model ?? "—"],
                ["오늘의 판단 호출", o?.jev_calls_today ?? "—"],
                [
                  "평균 응답 시간",
                  o?.average_latency_ms != null
                    ? `${o.average_latency_ms.toFixed(0)} ms`
                    : "—",
                ],
                ["응답 실패·키 미설정", o?.jev_errors_today ?? "—"],
                [
                  "예상 API 비용",
                  o?.decision_runtime.provider === "local"
                    ? "로컬 추론 · API 과금 없음"
                    : usd(o?.estimated_api_cost),
                ],
                ["실시간 데이터 연결", t(o?.websocket)],
                [
                  "최대 보유 시간 · 단타 전략",
                  o?.max_holding_minutes ? `${o.max_holding_minutes}분` : "—",
                ],
              ].map(([k, v]) => (
                <tr key={k}>
                  <td className="muted">{k}</td>
                  <td style={{ textAlign: "right" }}>{v}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p style={{ fontSize: 12 }}>
            {o?.decision_runtime.provider === "local"
              ? "로컬 모델은 TypeSafe Jev와 구분해 기록합니다. "
              : "TypeSafe 키가 필요합니다. "}
            응답에 실패하거나 시간 제한을 넘으면 관망합니다. 기준에 못 미친
            판단도 이후 수익률과 함께 저장합니다.
          </p>
          <Link href="/calibration" className="positive">
            확률 검증 살펴보기 ↗
          </Link>
        </div>
      </div>
      <div className="panel">
        <div className="panel-head">
          <h2>시장 관찰</h2>
          <span className="eyebrow">완료된 봉 · 실제 시세</span>
        </div>
        <div className="market-cards">
          {markets?.map((m) => (
            <article className="market-card" key={m.symbol}>
              <div className="panel-head">
                <div>
                  <strong>{assetName(m.symbol)}</strong>
                  <small
                    className="muted"
                    style={{ display: "block", marginTop: 5 }}
                  >
                    {m.symbol}
                  </small>
                </div>
                <span className="badge">{t(m.decision)}</span>
              </div>
              <div className="market-price">
                {money(m.price)}{" "}
                <small
                  className={(m.change_24h ?? 0) >= 0 ? "positive" : "negative"}
                >
                  {pct(m.change_24h)}
                </small>
              </div>
              <div className="market-detail">
                <span>
                  RSI 14 <b>{m.rsi?.toFixed(1) ?? "—"}</b>
                </span>
                <span>
                  추세 <b>{t(m.trend)}</b>
                </span>
                <span>
                  Jev 추세 <b>{t(m.jev.trend_direction?.choice)}</b>
                </span>
                <span>
                  추세 확신도 <b>{pct(m.jev.trend_direction?.confidence)}</b>
                </span>
                <span>
                  셋업 / 반전{" "}
                  <b>
                    {m.jev.setup_quality?.score?.toFixed(2) ?? "—"} /{" "}
                    {m.jev.reversal_risk?.score?.toFixed(2) ?? "—"}
                  </b>
                </span>
                <span>
                  거래 회피 <b>{pct(m.jev.avoid_trade?.noul)}</b>
                </span>
              </div>
              <div className="market-foot">
                <i />
                {t(m.status)}
                <small>
                  {m.timestamp ? time(m.timestamp) : "시세 연결 대기"}
                </small>
              </div>
            </article>
          ))}
        </div>
      </div>
      <div className="panel">
        <div className="panel-head">
          <h2>보유 포지션</h2>
          <select
            aria-label="조회할 전략"
            value={strategy}
            onChange={(e) => setStrategy(e.target.value)}
          >
            {strategies.map(([k, label]) => (
              <option key={k} value={k}>
                {label}
              </option>
            ))}
          </select>
        </div>
        {positions?.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  {[
                    "종목",
                    "방향",
                    "진입가",
                    "현재가",
                    "수량",
                    "손익",
                    "손절가",
                    "익절가",
                    "보유 시간",
                  ].map((x) => (
                    <th key={x}>{x}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {positions.map((p) => (
                  <tr key={p.id}>
                    <td>{assetName(p.symbol)}</td>
                    <td>{p.side === "LONG" ? "매수" : "공매도"}</td>
                    <td>{money(p.entry_price)}</td>
                    <td>{money(p.current_price)}</td>
                    <td className="mono">
                      {p.symbol.includes("/")
                        ? p.quantity.toFixed(6)
                        : p.quantity.toFixed(0)}
                    </td>
                    <td className={p.pnl >= 0 ? "positive" : "negative"}>
                      {money(p.pnl)}
                    </td>
                    <td>{money(p.stop_loss)}</td>
                    <td>{money(p.take_profit)}</td>
                    <td>{Math.floor(p.duration_minutes)}분</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty">
            이 전략의 보유 포지션이 없습니다.
            <br />새 포지션은 신호와 리스크 검증을 모두 통과해야 합니다.
          </div>
        )}
      </div>
      <div className="panel">
        <h2>거래 내역 · {strategies.find(([key]) => key === strategy)?.[1]}</h2>
        {trades?.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  {[
                    "청산 시각",
                    "종목",
                    "방향",
                    "진입가",
                    "청산가",
                    "손익",
                    "수익률",
                    "Jev 확률",
                    "사유",
                  ].map((x) => (
                    <th key={x}>{x}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {trades.map((r) => (
                  <tr key={r.id}>
                    <td>{time(r.closed_at)}</td>
                    <td>{assetName(r.symbol)}</td>
                    <td>{r.side === "LONG" ? "매수" : "공매도"}</td>
                    <td>{money(r.entry_price)}</td>
                    <td>{money(r.exit_price)}</td>
                    <td className={r.pnl >= 0 ? "positive" : "negative"}>
                      {money(r.pnl)}
                    </td>
                    <td>{pct(r.pnl_percent)}</td>
                    <td>
                      {r.decision_id ? (
                        <Link href={`/decisions/${r.decision_id}`}>
                          {pct(r.jev_confidence)}
                        </Link>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="muted">{t(r.reason)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty">
            완료된 거래가 없습니다. 판단과 거래 기록은 시간에 따라 쌓입니다.
          </div>
        )}
      </div>
      <div className="footer">
        <span>실험용 가상거래 시스템 · 실제 금융 주문 없음</span>
        <span>{o ? `최근 갱신 ${time(o.timestamp)}` : "연결 중"}</span>
      </div>
    </>
  );
}
