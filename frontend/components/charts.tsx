"use client";
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
export type EquityPoint = {
  timestamp: string;
  buy_hold: number;
  ema: number;
  rsi: number;
  jev: number;
};
export function EquityChart({ points }: { points: EquityPoint[] }) {
  return (
    <div className="chart">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={points}>
          <CartesianGrid stroke="#2a3130" vertical={false} />
          <XAxis
            dataKey="timestamp"
            tickFormatter={(s) =>
              new Date(s).toLocaleTimeString([], {
                hour: "2-digit",
                minute: "2-digit",
              })
            }
            stroke="#829084"
            tick={{ fontSize: 10 }}
            minTickGap={80}
          />
          <YAxis
            domain={["auto", "auto"]}
            stroke="#829084"
            tick={{ fontSize: 10 }}
            tickFormatter={(n) => `${Number(n).toLocaleString("ko-KR")}원`}
            width={65}
          />
          <Tooltip
            contentStyle={{
              background: "#18201a",
              border: "1px solid #3c4c36",
              borderRadius: 6,
            }}
            labelFormatter={(s) => new Date(String(s)).toLocaleString()}
            formatter={(n) =>
              `${Number(n).toLocaleString("ko-KR", { maximumFractionDigits: 0 })}원`
            }
          />
          <Legend wrapperStyle={{ fontSize: 11, paddingTop: 18 }} />
          {[
            ["jev", "Jev 전략", "#b8e891"],
            ["buy_hold", "매수 후 보유", "#929dd8"],
            ["ema", "EMA 교차", "#deb980"],
            ["rsi", "RSI 전략", "#70b8b0"],
          ].map(([key, label, color]) => (
            <Line
              key={key}
              type="stepAfter"
              dataKey={key}
              name={label}
              stroke={color}
              strokeWidth={key === "jev" ? 2.5 : 1.5}
              dot={false}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
