import { NextRequest, NextResponse } from "next/server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const apiUrl = process.env.DOWNLOAD_API_URL ?? "http://localhost:8000";

export async function POST(request: NextRequest) {
  let body: { url?: unknown };
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "Enter a valid public Suno song link." }, { status: 400 });
  }

  if (typeof body.url !== "string" || body.url.length > 2048) {
    return NextResponse.json({ error: "Enter a valid public Suno song link." }, { status: 400 });
  }

  try {
    const upstream = await fetch(`${apiUrl}/download`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url: body.url }),
      cache: "no-store",
      signal: AbortSignal.timeout(180_000),
    });

    if (!upstream.ok) {
      const payload = await upstream.json().catch(() => null);
      return NextResponse.json(
        { error: payload?.detail ?? "We could not prepare this song. Please try again." },
        { status: upstream.status },
      );
    }

    const headers = new Headers({
      "Content-Type": "audio/mpeg",
      "Cache-Control": "no-store",
    });
    for (const name of ["Content-Disposition", "Content-Length", "X-Song-Title", "X-Song-Artist"]) {
      const value = upstream.headers.get(name);
      if (value) headers.set(name, value);
    }
    return new Response(upstream.body, { status: 200, headers });
  } catch {
    return NextResponse.json(
      { error: "The download service is unavailable. Please try again shortly." },
      { status: 503 },
    );
  }
}
