// ============================================================================
// Academy Video CDN Worker — HLS signed URL gateway
// ============================================================================
// 백엔드(playback_mixin.py:206 → cloudflare_signing.py)가 생성한 서명을
// 본 Worker 가 검증한 뒤 R2 private bucket 에서 fetch 해 응답.
//
// URL 형식 (백엔드 build_url 과 1:1 일치):
//   https://cdn.hakwonplus.com/{path}?exp=<unix>&sig=<b64url>&kid=v1&uid=<user_id>
//
// 서명 메시지: `{path}|{exp}|{kid}|{uid}` (uid 없으면 빈 문자열)
// HMAC-SHA256(secret, message) → urlsafe_b64encode without padding
//
// 환경 변수 (wrangler.toml secret + binding):
//   CDN_HLS_SIGNING_SECRET — 백엔드 SSM /academy/api/env 의 CDN_HLS_SIGNING_SECRET 과 동일
//   R2_VIDEO              — R2 bucket binding (private, no public access)
// ============================================================================

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname;
    const sig = url.searchParams.get("sig");
    const exp = url.searchParams.get("exp");
    const kid = url.searchParams.get("kid") || "v1";
    const uid = url.searchParams.get("uid") || "";

    // 1) Required query params
    if (!sig || !exp) {
      return new Response("missing signature", { status: 401 });
    }

    // 2) Expiry check
    const now = Math.floor(Date.now() / 1000);
    const expNum = parseInt(exp, 10);
    if (!Number.isFinite(expNum) || expNum < now) {
      return new Response("signature expired", { status: 401 });
    }

    // 3) Compute expected HMAC
    const secret = env.CDN_HLS_SIGNING_SECRET;
    if (!secret) {
      return new Response("worker misconfigured: secret missing", { status: 500 });
    }
    const message = `${path}|${expNum}|${kid}|${uid}`;
    const expected = await hmacSha256B64url(secret, message);

    // 4) Timing-safe compare
    if (!timingSafeEqual(sig, expected)) {
      return new Response("invalid signature", { status: 403 });
    }

    // 5) Fetch from R2 (path strips leading slash; honor client Range header)
    // m3u8 은 작은 파일 + body rewrite 필요 → Range 무시 + 전체 body 가져옴.
    // segment/jpg 등 binary 만 Range 적용.
    const r2Key = path.replace(/^\/+/, "");
    const isM3u8 = r2Key.endsWith(".m3u8");
    const rangeHeader = request.headers.get("Range");
    const getOpts = {};
    let parsedRange = (!isM3u8 && rangeHeader) ? parseRangeHeader(rangeHeader) : null;
    if (parsedRange) getOpts.range = parsedRange;
    const obj = await env.R2_VIDEO.get(r2Key, getOpts);
    if (!obj || !obj.body) {
      return new Response("not found", { status: 404 });
    }

    // 5.5) m3u8 body rewrite — propagate sig to relative variant/segment URLs.
    // HLS spec drops query on relative URL resolve; without rewrite player gets
    // 401 "missing signature" on variant/segment fetch. (Root cause of 2026-05-15
    // video playback outage: master.m3u8 200 OK but `v2/index.m3u8` etc. 401.)
    // 2026-05-15 추가 fix: m3u8 은 항상 rewrite (Range request 무시 — 위 isM3u8 분기에서 처리).
    let m3u8Body = null;
    if (isM3u8) {
      const text = await new Response(obj.body).text();
      m3u8Body = await rewriteM3u8(text, path, expNum, kid, uid, secret);
    }

    // 6) Response with cache headers
    const headers = new Headers();
    headers.set("Content-Type", obj.httpMetadata?.contentType || guessContentType(r2Key));
    headers.set("ETag", obj.httpEtag);
    headers.set("Cache-Control", obj.httpMetadata?.cacheControl || cacheControlFor(r2Key));
    headers.set("Accept-Ranges", "bytes");

    if (m3u8Body !== null) {
      const buf = new TextEncoder().encode(m3u8Body);
      headers.set("Content-Length", String(buf.byteLength));
      return new Response(buf, { headers, status: 200 });
    }

    // Only emit Content-Range + 206 on a real (and honored) Range request.
    let status = 200;
    if (parsedRange) {
      let start, length;
      if (Number.isFinite(parsedRange.suffix)) {
        start = Math.max(0, obj.size - parsedRange.suffix);
        length = obj.size - start;
      } else if (Number.isFinite(parsedRange.offset) && Number.isFinite(parsedRange.length)) {
        start = parsedRange.offset;
        length = parsedRange.length;
      } else if (Number.isFinite(parsedRange.offset)) {
        start = parsedRange.offset;
        length = obj.size - start;
      }
      if (Number.isFinite(start) && Number.isFinite(length) && length > 0) {
        const end = start + length - 1;
        headers.set("Content-Range", `bytes ${start}-${end}/${obj.size}`);
        headers.set("Content-Length", String(length));
        status = 206;
      } else {
        headers.set("Content-Length", String(obj.size));
      }
    } else {
      headers.set("Content-Length", String(obj.size));
    }
    return new Response(obj.body, { headers, status });
  },
};

