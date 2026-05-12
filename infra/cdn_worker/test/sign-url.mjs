// Generate a signed URL for ad-hoc verification.
// Usage: node sign-url.mjs <base> <path> <secret> <user_id?> <ttl_seconds?>
import { createHmac } from "node:crypto";

const [, , base, pathRaw, secret, userIdRaw, ttlRaw] = process.argv;
if (!base || !pathRaw || !secret) {
  console.error("usage: node sign-url.mjs <base-url> <path> <secret> [user_id] [ttl_seconds]");
  process.exit(2);
}
const path = pathRaw.startsWith("/") ? pathRaw : `/${pathRaw}`;
const ttl = parseInt(ttlRaw || "3600", 10);
const exp = Math.floor(Date.now() / 1000) + ttl;
const uid = userIdRaw && userIdRaw !== "null" ? String(parseInt(userIdRaw, 10)) : "";
const kid = "v1";
const message = `${path}|${exp}|${kid}|${uid}`;
const mac = createHmac("sha256", secret).update(message, "utf-8").digest();
const sig = mac.toString("base64").replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
const qs = new URLSearchParams({ exp: String(exp), sig, kid });
if (uid) qs.set("uid", uid);
console.log(`${base.replace(/\/$/, "")}${path}?${qs.toString()}`);
