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
]);
async function forward(
  req: NextRequest,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  if (
    !allowed.has(path[0]) ||
    path.length > 2 ||
    (path.length === 2 && (path[0] !== "decisions" || !/^\d+$/.test(path[1])))
  )
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
