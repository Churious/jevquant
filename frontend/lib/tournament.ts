export type Standing = {
  trader_id: string;
  name: string;
  type: string;
  rank: number;
  starting_capital: number;
  current_equity: number;
  cash: number;
  net_pnl: number;
  return_pct: number;
  today_pnl: number;
  realized_pnl: number;
  unrealized_pnl: number;
  max_drawdown: number;
  win_rate: number | null;
  profit_factor: number | null;
  trades: number;
  open_positions: number;
  current_exposure: number;
  crypto_exposure: number;
  stock_exposure: number;
  time_in_market: number;
  average_exposure: number;
  max_exposure: number;
  turnover: number;
  fees_paid: number;
  risk_adjusted_score: number | null;
  risk_halted: boolean;
};
export type Comparison = {
  best_jev: Standing | null;
  worst_jev: Standing | null;
  best_baseline: Standing | null;
  jev_vs_best_baseline_pp: number | null;
  average_jev_return: number | null;
  median_jev_return: number | null;
};
export type Participant = {
  trader_id: string;
  name: string;
  kind: string;
  runtime: { provider: string; model: string } | null;
  definition: Record<string, unknown>;
  configuration: Record<string, unknown>;
  questions: Record<string, unknown>;
  configuration_hash: string;
};
export type Tournament = {
  id: string;
  name: string;
  status: string;
  started_at: string | null;
  ends_at: string | null;
  duration_days: number;
  day: number;
  remaining_seconds: number | null;
  market_universe: string[];
  starting_capital: number;
  configuration_hash: string;
  participants: Participant[];
  leaderboard: Standing[];
  comparison: Comparison;
  market_status: Record<string, string>;
  decision_budget: {
    jobs: number;
    pending: number;
    concurrency: number;
    deadline_misses: number;
    completed_before_deadline: number;
    per_trader: Record<
      string,
      {
        jobs: number;
        ok: number;
        deadline_misses: number;
        errors: number;
        average_latency_ms: number | null;
        average_queue_wait_ms: number | null;
      }
    >;
  };
  final_report:
    | (Comparison & {
        ranking: Standing[];
        winner: Standing | null;
        joint_winners: Standing[];
        fees_by_trader: Record<string, number>;
        risk_adjusted_ranking: Standing[];
        valuation_prices: Record<
          string,
          { price: number; observed_at: string; stale: boolean }
        >;
      })
    | null;
};
export type CurvePoint = {
  timestamp: string;
  equity: Record<string, number>;
  returns: Record<string, number>;
};
export type Position = {
  id: number;
  trader_id: string;
  symbol: string;
  side: string;
  entry_price: number;
  current_price: number;
  quantity: number;
  pnl: number;
  stop_loss: number;
  take_profit: number;
  timestamp: string;
};
export type Decision = {
  id: number;
  decision_id: number | null;
  timestamp: string;
  symbol: string;
  action: string;
  status: string;
  model_status: string | null;
  confidence: number | null;
  decision_latency_ms: number | null;
  model_version: string | null;
  reason: string;
};
export const statusName = (s: string) =>
  (
    ({
      PENDING: "시작 대기",
      RUNNING: "진행 중",
      PAUSED: "일시정지",
      COMPLETED: "종료",
    }) as Record<string, string>
  )[s] ?? s;
export const signed = (n: number | null | undefined, suffix = "") =>
  n == null ? "—" : `${n >= 0 ? "+" : ""}${n.toFixed(2)}${suffix}`;
