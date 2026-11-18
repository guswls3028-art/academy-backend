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
    R2_VIDEO: {
      async get(name, options) {
        calls.push({ name, options });
        if (!(name in objects)) return null;
        let data = Buffer.from(objects[name]);
        const size = data.length;
        if (options.range) {
          const { offset, length, suffix } = options.range;
          if (size === 0 || offset >= size) {
            throw new Error("get: The requested range is not satisfiable. (10039)");
          }
          data = suffix ? data.subarray(-suffix) : data.subarray(offset, length ? offset + length : undefined);
        }
        return { body: data, size, httpEtag: '"fixture"', httpMetadata: {} };
      },
      async head(name) {
        calls.push({ name, method: "head" });
        return name in objects ? { size: Buffer.byteLength(objects[name]) } : null;
      },
    },
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
  const headers = { Origin: "https://academy.example" };
  const master = await worker.fetch(new Request(masterUrl, { headers }), env, {});
  assert.equal(master.status, 200);
  assert.equal(master.headers.get("Access-Control-Allow-Origin"), "*");
  assert.equal(master.headers.get("Access-Control-Allow-Credentials"), null);
  const variantUrl = new URL((await master.text()).split("\n")[1], masterUrl);
  const variant = await worker.fetch(new Request(variantUrl, { headers }), env, {});
  assert.equal(variant.status, 200);
  assert.equal(variant.headers.get("Access-Control-Allow-Origin"), "*");
  const segmentUrl = new URL((await variant.text()).split("\n")[2], variantUrl);
  const segment = await worker.fetch(new Request(segmentUrl), env, {});
  assert.equal(await segment.text(), "0123456789");
  const partial = await worker.fetch(new Request(segmentUrl, { headers: { ...headers, Range: "bytes=2-5" } }), env, {});
  assert.equal(partial.status, 206);
  assert.equal(partial.headers.get("Access-Control-Allow-Origin"), "*");
  assert.equal(partial.headers.get("Access-Control-Expose-Headers"), "Accept-Ranges, Content-Range");
  assert.equal(partial.headers.get("Content-Range"), "bytes 2-5/10");
  assert.equal(await partial.text(), "2345");
});

test("binary range headers match the bytes that R2 returns", async () => {
  const path = "/tenants/17/video/hls/23/v1/seg.ts";
  const data = Buffer.alloc(968200).fill("0123456789");
  const env = environment({ [path.slice(1)]: data });
  for (const [range, start, end] of [
    ["bytes=0-1048575", 0, data.length - 1],
    ["bytes=2-5", 2, 5],
    ["bytes=968197-1048575", 968197, data.length - 1],
    ["bytes=968197-", 968197, data.length - 1],
    ["bytes=-3", 968197, data.length - 1],
    ["bytes=-1048576", 0, data.length - 1],
  ]) {
    const request = new Request(signed(path), { headers: { Origin: "https://academy.example", Range: range } });
    const response = await worker.fetch(request, env, {});
    assert.equal(response.status, 206, range);
    assert.equal(response.headers.get("Content-Range"), `bytes ${start}-${end}/${data.length}`, range);
    assert.equal(response.headers.get("Content-Length"), String(end - start + 1), range);
    assert.equal(response.headers.get("Access-Control-Allow-Origin"), "*");
    assert.deepEqual(Buffer.from(await response.arrayBuffer()), data.subarray(start, end + 1), range);
  }
  assert.equal(env.calls.length, 6);
  assert.ok(env.calls.every(call => call.options.range));
});

test("unsatisfiable binary ranges return an empty 416 with the object size", async () => {
  const path = "/tenants/17/video/hls/23/v1/seg.ts";
  const emptyPath = "/tenants/17/video/hls/23/v1/empty.ts";
  const env = environment({ [path.slice(1)]: "0123456789", [emptyPath.slice(1)]: "" });
  for (const [key, range, size] of [[path, "bytes=10-", 10], [path, "bytes=10-20", 10], [emptyPath, "bytes=-2", 0]]) {
    const response = await worker.fetch(new Request(signed(key), { headers: { Range: range } }), env, {});
    assert.equal(response.status, 416);
    assert.equal(response.headers.get("Content-Range"), `bytes */${size}`);
    assert.equal(response.headers.get("Content-Length"), "0");
    assert.equal(response.headers.get("Access-Control-Allow-Origin"), "*");
    assert.equal((await response.arrayBuffer()).byteLength, 0);
  }
  assert.equal(env.calls.filter(call => call.method === "head").length, 3);
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
    const result = await worker.fetch(new Request(url, { headers: { Origin: "https://academy.example" } }), env, {});
    assert.equal(result.status, status);
    assert.equal(result.headers.get("Access-Control-Allow-Origin"), null);
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