// Parse RFC 7233 single-range header. Returns {offset, length} or null.
function parseRangeHeader(value) {
  const m = /^bytes=(\d*)-(\d*)$/.exec(String(value).trim());
  if (!m) return null;
  const startStr = m[1];
  const endStr = m[2];
  if (startStr === "" && endStr === "") return null;
  if (startStr === "") {
    // suffix range: last N bytes — R2 supports via { suffix: N }
    const n = parseInt(endStr, 10);
    if (!Number.isFinite(n) || n <= 0) return null;
    return { suffix: n };
  }
  const start = parseInt(startStr, 10);
  if (!Number.isFinite(start) || start < 0) return null;
  if (endStr === "") return { offset: start };
  const end = parseInt(endStr, 10);
  if (!Number.isFinite(end) || end < start) return null;
  return { offset: start, length: end - start + 1 };
}

// ─── m3u8 body rewrite ──────────────────────────────────────────────────────

// Rewrite relative URLs inside an m3u8 manifest to include the same signed
// query (exp/sig/kid/uid) so HLS clients can fetch variants/segments without
// losing the signature across `urljoin`. Absolute URLs (http*) and comments
// (`#…`) pass through unchanged.
async function rewriteM3u8(text, currentPath, exp, kid, uid, secret) {
  // base dir = currentPath up to and including final "/"
  const slash = currentPath.lastIndexOf("/");
  const baseDir = slash >= 0 ? currentPath.substring(0, slash + 1) : "/";
  const lines = text.split("\n");
  const out = [];
  for (const raw of lines) {
    const trimmed = raw.trim();
    if (trimmed === "" || trimmed.startsWith("#")) {
      out.push(raw);
      continue;
    }
    if (/^https?:\/\//i.test(trimmed)) {
      // absolute URL — leave alone (already cross-origin or pre-signed)
      out.push(raw);
      continue;
    }
    // resolve relative against baseDir
    let resolved;
    if (trimmed.startsWith("/")) resolved = trimmed;
    else resolved = baseDir + trimmed;
    // normalize ".." / "."
    resolved = normalizeUrlPath(resolved);
    const sigNew = await hmacSha256B64url(secret, `${resolved}|${exp}|${kid}|${uid}`);
    const qs = `exp=${exp}&sig=${encodeURIComponent(sigNew)}&kid=${encodeURIComponent(kid)}` +
      (uid ? `&uid=${encodeURIComponent(uid)}` : "");
    out.push(trimmed + "?" + qs);
  }
  return out.join("\n");
}

function normalizeUrlPath(p) {
  const parts = p.split("/");
  const stack = [];
  for (const seg of parts) {
    if (seg === "" || seg === ".") {
      if (stack.length === 0) stack.push("");
      continue;
    }
    if (seg === "..") {
      if (stack.length > 1) stack.pop();
      continue;
    }
    stack.push(seg);
  }
  let out = stack.join("/");
  if (!out.startsWith("/")) out = "/" + out;
  return out;
}

// ─── helpers ────────────────────────────────────────────────────────────────

async function hmacSha256B64url(secret, message) {
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey(
    "raw",
    enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
  const macBuf = await crypto.subtle.sign("HMAC", key, enc.encode(message));
  return b64urlNoPad(macBuf);
}

function b64urlNoPad(arrayBuffer) {
  const bytes = new Uint8Array(arrayBuffer);
  let bin = "";
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
  const b64 = btoa(bin);
  return b64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function timingSafeEqual(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function guessContentType(key) {
  if (key.endsWith(".m3u8")) return "application/vnd.apple.mpegurl";
  if (key.endsWith(".ts")) return "video/mp2t";
  if (key.endsWith(".jpg") || key.endsWith(".jpeg")) return "image/jpeg";
  if (key.endsWith(".png")) return "image/png";
  return "application/octet-stream";
}

function cacheControlFor(key) {
  // master.m3u8 / variant index.m3u8 — short cache (manifests can be updated)
  if (key.endsWith(".m3u8")) return "public, max-age=60";
  // segments / thumbnails — long cache (immutable by HLS spec convention)
  if (key.endsWith(".ts") || key.endsWith(".jpg")) return "public, max-age=604800, immutable";
  return "public, max-age=300";
}
