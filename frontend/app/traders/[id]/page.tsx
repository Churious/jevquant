"use client";
import { Suspense } from "react";
import { useParams, useSearchParams } from "next/navigation";
import Link from "next/link";
import { money, pct, time, useApi } from "@/lib/api";
import { assetName } from "@/lib/labels";
import {
  CurvePoint,
  Decision,
  Participant,
  Position,
  Standing,
  statusName,
} from "@/lib/tournament";
import { Decisions, Positions, TournamentChart } from "@/components/tournament";
type Detail = Participant & {
  tournament_id: string;
  portfolio: Standing;
  tournament_status: string;
};
type Trade = {
  id: number;
  symbol: string;
  side: string;
  entry_price: number;
  exit_price: number;
  quantity: number;
  pnl: number;
  fees: number;
  opened_at: string;
  closed_at: string;
  reason: string;
  strategy_decision_id: number;
  model_version: string | null;
};
function DetailView({ d }: { d: Detail }) {
  const query = `?tournament_id=${encodeURIComponent(d.tournament_id)}`;
  const { data: curves } = useApi<CurvePoint[]>(
    `/api/tournament/${d.tournament_id}/equity`,
  );
  const { data: positions, error: positionError } = useApi<Position[]>(
    `/api/traders/${d.trader_id}/positions${query}`,
  );
  const { data: decisions, error: decisionError } = useApi<Decision[]>(
    `/api/traders/${d.trader_id}/decisions${query}`,
  );
  const { data: trades, error: tradeError } = useApi<Trade[]>(
    `/api/traders/${d.trader_id}/trades${query}`,
  );
  const p = d.portfolio;
  return (
    <>
      <Link href="/">← 대회 순위</Link>
      <div className="topline">
        <div>
          <div className="eyebrow">
            독립 포트폴리오 / {d.kind === "jev" ? "Jev 투자자" : "기준 전략"}
          </div>
          <h1>{d.name}</h1>
          <p>
            {d.runtime
              ? `${d.runtime.provider} · ${d.runtime.model}`
              : "모델 호출 없는 규칙 전략"}{" "}
            · {d.tournament_id}
          </p>
        </div>
        <span className="badge">
          {statusName(d.tournament_status)} · {p.rank}위
        </span>
      </div>
      <div className="stats">
        {[
          ["시작금", money(p.starting_capital)],
          ["현재 평가액", money(p.current_equity)],
          ["총 수익률", pct(p.return_pct)],
          ["실현 손익", money(p.realized_pnl)],
          ["미실현 손익", money(p.unrealized_pnl)],
          ["최대 낙폭", pct(p.max_drawdown)],
          ["승률", pct(p.win_rate)],
          ["완료 거래", String(p.trades)],
        ].map(([label, value]) => (
          <div className="stat" key={label}>
            <label>{label}</label>
            <strong>{value}</strong>
          </div>
        ))}
      </div>
      <section className="panel">
        <TournamentChart points={curves ?? []} participants={[d]} />
      </section>
      <section className="panel">
        <h2>현재 포지션</h2>
        {positionError ? (
          <p className="error">{positionError}</p>
        ) : (
          <Positions rows={positions ?? []} />
        )}
      </section>
      <section className="panel">
        <h2>최근 판단</h2>
        {decisionError ? (
          <p className="error">{decisionError}</p>
        ) : (
          <Decisions rows={decisions ?? []} />
        )}
      </section>
      <section className="panel">
        <h2>거래 이력 · 최근 200건</h2>
        {tradeError ? (
          <p className="error">{tradeError}</p>
        ) : trades?.length ? (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  {[
                    "종목",
                    "진입 / 청산",
                    "수량",
                    "순손익",
                    "수수료",
                    "청산 시각",
                    "사유",
                    "판단 / 모델",
                  ].map((h) => (
                    <th key={h}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {trades.map((r) => (
                  <tr key={r.id}>
                    <td>{assetName(r.symbol)}</td>
                    <td>
                      {money(r.entry_price)} / {money(r.exit_price)}
                    </td>
                    <td>{r.quantity.toFixed(6)}</td>
                    <td className={r.pnl >= 0 ? "positive" : "negative"}>
                      {money(r.pnl)}
                    </td>
                    <td>{money(r.fees)}</td>
                    <td>{time(r.closed_at)}</td>
                    <td>{r.reason}</td>
                    <td>
                      #{r.strategy_decision_id}
                      <small>{r.model_version ?? "규칙 기반"}</small>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="empty">완료한 거래가 없습니다.</div>
        )}
      </section>
      <section className="panel">
        <h2>노출과 비용</h2>
        <div className="tournament-meta">
          <span>
            투자 시간 비율 <b>{pct(p.time_in_market)}</b>
          </span>
          <span>
            평균 / 최대 노출{" "}
            <b>
              {pct(p.average_exposure)} / {pct(p.max_exposure)}
            </b>
          </span>
          <span>
            가상자산 노출 <b>{money(p.crypto_exposure)}</b>
          </span>
          <span>
            한국 주식·ETF 노출 <b>{money(p.stock_exposure)}</b>
          </span>
          <span>
            누적 거래대금 <b>{money(p.turnover)}</b>
          </span>
          <span>
            지급 수수료 <b>{money(p.fees_paid)}</b>
          </span>
        </div>
      </section>
      <section className="panel">
        <details>
          <summary>고정된 Trader 설정과 질문</summary>
          <pre>
            {JSON.stringify(
              {
                configuration_hash: d.configuration_hash,
                definition: d.definition,
                configuration: d.configuration,
              },
              null,
              2,
            )}
          </pre>
        </details>
      </section>
    </>
  );
}
function Trader() {
  const { id } = useParams<{ id: string }>();
  const query = useSearchParams();
  const { data, error } = useApi<Detail>(
    `/api/traders/${id}${query.get("tournament_id") ? `?tournament_id=${encodeURIComponent(query.get("tournament_id")!)}` : ""}`,
  );
  return (
    <>
      {error && <p className="error">{error}</p>}
      {data ? (
        <DetailView d={data} />
      ) : (
        !error && (
          <div className="loading">참가자 정보를 불러오는 중입니다.</div>
        )
      )}
    </>
  );
}
export default function Page() {
  return (
    <Suspense fallback={<div className="loading">참가자 조회 중</div>}>
      <Trader />
    </Suspense>
  );
}
