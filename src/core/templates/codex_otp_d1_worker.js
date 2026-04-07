// codex-otp-d1-worker template version: 2026-03-29.5

function normalizeText(raw) {
  return String(raw || "").replace(/=\r?\n/g, "").replace(/=3D/g, "=");
}

function canonicalizeEmail(email) {
  return String(email || "").trim().toLowerCase();
}

function otpRegex() {
  return /(?<!\d)(\d{6})(?!\d)/g;
}

function extractCode(cleaned) {
  const semanticPatterns = [
    /verification code[^\d]{0,20}(\d{6})/i,
    /code[^\d]{0,20}(\d{6})/i,
    /one[-\s]?time[^\d]{0,20}(\d{6})/i,
    /otp[^\d]{0,20}(\d{6})/i,
    /\b(\d{6})\b(?=[^\n]{0,80}(verification|login|sign in|openai|chatgpt))/i,
  ];

  for (const pattern of semanticPatterns) {
    const match = cleaned.match(pattern);
    if (match && match[1] && match[1] !== "177010" && match[1] !== "000000") {
      return match[1];
    }
  }

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

function decodeBase64Chunks(cleaned) {
  const chunks = cleaned.match(/[A-Za-z0-9+/=]{80,}/g) || [];
  const decoded = [];
  for (const chunk of chunks.slice(0, 10)) {
    try {
      const text = atob(chunk.replace(/\s+/g, ""));
      if (text && /\d{6}/.test(text)) {
        decoded.push(text);
      }
    } catch (_) {
    }
  }
  return decoded.join("\n");
}

function buildCandidateTexts(rawEmail) {
  const cleaned = normalizeText(rawEmail);
  const htmlStripped = cleaned.replace(/<[^>]+>/g, " ");
  const decodedBase64 = decodeBase64Chunks(cleaned);
  return [cleaned, htmlStripped, decodedBase64].filter(Boolean);
}

async function logEvent(env, email, eventType, subject = "", detail = "") {
  try {
    await env.DB.prepare(
      "INSERT INTO mail_events (email, event_type, subject, detail) VALUES (?, ?, ?, ?)"
    ).bind(email, eventType, subject, String(detail || "").slice(0, 1000)).run();
  } catch (error) {
    console.error("[codex-otp-d1] mail_events insert failed:", error);
  }
}

export default {
  async email(message, env) {
    try {
      const originalToAddress = String(message.to || "").trim();
      const toAddress = canonicalizeEmail(originalToAddress);
      if (!toAddress) {
        return;
      }

      const rawEmail = await new Response(message.raw).text();
      const candidates = buildCandidateTexts(rawEmail);
      const primaryText = candidates.join("\n\n---\n\n");
      const code = extractCode(primaryText);
      const stage = detectStage(primaryText);
      const subjectMatch = primaryText.match(/Subject:\s*(.*)/i);
      const subject = subjectMatch ? String(subjectMatch[1] || "").trim() : "";

      console.log(`[收到邮件] to=${toAddress} original_to=${originalToAddress} from=${String(message.from || "")} subject=${subject}`);
      await logEvent(env, toAddress, "received", subject, `from=${String(message.from || "")};original_to=${originalToAddress}`);

      if (!code) {
        console.log(`[特殊拦截] 未找到验证码 | 地址: ${toAddress} | 标题: ${subject}`);
        await logEvent(env, toAddress, "code_missing", subject, primaryText.slice(0, 800));
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
      console.log(`[成功] 验证码 ${code} 已写入 D1 -> ${toAddress} | stage=${stage} | 标题: ${subject}`);
      await logEvent(env, toAddress, "code_stored", subject, `stage=${stage};code=${code}`);
    } catch (error) {
      console.error(`[异常] D1 邮件处理失败: ${error?.message || error}`);
      try {
        const toAddress = String(message?.to || "").trim().toLowerCase();
        await logEvent(env, toAddress || "unknown", "insert_failed", "", String(error?.message || error));
      } catch (_) {
      }
    }
  },
};
