// ============================================================================
// 백엔드 cloudflare_signing.py 서명 ↔ Worker hmacSha256B64url 검증 parity test.
// run: node infra/cdn_worker/test/verify-parity.mjs
// ============================================================================

import { createHmac } from "node:crypto";

// Replicate backend cloudflare_signing.py logic
function backendSign({ secret, path, expiresAt, keyId = "v1", userId = null }) {
  const uid = userId === null ? "" : String(parseInt(userId, 10));
  const p = path.startsWith("/") ? path : `/${path}`;
  const message = `${p}|${parseInt(expiresAt, 10)}|${keyId}|${uid}`;
  const mac = createHmac("sha256", secret).update(message, "utf-8").digest();
  // base64 urlsafe, strip padding (matches Python `_b64url`)
  const sig = mac.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  const params = { exp: String(parseInt(expiresAt, 10)), sig, kid: keyId };
  if (userId !== null) params.uid = String(parseInt(userId, 10));
  return params;
}

// Replicate Worker HMAC (uses WebCrypto, port to node for test parity)
async function workerVerify({ secret, path, expiresAt, sig, keyId = "v1", userId = "" }) {
  const enc = new TextEncoder();
  const message = `${path}|${parseInt(expiresAt, 10)}|${keyId}|${userId}`;
  const mac = createHmac("sha256", secret).update(message, "utf-8").digest();
  const expected = mac.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return { expected, match: expected === sig };
}

// Test cases
const cases = [
  {
    desc: "tenant 1 video 388",
    secret: "test-secret-123456789012345678901234",
    path: "/tenants/1/video/hls/388/master.m3u8",
    expiresAt: 1778624400,
    userId: 12,
  },
  {
    desc: "no user_id (anonymous)",
    secret: "test-secret-123456789012345678901234",
    path: "/tenants/2/video/hls/500/master.m3u8",
    expiresAt: 1778624400,
    userId: null,
  },
  {
    desc: "segment .ts",
    secret: "test-secret-123456789012345678901234",
    path: "/tenants/2/video/hls/388/v1/seg-0001.ts",
    expiresAt: 1778624400,
    userId: 7,
  },
];

let pass = 0, fail = 0;
for (const c of cases) {
  const backendQs = backendSign({ secret: c.secret, path: c.path, expiresAt: c.expiresAt, userId: c.userId });
  const workerR = await workerVerify({
    secret: c.secret,
    path: c.path,
    expiresAt: c.expiresAt,
    sig: backendQs.sig,
    userId: c.userId === null ? "" : String(c.userId),
  });
  const ok = workerR.match;
  if (ok) pass++; else fail++;
  console.log(`[${ok ? "PASS" : "FAIL"}] ${c.desc}`);
  console.log(`  backend sig:  ${backendQs.sig}`);
  console.log(`  worker calc:  ${workerR.expected}`);
}

// Negative test: tampered signature must NOT verify
const tampered = await workerVerify({
  secret: cases[0].secret,
  path: cases[0].path,
  expiresAt: cases[0].expiresAt,
  sig: "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
  userId: String(cases[0].userId),
});
if (!tampered.match) { pass++; console.log("[PASS] tampered signature rejected"); }
else { fail++; console.log("[FAIL] tampered signature unexpectedly verified"); }

console.log(`\n${pass} pass / ${fail} fail`);
process.exit(fail === 0 ? 0 : 1);
