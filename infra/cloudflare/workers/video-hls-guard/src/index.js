/**
 * PATH: C:\academy\infra\cloudflare\workers\video-hls-guard\src\index.js
 *
 * Cloudflare Worker - HLS Signed Query Guard
 * Query params:
 *   exp: unix timestamp (seconds)
 *   sig: base64url(HMAC_SHA256(secret, `${path}|${exp}|${kid}|${uid}`))
 *   kid: key id (string, optional but recommended)
 *   uid: user id (optional)
 *
 * Behavior:
 * - Only protects HLS paths (default: /media/hls/ or /hls/)
 * - If missing secret -> allow all (fail-open for staged rollout)
 * - If invalid -> 403
 */

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // 1) Protect only HLS paths (keep original behavior for others)
    const pathname = url.pathname || "/";
    const isHls =
      pathname.startsWith("/media/hls/") ||
      pathname.startsWith("/hls/");

    if (!isHls) {
      return fetch(request);
    }

    // 2) Secret 없으면 fail-open (배포/전환 안전장치)
    const secret = (env.CDN_HLS_SIGNING_SECRET || "").trim();
    if (!secret) {
      return fetch(request);
    }

    // 3) Query params
    const expStr = url.searchParams.get("exp") || "";
    const sig = url.searchParams.get("sig") || "";
    const kid = url.searchParams.get("kid") || "v1";
    const uid = url.searchParams.get("uid") || ""; // optional

    // Required
    if (!expStr || !sig) {
      return new Response("forbidden", { status: 403 });
    }

    const exp = parseInt(expStr, 10);
    if (!Number.isFinite(exp) || exp <= 0) {
      return new Response("forbidden", { status: 403 });
    }

    // 4) Expiry check (seconds)
    const now = Math.floor(Date.now() / 1000);
    if (exp <= now) {
      return new Response("expired", { status: 403 });
    }

    // 5) Build message: `${path}|${exp}|${kid}|${uid}`
    // IMPORTANT: path must be exactly the request pathname, starting with '/'
    const msg = `${pathname}|${exp}|${kid}|${uid}`;

    const ok = await verifySigHmacSha256Base64Url({
      secret,
      message: msg,
      sigBase64Url: sig,
    });

    if (!ok) {
      return new Response("forbidden", { status: 403 });
    }

    // 6) Option: strip signing query from upstream cache key (recommended)
    // - If you keep as-is, cache will vary by sig/exp (cache fragmentation)
    // - We will remove exp/sig/kid/uid before fetching origin, but keep other params.
    const cleanUrl = new URL(url.toString());
    cleanUrl.searchParams.delete("exp");
    cleanUrl.searchParams.delete("sig");
    cleanUrl.searchParams.delete("kid");
    cleanUrl.searchParams.delete("uid");

    // Forward request with cleaned URL
    const newReq = new Request(cleanUrl.toString(), request);

    // (Optional) You can set Cache-Control headers here; keep minimal.
    return fetch(newReq);
  },
};

/**
 * Verify:
 *   sig == base64url(HMAC_SHA256(secret, message))
 */
async function verifySigHmacSha256Base64Url({ secret, message, sigBase64Url }) {
  try {
    const key = await crypto.subtle.importKey(
      "raw",
      new TextEncoder().encode(secret),
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["sign"]
    );

    const mac = await crypto.subtle.sign(
      "HMAC",
      key,
      new TextEncoder().encode(message)
    );

    const computed = base64UrlEncode(new Uint8Array(mac));
    return timingSafeEqualStr(computed, sigBase64Url);
  } catch (e) {
    return false;
  }
}

function base64UrlEncode(bytes) {
  // Convert bytes -> binary string -> base64 -> base64url
  let binary = "";
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i]);
  const b64 = btoa(binary);
  return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function timingSafeEqualStr(a, b) {
  // Simple timing-safe-ish compare (length check + XOR)
  if (typeof a !== "string" || typeof b !== "string") return false;
  if (a.length !== b.length) return false;

  let out = 0;
  for (let i = 0; i < a.length; i++) {
    out |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return out === 0;
}
