import assert from "node:assert/strict";
import test from "node:test";

import { safeEqual, worker } from "../src/index.js";

const env = { BOT_TOKEN: "123:real-secret", RELAY_SECRET: "relay-secret" };

test("safeEqual compares complete secrets", () => {
  assert.equal(safeEqual("same", "same"), true);
  assert.equal(safeEqual("same", "diff"), false);
  assert.equal(safeEqual("short", "longer"), false);
});

test("rejects requests without the relay secret", async () => {
  const response = await worker.fetch(new Request("https://relay.example/api/bot0:fake/getMe"), env);
  assert.equal(response.status, 401);
});

test("forwards an authenticated API method without leaking relay headers", async () => {
  const originalFetch = globalThis.fetch;
  let forwarded;
  globalThis.fetch = async (url, init) => {
    forwarded = { url, init };
    return new Response('{"ok":true}', { status: 200, headers: { "Content-Type": "application/json" } });
  };
  try {
    const request = new Request("https://relay.example/api/bot0:placeholder/getMe", {
      headers: { "X-Bira-Relay-Secret": env.RELAY_SECRET },
    });
    const response = await worker.fetch(request, env);
    assert.equal(response.status, 200);
    assert.equal(forwarded.url, "https://api.telegram.org/bot123:real-secret/getMe");
    assert.equal(forwarded.init.headers.has("X-Bira-Relay-Secret"), false);
  } finally {
    globalThis.fetch = originalFetch;
  }
});

test("rejects malformed file paths", async () => {
  const request = new Request("https://relay.example/file/bot0:placeholder/%ZZ", {
    headers: { "X-Bira-Relay-Secret": env.RELAY_SECRET },
  });
  const response = await worker.fetch(request, env);
  assert.equal(response.status, 400);
});
