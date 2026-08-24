const supabaseUrl = Deno.env.get("SUPABASE_URL") ?? "";
const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ??
  Deno.env.get("SUPABASE_SERVICE_KEY") ?? "";

const headers = {
  apikey: serviceKey,
  Authorization: `Bearer ${serviceKey}`,
  "Content-Type": "application/json",
};

Deno.serve(async (request: Request): Promise<Response> => {
  if (request.method !== "GET") return page("Method not allowed", 405);
  if (!supabaseUrl || !serviceKey) return page("Service unavailable", 503);

  const query = new URL(request.url).searchParams;
  const token = query.get("t") ?? "";
  const kind = query.get("kind") ?? "";
  const item = query.get("item") ?? "";
  const score = Number(query.get("s"));
  const date = query.get("d") ?? "";
  if (
    !token || !["topic", "article"].includes(kind) || !item ||
    !Number.isInteger(score) || score < 1 || score > 5 ||
    !/^\d{4}-\d{2}-\d{2}$/.test(date)
  ) {
    return page("Invalid rating link", 400);
  }

  const recipientResponse = await fetch(
    `${supabaseUrl}/rest/v1/recipients?select=id&token=eq.${encodeURIComponent(token)}&active=eq.true&limit=1`,
    { headers },
  );
  if (!recipientResponse.ok) return page("Service unavailable", 503);
  const recipients = await recipientResponse.json();
  if (!Array.isArray(recipients) || recipients.length === 0) {
    return page("Invalid rating link", 403);
  }
  const recipient = recipients[0].id;
  const ratingResponse = await fetch(
    `${supabaseUrl}/rest/v1/ratings?on_conflict=recipient,kind,item,date`,
    {
      method: "POST",
      headers: {
        ...headers,
        Prefer: "resolution=merge-duplicates,return=minimal",
      },
      body: JSON.stringify({
        recipient,
        kind,
        item,
        score,
        date,
        rated_at: new Date().toISOString(),
      }),
    },
  );
  if (!ratingResponse.ok) return page("Could not record rating", 500);

  let followup = "";
  if (kind === "article" && score >= 4) {
    const id = crypto.randomUUID();
    const offerResponse = await fetch(`${supabaseUrl}/rest/v1/followup_requests`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        id,
        recipient,
        article_id: item,
        status: "offered",
        created_at: new Date().toISOString(),
      }),
    });
    if (offerResponse.ok) {
      followup = `<p><a href="/functions/v1/followup?req=${encodeURIComponent(id)}&go=1">Yes — send me a deep dive</a></p><p>No thanks</p>`;
    }
  }
  return page(`Thanks — recorded ${"⭐".repeat(score)}${followup}`);
});

function page(content: string, status = 200): Response {
  return new Response(
    `<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Daily News Briefing</title></head><body style="font-family:system-ui,sans-serif;max-width:520px;margin:60px auto;padding:0 20px;color:#202020"><h1>${content}</h1></body></html>`,
    { status, headers: { "content-type": "text/html; charset=utf-8" } },
  );
}
