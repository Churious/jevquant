"use client";
import { useApi, money, pct, time } from "@/lib/api";
import { Tournament } from "@/lib/tournament";
import {
  ComparisonCards,
  Countdown,
  Leaderboard,
} from "@/components/tournament";
export default function Results() {
  const { data: d, error } = useApi<Tournament>("/api/tournament/current");
  if (!d)
    return (
      <div className={error ? "error" : "loading"}>
        {error || "결과를 불러오는 중입니다."}
      </div>
    );
  const r = d.final_report;
  return (
    <>
      <div className="eyebrow">7일 대회 / 최종 평가</div>
      <h1>대회 결과</h1>
      {error && <p className="error">{error}</p>}
      <Countdown tournament={d} />
      {!r && (
        <div className="notice">
          대회가 진행 중입니다. 아래는 현재 성과이며, 7일 종료 후 최종 보고서가
          자동으로 고정됩니다.
        </div>
      )}
      <ComparisonCards comparison={r ?? d.comparison} />
      {r && (
        <section className="panel">
          <h2>
            {r.winner
              ? `우승 · ${r.winner.name}`
              : `공동 1위 · ${r.joint_winners.length}명`}
          </h2>
          <p>
            종료 {d.ends_at && time(d.ends_at)} · 마지막 확정 가격에 따른 평가액
          </p>
          <p>
            최저 Jev: {r.worst_jev?.name} {pct(r.worst_jev?.return_pct)} ·
            미실현 포지션을 유지한 평가입니다.
          </p>
        </section>
      )}
      <section className="panel">
        <h2>{r ? "최종 전체 순위" : "현재 전체 순위"}</h2>
        <Leaderboard rows={r?.ranking ?? d.leaderboard} id={d.id} />
      </section>
      <section className="panel">
        <h2>비용과 투자 노출</h2>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                {[
                  "참가자",
                  "지급 수수료",
                  "거래대금",
                  "투자 시간 비율",
                  "평균 노출",
                  "최대 노출",
                ].map((h) => (
                  <th key={h}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {(r?.ranking ?? d.leaderboard).map((p) => (
                <tr key={p.trader_id}>
                  <td>{p.name}</td>
                  <td>{money(p.fees_paid)}</td>
                  <td>{money(p.turnover)}</td>
                  <td>{pct(p.time_in_market)}</td>
                  <td>{pct(p.average_exposure)}</td>
                  <td>{pct(p.max_exposure)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
      {r && (
        <>
          <section className="panel">
            <h2>위험 대비 성과 순위</h2>
            <p>
              대회 수익률 ÷ 최대 낙폭. 낙폭이 0이면 계산하지 않습니다. 7일
              데이터의 샤프 지수는 표시하지 않습니다.
            </p>
            <table>
              <thead>
                <tr>
                  <th>순서</th>
                  <th>참가자</th>
                  <th>수익률 / 최대 낙폭</th>
                </tr>
              </thead>
              <tbody>
                {r.risk_adjusted_ranking.map((p, i) => (
                  <tr key={p.trader_id}>
                    <td>{p.risk_adjusted_score == null ? "—" : i + 1}</td>
                    <td>{p.name}</td>
                    <td>{p.risk_adjusted_score?.toFixed(3) ?? "계산 불가"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
          <section className="panel">
            <h2>최종 평가 가격</h2>
            <p>
              종료 이전 마지막 확정 봉을 사용합니다. 포지션을 가상 청산하지
              않으므로 추가 청산 수수료는 차감하지 않습니다.
            </p>
            {Object.entries(r.valuation_prices).map(([symbol, p]) => (
              <p key={symbol}>
                {symbol} · {money(p.price)} · {time(p.observed_at)}{" "}
                {p.stale ? "· 오래된 시세" : ""}
              </p>
            ))}
          </section>
        </>
      )}
      <div className="footer">
        <span>{d.id}</span>
        <span className="mono">설정 {d.configuration_hash.slice(0, 16)}</span>
      </div>
    </>
  );
}
