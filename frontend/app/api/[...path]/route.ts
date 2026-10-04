import { NextRequest } from "next/server";
const allowed = new Set([
  "overview",
  "market",
  "equity",
  "positions",
  "trades",
  "decisions",
  "calibration",
  "evaluation",
  "tournament",
  "traders",
]);
async function forward(
  req: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  const tournament =
    path[0] === "tournament" &&
    path.length >= 2 &&
    path.length <= 3 &&
    /^[a-zA-Z0-9_-]{1,64}$/.test(path[1]) &&
    (path.length === 2 || ["leaderboard", "equity"].includes(path[2]));
  const trader =
    path[0] === "traders" &&
    path.length <= 3 &&
    (path.length === 1 || /^[a-z0-9-]{1,32}$/.test(path[1])) &&
    (path.length < 3 ||
      ["portfolio", "positions", "trades", "decisions"].includes(path[2]));
  const legacy =
    !["tournament", "traders"].includes(path[0]) &&
    allowed.has(path[0]) &&
    (path.length === 1 ||
      (path.length === 2 && path[0] === "decisions" && /^\d+$/.test(path[1])));
  if (!(tournament || trader || legacy))
    return new Response("Not found", { status: 404 });
  try {
    const response = await fetch(
      `${process.env.BACKEND_URL ?? "http://localhost:8000"}/api/${path.join("/")}${req.nextUrl.search}`,
      {
        method: req.method,
        headers: {
          "Content-Type": "application/json",
          Authorization: req.headers.get("authorization") ?? "",
        },
        cache: "no-store",
        signal: AbortSignal.timeout(15000),
      },
    );
    return new Response(await response.text(), {
      status: response.status,
      headers: {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
      },
    });
  } catch {
    return Response.json({ error: "Backend unavailable" }, { status: 503 });
  }
}
export const GET = forward;
