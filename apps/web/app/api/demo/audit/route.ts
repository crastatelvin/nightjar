const LAB_URL = (process.env.NIGHTJAR_LAB_URL ?? "http://127.0.0.1:8100").replace(/\/$/, "");

export async function GET() {
  try {
    const response = await fetch(`${LAB_URL}/lab/audit`, { cache: "no-store" });
    const body = (await response.json()) as Record<string, unknown>;
    return Response.json(body, { status: response.status });
  } catch (error) {
    return Response.json(
      {
        healthy: false,
        checks: {},
        error: `cannot reach the lab at ${LAB_URL}: ${
          error instanceof Error ? error.message : "connection failed"
        }`,
      },
      { status: 502 },
    );
  }
}
