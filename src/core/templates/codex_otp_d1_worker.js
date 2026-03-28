// codex-otp-d1-worker template version: 2026-03-29.1

function normalizeText(raw) {
  return String(raw || "").replace(/=\r?\n/g, "").replace(/=3D/g, "=");
}

function otpRegex() {
  return /(?<!\d)(\d{6})(?!\d)/g;
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

export default {
  async email(message, env) {
    try {
      const toAddress = String(message.to || "").trim().toLowerCase();
      if (!toAddress) {
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
        "INSERT INTO codes (email, code, stage, source, subject) VALUES (?, ?, ?, ?, ?)"
      ).bind(
        toAddress,
        code,
        stage,
        "email_worker_d1",
        subject
      ).run();
    } catch (error) {
      console.error("[codex-otp-d1] email handler crashed:", error);
    }
  },
};
