"use client";
import { useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useApi, pct } from "@/lib/api";
import { t } from "@/lib/labels";
type Bucket = {
  low: number;
  high: number;
  sample_count: number;
  mean_return: number | null;
  median_return: number | null;
  win_rate: number | null;
  standard_deviation: number | null;
  mean_probability: number | null;
};
type Calibration = {
  horizon_minutes: number;
  direction: string;
  axis: string;
  buckets: Bucket[];
  total_samples: number;
  correlation: number | null;
  observed_days: number;
  below_bucket_range: number;
  mean_return_daily_block_ci95: number[] | null;
  models: string[];
  note: string;
  brier_direction_proxy: number | null;
};
export default function Calibration() {
  const [horizon, setHorizon] = useState("5");
  const [direction, setDirection] = useState("long");
  const [axis, setAxis] = useState("probability");
  const [regime, setRegime] = useState("");
  const [run, setRun] = useState("");
  const [model, setModel] = useState("");
  const { data: runs } = useApi<{ runs: { id: string; mode: string }[] }>(
    "/api/evaluation",
  );
  const { data: d, error } = useApi<Calibration>(
    `/api/calibration?run_id=${encodeURIComponent(run)}&horizon=${horizon}&direction=${direction}&axis=${axis}${regime ? `&regime=${regime}` : ""}${model ? `&model=${encodeURIComponent(model)}` : ""}`,
  );
  return (
    <>
      <div className="eyebrow">연구 / 확률 검증</div>
      <h1>확률이 높을수록 결과도 좋아질까?</h1>
      <p>관망을 포함한 모든 판단과 이후 수익률을 비교합니다.</p>
      <div className="toolbar" style={{ flexWrap: "wrap" }}>
        <select
          aria-label="실험 선택"
          value={run}
          onChange={(e) => setRun(e.target.value)}
        >
          <option value="">현재 실험</option>
          {runs?.runs.map((r) => (
            <option key={r.id}>{r.id}</option>
          ))}
        </select>
        <select
          aria-label="판단 방향"
          value={direction}
          onChange={(e) => setDirection(e.target.value)}
        >
          <option value="long">매수 셋업</option>
          <option value="short">공매도 셋업</option>
        </select>
        <select
          aria-label="검증 기준"
          value={axis}
          onChange={(e) => setAxis(e.target.value)}
        >
          <option value="probability">셋업 확률</option>
          <option value="confidence">셋업 점수의 확신도</option>
        </select>
        <select
          aria-label="수익률 관찰 기간"
          value={horizon}
          onChange={(e) => setHorizon(e.target.value)}
        >
          {[
            ["1", "1분"],
            ["3", "3분"],
            ["5", "5분"],
            ["15", "15분"],
            ["60", "1시간"],
            ["240", "4시간"],
            ["1440", "24시간"],
          ].map(([v, l]) => (
            <option key={v} value={v}>
              {l} 이후
            </option>
          ))}
        </select>
        <select
          aria-label="시장 국면"
          value={regime}
          onChange={(e) => setRegime(e.target.value)}
        >
          <option value="">모든 시장 국면</option>
          {[
            "TRENDING_UP",
            "TRENDING_DOWN",
            "RANGING",
            "HIGH_VOLATILITY",
            "LOW_VOLATILITY",
          ].map((r) => (
            <option key={r}>{t(r)}</option>
          ))}
        </select>
        <input
          aria-label="모델 버전 필터"
          placeholder="모델 버전 (전체)"
          value={model}
          onChange={(e) => setModel(e.target.value)}
          style={{
            background: "#252c27",
            border: "1px solid #48513e",
            borderRadius: 5,
            padding: 10,
          }}
        />
      </div>
      {error && <p className="error">{error}</p>}
      <div className="stats">
        {[
          ["기간이 지난 관측 수", String(d?.total_samples ?? 0)],
          ["관측 일수", String(d?.observed_days ?? 0)],
          ["확률·수익률 상관계수", d?.correlation?.toFixed(3) ?? "—"],
          ["표시 구간 미만 관측 수", String(d?.below_bucket_range ?? 0)],
        ].map(([label, value]) => (
          <div key={label} className="stat">
            <label>{label}</label>
            <strong>{value}</strong>
          </div>
        ))}
      </div>
      <div className="panel">
        <h2>
          확률 구간별 수익률 ·{" "}
          {direction === "short" ? "하락 방향 수익률" : "자산 수익률"}
        </h2>
        {d?.total_samples ? (
          <div className="chart">
            <ResponsiveContainer width="100%" height="100%">
              <LineChart
                data={d.buckets.map((b) => ({
                  ...b,
                  label: `${b.low.toFixed(1)}–${b.high.toFixed(1)}`,
                }))}
              >
                <CartesianGrid stroke="#2a3130" vertical={false} />
                <XAxis dataKey="label" stroke="#829084" />
                <YAxis tickFormatter={(n) => pct(Number(n))} stroke="#829084" />
                <Tooltip
                  contentStyle={{
                    background: "#18201a",
                    border: "1px solid #3c4c36",
                  }}
                  formatter={(n) => pct(Number(n))}
                />
                <Line
                  dataKey="mean_return"
                  name="평균 이후 수익률"
                  stroke="#b8e891"
                  strokeWidth={2}
                  connectNulls={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <div className="empty">
            선택한 조건의 후속 관측이 없습니다.
            <br />
            관찰 기간이 지난 뒤 수익률이 표시됩니다.
          </div>
        )}
      </div>
      <div className="panel">
        <table>
          <thead>
            <tr>
              {[
                "확률 구간",
                "표본 수",
                "평균 수익률",
                "중앙 수익률",
                "상승 비율",
                "표준편차",
              ].map((h) => (
                <th key={h}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {d?.buckets.map((b) => (
              <tr key={b.low}>
                <td className="mono">
                  {b.low.toFixed(2)}–{b.high.toFixed(2)}
                </td>
                <td>{b.sample_count}</td>
                <td>{pct(b.mean_return)}</td>
                <td>{pct(b.median_return)}</td>
                <td>{pct(b.win_rate)}</td>
                <td>{pct(b.standard_deviation)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="notice">
        {d?.note}
        <br />일 단위 블록 부트스트랩 95% 평균 수익률 구간:{" "}
        {d?.mean_return_daily_block_ci95?.map(pct).join(" ~ ") ??
          "최소 10일의 관측 필요"}
        . 모델: {d?.models.join(", ") || "정상 응답 없음"}.
      </div>
    </>
  );
}
