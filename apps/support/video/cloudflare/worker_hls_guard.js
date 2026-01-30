// PATH: apps/support/video/cloudflare/worker_hls_guard.js
//
// Cloudflare Worker - HLS Guard
// - playlist(.m3u8) / segment(.ts) 접근 제어 분리
// - exp / uid / sig / kid 검증 (요청마다)
// - 캐시 전략:
//   * .m3u8 : no-cache
//   * .ts   : public, immutable 가능하되 "요청 시 서명 만료 검증"은 항상 수행
//
// ENV:
// - HLS_SIGNING_KEYS: JSON string {"kid1":"secret1","kid2":"secret2"}
// - CLOCK_SKEW_SECONDS: number (default 60)

const textEncoder = new TextEncoder();

async function hmacSHA256(secret, message) {
  const key = await crypto.subtle.importKey(
    "raw",
    textEncoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );
  const sig = await crypto.subtle.sign("HMAC", key, textEncoder.encode(message));
  return btoa(String.fromCharCode(...new Uint8Array(sig)))
    .replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function parseQuery(url) {
  const u = new URL(url);
  const q = {};
  for (const [k, v] of u.searchParams.entries()) q[k] = v;
  return q;
}

function isExpired(exp, skew) {
  const now = Math.floor(Date.now() / 1000);
  return now > (Number(exp) + skew);
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname.toLowerCase();

    const isPlaylist = path.endsWith(".m3u8");
    const isSegment = path.endsWith(".ts");

    // only guard HLS
    if (!isPlaylist && !isSegment) {
      return fetch(request);
    }

    const q = parseQuery(request.url);
    const { exp, uid, sig, kid } = q;

    if (!exp || !uid || !sig || !kid) {
      return new Response("forbidden", { status: 403 });
    }

    let keys = {};
    try {
      keys = JSON.parse(env.HLS_SIGNING_KEYS || "{}");
    } catch (_) {
      return new Response("misconfigured_keys", { status: 500 });
    }

    const secret = keys[kid];
    if (!secret) {
      return new Response("invalid_key", { status: 403 });
    }

    const skew = Number(env.CLOCK_SKEW_SECONDS || 60);
    if (isExpired(exp, skew)) {
      return new Response("expired", { status: 403 });
    }

    // message = METHOD + PATH + uid + exp
    const msg = `${request.method}\n${url.pathname}\n${uid}\n${exp}`;
    const expected = await hmacSHA256(secret, msg);
    if (expected !== sig) {
      return new Response("bad_signature", { status: 403 });
    }

    // Forward with cache policy
    const resp = await fetch(request);
    const headers = new Headers(resp.headers);

    if (isPlaylist) {
      headers.set("Cache-Control", "no-cache");
    } else if (isSegment) {
      headers.set("Cache-Control", "public, max-age=31536000, immutable");
    }

    return new Response(resp.body, {
      status: resp.status,
      statusText: resp.statusText,
      headers,
    });
  }
};
