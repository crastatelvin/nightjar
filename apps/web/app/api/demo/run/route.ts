const SCENARIOS = new Set([
  "site_cache_poisoned",
  "site_bad_deploy",
  "site_db_down",
  "credential_leaked",
  "phishing_report",
]);

const LAB_URL = (process.env.NIGHTJAR_LAB_URL ?? "http://127.0.0.1:8100").replace(/\/$/, "");
const API_URL = (process.env.NIGHTJAR_API_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
const API_TOKEN = process.env.NIGHTJAR_API_TOKEN ?? "";

interface RunBody {
  scenario?: unknown;
  variant?: unknown;
  injection?: unknown;
  relapse?: unknown;
  severity?: unknown;
}

async function readJson(response: Response): Promise<Record<string, unknown>> {
  try {
    return (await response.json()) as Record<string, unknown>;
  } catch {
    return {};
  }
}

export async function POST(request: Request) {
  let body: RunBody;
  try {
    body = (await request.json()) as RunBody;
  } catch {
    return Response.json({ error: "invalid JSON body" }, { status: 400 });
  }

  const scenario = typeof body.scenario === "string" ? body.scenario : "";
  if (!SCENARIOS.has(scenario)) {
    return Response.json({ error: `unknown scenario: ${scenario}` }, { status: 400 });
  }

  const variant = Math.max(0, Math.min(5, Number(body.variant) || 0));
  const injection = body.injection === true;
  const relapse = body.relapse === true;
  const severity =
    typeof body.severity === "string" &&
    ["critical", "high", "medium", "low"].includes(body.severity)
      ? body.severity
      : undefined;

  const labParams = new URLSearchParams({
    variant: String(variant),
    injection: String(injection),
    relapse: String(relapse),
  });

  let seed: Record<string, unknown>;
  try {
    const labResponse = await fetch(
      `${LAB_URL}/lab/scenario/${encodeURIComponent(scenario)}?${labParams.toString()}`,
      { method: "POST", cache: "no-store" },
    );
    seed = await readJson(labResponse);
    if (!labResponse.ok) {
      return Response.json(
        {
          error:
            typeof seed.detail === "string"
              ? seed.detail
              : `lab refused the scenario (${labResponse.status})`,
        },
        { status: 502 },
      );
    }
  } catch (error) {
    return Response.json(
      {
        error: `cannot reach the lab at ${LAB_URL}: ${
          error instanceof Error ? error.message : "connection failed"
        }`,
      },
      { status: 502 },
    );
  }

  const incidentBody = {
    title: seed.title,
    description: seed.description,
    incident_class: seed.incident_class,
    severity: severity ?? seed.severity,
    source: "ui",
    metadata: {
      ...(typeof seed.metadata === "object" && seed.metadata !== null
        ? (seed.metadata as Record<string, unknown>)
        : {}),
      nightjar_scenario: scenario,
      nightjar_variant: variant,
      nightjar_injection: injection,
      nightjar_relapse: relapse,
    },
  };

  try {
    const apiResponse = await fetch(`${API_URL}/v1/incidents?wait=false`, {
      method: "POST",
      cache: "no-store",
      headers: {
        "content-type": "application/json",
        ...(API_TOKEN ? { authorization: `Bearer ${API_TOKEN}` } : {}),
      },
      body: JSON.stringify(incidentBody),
    });
    const incident = await readJson(apiResponse);
    if (!apiResponse.ok) {
      return Response.json(
        {
          error:
            typeof incident.detail === "string"
              ? incident.detail
              : `API rejected the incident (${apiResponse.status})`,
        },
        { status: 502 },
      );
    }

    let audit: Record<string, unknown> = { healthy: false, checks: {} };
    try {
      const auditResponse = await fetch(`${LAB_URL}/lab/audit`, { cache: "no-store" });
      if (auditResponse.ok) audit = await readJson(auditResponse);
    } catch {
      // The incident exists; the studio can poll for audit separately.
    }

    return Response.json({
      incident,
      audit,
      scenario,
      variant,
      injection,
      relapse,
      labUrl: LAB_URL,
    });
  } catch (error) {
    return Response.json(
      {
        error: `cannot reach the API at ${API_URL}: ${
          error instanceof Error ? error.message : "connection failed"
        }`,
      },
      { status: 502 },
    );
  }
}
