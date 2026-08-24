const supabaseUrl = Deno.env.get("SUPABASE_URL") ?? "";
const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ??
  Deno.env.get("SUPABASE_SERVICE_KEY") ?? "";

Deno.serve(async (request: Request): Promise<Response> => {
  if (request.method !== "GET") return page("Method not allowed", 405);
  const query = new URL(request.url).searchParams;
  const requestId = query.get("req") ?? "";
  if (!/^[0-9a-f-]{36}$/i.test(requestId) || query.get("go") !== "1") {
    return page("Invalid follow-up link", 400);
  }
  if (!supabaseUrl || !serviceKey) return page("Service unavailable", 503);
  const databaseHeaders = {
    apikey: serviceKey,
    Authorization: `Bearer ${serviceKey}`,
    "Content-Type": "application/json",
    Prefer: "return=representation",
  };
  const consentResponse = await fetch(
    `${supabaseUrl}/rest/v1/followup_requests?id=eq.${encodeURIComponent(requestId)}&status=eq.offered`,
    {
      method: "PATCH",
      headers: databaseHeaders,
      body: JSON.stringify({ status: "consented" }),
    },
  );
  if (!consentResponse.ok) return page("Could not record consent", 500);
  const updated = await consentResponse.json();
  if (!Array.isArray(updated) || updated.length === 0) {
    return page("This follow-up link has already been used or is invalid", 404);
  }

  const repository = Deno.env.get("GH_REPO") ?? "";
  const githubToken = Deno.env.get("GH_PAT") ?? "";
  if (!/^[^/]+\/[^/]+$/.test(repository) || !githubToken) {
    return page("Follow-up dispatch is not configured", 503);
  }
  const [owner, repo] = repository.split("/");
  const dispatchResponse = await fetch(
    `https://api.github.com/repos/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/actions/workflows/followup.yml/dispatches`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${githubToken}`,
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
        "User-Agent": "daily-news-briefing",
      },
      body: JSON.stringify({ ref: "main", inputs: { request_id: requestId } }),
    },
  );
  if (!dispatchResponse.ok) return page("Could not start the deep dive", 502);
  return page("On it — your deep dive lands in ~15 minutes.");
});

function page(content: string, status = 200): Response {
  return new Response(
    `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Daily News Briefing</title></head><body style="font-family:system-ui,sans-serif;max-width:520px;margin:60px auto;padding:0 20px;color:#202020"><h1>${content}</h1></body></html>`,
    { status, headers: { "content-type": "text/html; charset=utf-8" } },
  );
}
