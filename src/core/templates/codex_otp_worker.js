// codex-otp-worker template version: 2026-03-28.1

const json = (data, init = {}) => new Response(JSON.stringify(data, null, 2), {
  ...init,
  headers: {
    "content-type": "application/json; charset=utf-8",
    ...(init.headers || {}),
  },
});

function unauthorized() {
  return json({ success: false, error: "unauthorized" }, { status: 401 });
}

function notFound() {
  return json({ success: false, error: "not_found" }, { status: 404 });
}

function isAdmin(request, env) {
  const admin = request.headers.get("x-admin-auth") || "";
  const custom = request.headers.get("x-custom-auth") || "";
  if (!env.ADMIN_TOKEN || admin !== env.ADMIN_TOKEN) {
    return false;
  }
  if (env.CUSTOM_AUTH && custom !== env.CUSTOM_AUTH) {
    return false;
  }
  return true;
}

function corsHeaders() {
  return {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET,POST,OPTIONS",
    "access-control-allow-headers": "content-type,x-admin-auth,x-custom-auth",
  };
}

function otpRegex() {
  return /(?<!\d)(\d{6})(?!\d)/g;
}

function normalizeText(raw) {
  return String(raw || "").replace(/=\r?\n/g, "").replace(/=3D/g, "=");
}

function extractCode(cleaned) {
  const hits = [];
  let match;
  const regex = otpRegex();
  while ((match = regex.exec(cleaned)) !== null) {
    hits.push(match[1]);
  }
  const excluded = new Set(["177010", "000000"]);
  for (const code of hits) {
    if (!excluded.has(code)) {
      return code;
    }
  }
  return "";
}

function detectStage(cleaned) {
  const lower = String(cleaned || "").toLowerCase();
  if (lower.includes("log in") || lower.includes("login") || lower.includes("sign in")) {
    return "login";
  }
  return "register";
}

function buildEmail(localPart, domain) {
  return `${localPart}@${domain}`.toLowerCase();
}

function randomLocalPart() {
  const alphabet = "abcdefghijklmnopqrstuvwxyz0123456789";
  let text = "";
  const length = 10 + Math.floor(Math.random() * 4);
  for (let i = 0; i < length; i += 1) {
    text += alphabet[Math.floor(Math.random() * alphabet.length)];
  }
  return text;
}

async function createAddress(env, body) {
  const domain = String(body.domain || env.DEFAULT_EMAIL_DOMAIN || "").trim().toLowerCase();
  if (!domain) {
    return json({ success: false, error: "missing_domain" }, { status: 400 });
  }

  let localPart = randomLocalPart();
  let email = buildEmail(localPart, domain);
  let retry = 0;
  while (retry < 5) {
    const exists = await env.DB.prepare("SELECT email FROM addresses WHERE email = ?").bind(email).first();
    if (!exists) {
      break;
    }
    localPart = randomLocalPart();
    email = buildEmail(localPart, domain);
    retry += 1;
  }

  const ttl = Math.max(300, Number(body.ttl_seconds || env.DEFAULT_TTL_SECONDS || 1800));
  const expiresAt = new Date(Date.now() + ttl * 1000).toISOString();
  const metadata = JSON.stringify({ tags: body.tags || [], source: "codex_otp" });

  await env.DB.prepare(
    "INSERT INTO addresses (email, local_part, domain, status, expires_at, metadata) VALUES (?, ?, ?, 'active', ?, ?)"
  ).bind(email, localPart, domain, expiresAt, metadata).run();

  return json({
    success: true,
    email,
    domain,
    created_at: new Date().toISOString(),
    expires_at: expiresAt,
  });
}

async function latestCode(env, body) {
  const email = String(body.email || "").trim().toLowerCase();
  if (!email) {
    return json({ success: false, error: "missing_email" }, { status: 400 });
  }

  const ignoreCodes = new Set(Array.isArray(body.ignore_codes) ? body.ignore_codes.map((item) => String(item)) : []);
  const rows = await env.DB.prepare(
    "SELECT id, code, stage, source, subject, received_at FROM codes WHERE email = ? AND consumed = 0 ORDER BY received_at DESC LIMIT 20"
  ).bind(email).all();
  const results = rows?.results || [];
  const stage = String(body.stage || "").trim();
  const match = results.find((row) => {
    if (stage && String(row.stage || "") !== stage) {
      return false;
    }
    return !ignoreCodes.has(String(row.code || ""));
  });
  const latest = results[0] || null;

  return json({
    success: true,
    found: !!match,
    id: match?.id || null,
    code: match?.code || null,
    latest_code: latest?.code || null,
    received_at: match?.received_at || null,
  });
}

