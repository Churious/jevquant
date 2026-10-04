"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { money, useApi, time } from "@/lib/api";
import { assetName, t } from "@/lib/labels";
import { Tournament, Position, Decision, CurvePoint } from "@/lib/tournament";
import {
  ComparisonCards,
  Countdown,
  Decisions,
  Leaderboard,
  Positions,
  TournamentChart,
} from "@/components/tournament";
function Competition({ d }: { d: Tournament }) {
  const { data: curves } = useApi<CurvePoint[]>(
    `/api/tournament/${d.id}/equity`,
  );
  const [positions, setPositions] = useState<Position[]>([]);
  const [positionError, setPositionError] = useState(false);
  const [decisionTrader, setDecisionTrader] = useState(
    d.participants.find((p) => p.kind === "jev")?.trader_id ?? "",
  );
  const { data: decisions } = useApi<Decision[]>(
    `/api/traders/${decisionTrader}/decisions?tournament_id=${d.id}&limit=8`,
  );
  const ids = d.participants.map((p) => p.trader_id).join(",");
  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try {
        const result = await Promise.all(
          ids.split(",").map(async (id) => {
            const r = await fetch(
              `/api/traders/${id}/positions?tournament_id=${d.id}`,
              { cache: "no-store" },
            );
            if (!r.ok) throw new Error("positions");
            return r.json() as Promise<Position[]>;
          }),
        );
        if (active) {
          setPositions(result.flat());
          setPositionError(false);
        }
      } catch {
        if (active) setPositionError(true);
      }
    };
    void refresh();
    const timer = setInterval(refresh, 15000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [d.id, ids]);
  const leaders = d.leaderboard.filter((r) => r.rank === 1);
  return (
    <>
      <div className="topline">
        <div>
          <div className="eyebrow">동일 시장 · 독립 자금 · 실제 주문 없음</div>
          <h1>{d.name}</h1>
          <p>
            어떤 Jev 투자 방식이 가장 많은 돈을 벌까?{" "}
            {d.participants.filter((p) => p.kind === "jev").length}개 Jev와{" "}
            {d.participants.filter((p) => p.kind === "baseline").length}개 기준
            전략이 경쟁합니다.
          </p>
        </div>
        <span className="badge">읽기 전용 · 가상거래</span>
      </div>
      <Countdown tournament={d} />
      <div className="tournament-meta">
        <span>
          참가자별 시작금 <b>{money(d.starting_capital)}</b>
        </span>
        <span>
          시장 <b>{d.market_universe.map(assetName).join(" · ")}</b>
        </span>
        <span>
          현재 1위{" "}
          <b>
            {leaders.length > 1
              ? `공동 ${leaders.length}명`
              : (leaders[0]?.name ?? "—")}
          </b>
        </span>
        {d.ends_at && (
          <span>
            종료 <b>{time(d.ends_at)}</b>
          </span>
        )}
      </div>
      {d.scheduled_start_at && d.scheduled_end_at && (
        <section className="panel">
          <h2>이번 대회 일정</h2>
          <p>예정 시작 {time(d.scheduled_start_at)} · 고정 종료 {time(d.scheduled_end_at)} (한국시간)</p>
          <p>각 시장의 정규장에서만 가상거래합니다. 한국 휴장일: {d.kr_holidays.join(" · ") || "지정된 휴장일 없음"}. 미국 정규장은 뉴욕시간 09:30~16:00입니다.</p>
          <p>시세 키와 과거 데이터가 준비돼야 시작합니다. 늦게 시작해도 종료 시각은 연장하지 않습니다.</p>
          {d.usd_krw && <p>미국 시세는 달러 기준이며, 가상 장부는 고정 환율 1 USD = {d.usd_krw.toLocaleString("ko-KR")}원으로 계산합니다. {d.us_fractional ? "미국 종목은 소수점 수량을 가상 체결합니다." : "미국 종목은 정수 주로 가상 체결합니다."}</p>}
        </section>
      )}
      <section className="panel">
        <div className="market-status">
          {d.market_universe.map((symbol) => (
            <span key={symbol}>
              {assetName(symbol)}{" "}
              <b>{t(d.market_status[symbol] ?? "WAITING")}</b>
            </span>
          ))}
        </div>
        <div className="panel-head">
          <h2>{d.status === "COMPLETED" ? "최종 순위" : "실시간 순위"}</h2>
          <span className="muted">평가액 내림차순 · 수수료 반영</span>
        </div>
        <Leaderboard rows={d.leaderboard} id={d.id} />
      </section>
      <ComparisonCards comparison={d.comparison} />
      <section className="panel">
        <TournamentChart points={curves ?? []} participants={d.participants} />
      </section>
      <section className="panel">
        <h2>현재 보유 포지션</h2>
        {positionError ? (
          <p className="error">
            포지션을 조회할 수 없습니다. 잠시 후 다시 조회합니다.
          </p>
        ) : (
          <Positions
            rows={positions}
            names={Object.fromEntries(
              d.participants.map((p) => [p.trader_id, p.name]),
            )}
          />
        )}
      </section>
      <section className="panel">
        <div className="panel-head">
          <h2>최근 Jev 판단</h2>
          <select
            aria-label="판단 참가자"
            value={decisionTrader}
            onChange={(e) => setDecisionTrader(e.target.value)}
          >
            {d.participants
              .filter((p) => p.kind === "jev")
              .map((p) => (
                <option key={p.trader_id} value={p.trader_id}>
                  {p.name}
                </option>
              ))}
          </select>
        </div>
        <Decisions rows={decisions ?? []} />
      </section>
      <section className="panel">
        <div className="panel-head">
          <h2>1분 판단 예산</h2>
          <span className="badge">
            동시 요청 {d.decision_budget.concurrency}개
          </span>
        </div>
        <p>
          완료 {d.decision_budget.completed_before_deadline} / 전체{" "}
          {d.decision_budget.jobs} · 대기 {d.decision_budget.pending} · 마감
          누락 {d.decision_budget.deadline_misses}. 늦은 판단은 관망으로
          기록합니다. 로컬 CPU에서는 호출량에 따라 누락이 생길 수 있습니다.
        </p>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                {[
                  "참가자",
                  "정상 완료",
                  "마감 누락",
                  "오류",
                  "평균 추론",
                  "평균 대기",
                ].map((h) => (
                  <th key={h}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Object.entries(d.decision_budget.per_trader).map(([id, b]) => (
                <tr key={id}>
                  <td>
                    {d.participants.find((p) => p.trader_id === id)?.name}
                  </td>
                  <td>
                    {b.ok} / {b.jobs}
                  </td>
                  <td>{b.deadline_misses}</td>
                  <td>{b.errors}</td>
                  <td>
                    {b.average_latency_ms == null
                      ? "—"
                      : `${(b.average_latency_ms / 1000).toFixed(1)}초`}
                  </td>
                  <td>
                    {b.average_queue_wait_ms == null
                      ? "—"
                      : `${(b.average_queue_wait_ms / 1000).toFixed(1)}초`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      <div className="notice">
        설정과 모델은 대회별로 고정합니다. 종료 시 마지막 확정 가격으로
        평가하며, 최종 결과는 이후 시세로 바뀌지 않습니다.{" "}
        <Link href="/results">대회 결과</Link> ·{" "}
        <Link href="/calibration">확률 검증</Link>
      </div>
      <div className="footer">
        <span>{d.id}</span>
        <span>1분 판단 · 계정별 동일 리스크 예산 · 원화 기준</span>
      </div>
    </>
  );
}
export default function Home() {
  const { data, error } = useApi<Tournament>("/api/tournament/current");
  return (
    <>
      {error && <div className="notice error">{error}</div>}
      {data ? (
        <Competition key={data.id} d={data} />
      ) : (
        !error && <div className="loading">대회 현황을 불러오는 중입니다.</div>
      )}
    </>
  );
}
