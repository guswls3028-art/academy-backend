import assert from "node:assert/strict";
import { createHmac, webcrypto } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";

const source = await readFile(new URL("../src/index.js", import.meta.url), "utf8");
const { default: worker } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
const secret = "synthetic-cdn-contract-secret";
const expiry = () => Math.floor(Date.now() / 1000) + 600;
let imports = 0;
Object.defineProperty(globalThis, "crypto", { configurable: true, value: {
  subtle: {
    importKey(...args) { imports++; return webcrypto.subtle.importKey(...args); },
    sign: webcrypto.subtle.sign.bind(webcrypto.subtle),
  },
} });

function signed(path, { key = secret, exp = expiry(), uid = "42", kid = "v1" } = {}) {
  const sig = createHmac("sha256", key).update(`${path}|${exp}|${kid}|${uid}`).digest("base64url");
  const url = new URL(path, "https://cdn.example");
  url.search = new URLSearchParams({ exp: String(exp), sig, kid, uid }).toString();
  return url;
}

function environment(objects, key = secret) {
  const calls = [];
  return {
    calls,
    CDN_HLS_SIGNING_SECRET: key,
    R2_VIDEO: { async get(name, options) {
      calls.push({ name, options });
      if (!(name in objects)) return null;
      let data = Buffer.from(objects[name]);
      const size = data.length;
      if (options.range) {
        const { offset, length, suffix } = options.range;
        data = suffix ? data.subarray(-suffix) : data.subarray(offset, length ? offset + length : undefined);
      }
      return { body: data, size, httpEtag: '"fixture"', httpMetadata: {} };
    } },
  };
}

test("master -> variant -> segment preserves valid playback and byte ranges", async () => {
  const root = "tenants/17/video/hls/23/";
  const env = environment({
    [root + "master.m3u8"]: "#EXTM3U\nv1/index.m3u8\n",
    [root + "v1/index.m3u8"]: "#EXTM3U\n#EXTINF:4,\nseg.ts\n",
    [root + "v1/seg.ts"]: "0123456789",
  });
  const masterUrl = signed("/" + root + "master.m3u8");
  const master = await worker.fetch(new Request(masterUrl), env, {});
  assert.equal(master.status, 200);
  const variantUrl = new URL((await master.text()).split("\n")[1], masterUrl);
  const variant = await worker.fetch(new Request(variantUrl), env, {});
  assert.equal(variant.status, 200);
  const segmentUrl = new URL((await variant.text()).split("\n")[2], variantUrl);
  const segment = await worker.fetch(new Request(segmentUrl), env, {});
  assert.equal(await segment.text(), "0123456789");
  const partial = await worker.fetch(new Request(segmentUrl, { headers: { Range: "bytes=2-5" } }), env, {});
  assert.equal(partial.status, 206);
  assert.equal(partial.headers.get("Content-Range"), "bytes 2-5/10");
  assert.equal(await partial.text(), "2345");
});

test("long playlists reuse the key while independently signing every URL", async () => {
  const path = "/tenants/17/video/hls/23/v1/index.m3u8";
  const playlist = "#EXTM3U\n" + Array.from({ length: 2500 }, (_, i) => `seg-${i}.ts`).join("\n");
  const env = environment({ [path.slice(1)]: playlist });
  const url = signed(path);
  imports = 0;
  const result = await worker.fetch(new Request(url, { headers: { Range: "bytes=0-10" } }), env, {});
  const body = await result.text();
  assert.equal(result.status, 200);
  assert.equal(result.headers.get("Content-Length"), String(Buffer.byteLength(body)));
  assert.equal(imports, 1);
  const lines = body.split("\n").slice(1);
  assert.equal(lines.length, 2500);
  for (let i = 0; i < lines.length; i++) {
    const child = new URL(lines[i], url);
    assert.equal(child.pathname, `/tenants/17/video/hls/23/v1/seg-${i}.ts`);
    assert.equal(child.href, signed(child.pathname, { exp: Number(url.searchParams.get("exp")) }).href);
  }
});

test("missing, expired, forged and cross-tenant signatures cannot access storage", async () => {
  const path = "/tenants/17/video/hls/23/master.m3u8";
  const forged = signed(path);
  forged.searchParams.set("sig", "forged");
  const otherTenant = signed(path);
  otherTenant.pathname = path.replace("/17/", "/18/");
  for (const [url, status] of [
    [new URL(path, "https://cdn.example"), 401],
    [signed(path, { exp: 1 }), 401], [forged, 403], [otherTenant, 403],
  ]) {
    const env = environment({});
    assert.equal((await worker.fetch(new Request(url), env, {})).status, status);
    assert.equal(env.calls.length, 0);
  }
});

test("key rotation and missing objects preserve failure and recovery behavior", async () => {
  const path = "/tenants/17/video/hls/23/v1/seg.ts";
  const key = "rotated-synthetic-key";
  const env = environment({ [path.slice(1)]: "video" }, key);
  assert.equal((await worker.fetch(new Request(signed(path)), env, {})).status, 403);
  const request = new Request(signed(path, { key }));
  assert.equal(await (await worker.fetch(request, env, {})).text(), "video");
  assert.equal((await worker.fetch(request, environment({}, key), {})).status, 404);
});