async function consumeCode(env, body) {
  const id = Number(body.id || 0);
  if (!id) {
    return json({ success: false, error: "missing_id" }, { status: 400 });
  }
  await env.DB.prepare(
    "UPDATE codes SET consumed = 1, consumed_at = CURRENT_TIMESTAMP WHERE id = ?"
  ).bind(id).run();
  return json({ success: true });
}

async function deactivateAddress(env, body) {
  const email = String(body.email || "").trim().toLowerCase();
  if (!email) {
    return json({ success: false, error: "missing_email" }, { status: 400 });
  }
  await env.DB.prepare(
    "UPDATE addresses SET status = 'disabled' WHERE email = ?"
  ).bind(email).run();
  return json({ success: true });
}

async function cleanup(env) {
  await env.DB.prepare(
    "DELETE FROM codes WHERE datetime(received_at) < datetime('now', '-' || ? || ' day')"
  ).bind(String(env.CODE_RETENTION_DAYS || "2")).run();
  await env.DB.prepare(
    "DELETE FROM addresses WHERE expires_at IS NOT NULL AND datetime(expires_at) < datetime('now', '-1 day')"
  ).run();
  return json({ success: true });
}

async function health(env) {
  const count = await env.DB.prepare("SELECT count(*) AS count FROM addresses").first();
  return json({ success: true, ok: true, status: "ok", address_count: Number(count?.count || 0) });
}

async function handleApi(request, env) {
  const url = new URL(request.url);
  if (request.method === "OPTIONS") {
    return new Response(null, { headers: corsHeaders() });
  }
  if (!isAdmin(request, env)) {
    return unauthorized();
  }
  const path = url.pathname;
  const body = request.method === "GET" ? {} : await request.json().catch(() => ({}));

  if (path === "/admin/v1/health" && request.method === "GET") {
    return health(env);
  }
  if (path === "/admin/v1/new_address" && request.method === "POST") {
    return createAddress(env, body);
  }
  if (path === "/admin/v1/code/latest" && request.method === "POST") {
    return latestCode(env, body);
  }
  if (path === "/admin/v1/code/consume" && request.method === "POST") {
    return consumeCode(env, body);
  }
  if (path === "/admin/v1/address/deactivate" && request.method === "POST") {
    return deactivateAddress(env, body);
  }
  if (path === "/admin/v1/cleanup" && request.method === "POST") {
    return cleanup(env);
  }
  return notFound();
}

export default {
  async fetch(request, env) {
    const response = await handleApi(request, env);
    const headers = new Headers(response.headers);
    for (const [key, value] of Object.entries(corsHeaders())) {
      headers.set(key, value);
    }
    return new Response(response.body, {
      status: response.status,
      headers,
    });
  },

  async email(message, env) {
    try {
      const toAddress = String(message.to || "").trim().toLowerCase();
      if (!toAddress) {
        return;
      }

      const addr = await env.DB.prepare(
        "SELECT email, status FROM addresses WHERE email = ?"
      ).bind(toAddress).first();
      if (!addr || String(addr.status || "") !== "active") {
        return;
      }

      const rawEmail = await new Response(message.raw).text();
      const cleaned = normalizeText(rawEmail);
      const code = extractCode(cleaned);
      const stage = detectStage(cleaned);
      const subjectMatch = cleaned.match(/Subject:\s*(.*)/i);
      const subject = subjectMatch ? String(subjectMatch[1] || "").trim() : "";

      if (!code) {
        return;
      }

      await env.DB.prepare(
        "INSERT INTO codes (email, code, stage, source, subject, metadata) VALUES (?, ?, ?, ?, ?, ?)"
      ).bind(
        toAddress,
        code,
        stage,
        "email_worker",
        subject,
        JSON.stringify({ message_from: String(message.from || "") })
      ).run();

      await env.DB.prepare(
        "UPDATE addresses SET last_code_at = CURRENT_TIMESTAMP WHERE email = ?"
      ).bind(toAddress).run();
    } catch (error) {
      console.error("[codex-otp] email handler crashed:", error);
    }
  },
};
