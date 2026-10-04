"use client";
import { useApi } from "@/lib/api";
import { Participant } from "@/lib/tournament";
export function TraderFilter({
  value,
  onChange,
  run = "",
}: {
  value: string;
  onChange: (id: string) => void;
  run?: string;
}) {
  const { data } = useApi<Participant[]>(
    `/api/traders${run ? `?tournament_id=${encodeURIComponent(run)}` : ""}`,
  );
  return (
    <select
      aria-label="연구 참가자"
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">모든 Jev 참가자</option>
      {data
        ?.filter((p) => p.kind === "jev")
        .map((p) => (
          <option key={p.trader_id} value={p.trader_id}>
            {p.name}
          </option>
        ))}
      {!data && <option value="jev">이전 단일 Jev 전략</option>}
    </select>
  );
}
