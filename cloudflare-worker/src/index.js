const API_METHOD = /^[A-Za-z][A-Za-z0-9_]{0,63}$/;

function unauthorized() {
  return new Response("Unauthorized", { status: 401 });
}

export function safeEqual(left, right) {
  if (typeof left !== "string" || typeof right !== "string" || left.length !== right.length) {
    return false;
  }
  let difference = 0;
  for (let index = 0; index < left.length; index += 1) {
    difference |= left.charCodeAt(index) ^ right.charCodeAt(index);
  }
  return difference === 0;
}

export const worker = {
  async fetch(request, env) {
    if (!safeEqual(request.headers.get("X-Bira-Relay-Secret"), env.RELAY_SECRET)) {
      return unauthorized();
    }
    if (request.method !== "GET" && request.method !== "POST") {
      return new Response("Method not allowed", { status: 405 });
    }

    const url = new URL(request.url);
    const match = url.pathname.match(/^\/(api|file)\/bot[^/]+\/(.+)$/);
    if (!match) {
      return new Response("Not found", { status: 404 });
    }

    const [, kind, remainder] = match;
    let telegramPath;
    if (kind === "api") {
      if (!API_METHOD.test(remainder)) {
        return new Response("Invalid Telegram method", { status: 400 });
      }
      telegramPath = `/bot${env.BOT_TOKEN}/${remainder}`;
    } else {
      let decodedSegments;
      try {
        decodedSegments = remainder.split("/").map((part) => decodeURIComponent(part));
      } catch {
        return new Response("Invalid Telegram file path", { status: 400 });
      }
      if (decodedSegments.some((part) => !part || part === "." || part === "..")) {
        return new Response("Invalid Telegram file path", { status: 400 });
      }
      telegramPath = `/file/bot${env.BOT_TOKEN}/${remainder}`;
    }

    const headers = new Headers(request.headers);
    headers.delete("X-Bira-Relay-Secret");
    headers.delete("Host");
    headers.delete("CF-Connecting-IP");
    headers.delete("CF-IPCountry");
    headers.delete("CF-Ray");
    headers.delete("X-Forwarded-For");
    headers.delete("X-Forwarded-Proto");

    const upstream = await fetch(`https://api.telegram.org${telegramPath}${url.search}`, {
      method: request.method,
      headers,
      body: request.method === "GET" ? undefined : request.body,
      redirect: "manual",
    });
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: upstream.headers,
    });
  },
};

export default worker;
