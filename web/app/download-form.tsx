"use client";

import { FormEvent, useRef, useState } from "react";

type Status = "idle" | "loading" | "success" | "error";

function ArrowIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M10 3v11m0 0 4-4m-4 4-4-4M4 17h12" />
    </svg>
  );
}

function filenameFromDisposition(value: string | null) {
  if (!value) return "BiraDown-Suno.mp3";
  const encoded = value.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  if (encoded) return decodeURIComponent(encoded);
  return value.match(/filename="?([^";]+)"?/i)?.[1] ?? "BiraDown-Suno.mp3";
}

function decodeHeader(value: string | null, fallback: string) {
  if (!value) return fallback;
  try { return decodeURIComponent(value); } catch { return fallback; }
}

export function DownloadForm() {
  const [url, setUrl] = useState("");
  const [status, setStatus] = useState<Status>("idle");
  const [message, setMessage] = useState("");
  const [song, setSong] = useState<{ title: string; artist: string } | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  async function pasteLink() {
    try {
      const value = await navigator.clipboard.readText();
      setUrl(value.trim());
      setStatus("idle");
      inputRef.current?.focus();
    } catch {
      setStatus("error");
      setMessage("Clipboard access was blocked. Paste the link into the field instead.");
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setStatus("loading");
    setMessage("");
    setSong(null);

    try {
      const response = await fetch("/api/download", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: url.trim() }),
      });

      if (!response.ok) {
        const payload = await response.json().catch(() => null);
        throw new Error(payload?.error ?? "We could not prepare this song. Please try again.");
      }

      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = filenameFromDisposition(response.headers.get("Content-Disposition"));
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1_000);

      setSong({
        title: decodeHeader(response.headers.get("X-Song-Title"), "Your Suno song"),
        artist: decodeHeader(response.headers.get("X-Song-Artist"), "Suno creator"),
      });
      setStatus("success");
    } catch (error) {
      setStatus("error");
      setMessage(error instanceof Error ? error.message : "Something went wrong. Please try again.");
    }
  }

  function reset() {
    setUrl("");
    setStatus("idle");
    setSong(null);
    setMessage("");
    requestAnimationFrame(() => inputRef.current?.focus());
  }

  return (
    <div className="tool-panel">
      <div className="panel-number" aria-hidden="true">01</div>
      <div className="panel-copy">
        <h2>Download a song</h2>
        <p>Public Suno song and share links are supported.</p>
      </div>

      <form onSubmit={submit} noValidate>
        <label htmlFor="suno-url">Suno link</label>
        <div className="input-shell" data-invalid={status === "error" || undefined}>
          <input
            ref={inputRef}
            id="suno-url"
            name="url"
            type="url"
            inputMode="url"
            autoComplete="url"
            placeholder="https://suno.com/song/…"
            value={url}
            onChange={(event) => {
              setUrl(event.target.value);
              if (status !== "loading") setStatus("idle");
            }}
            aria-invalid={status === "error"}
            aria-describedby="form-status"
            disabled={status === "loading"}
            required
          />
          <button className="paste-button" type="button" onClick={pasteLink} disabled={status === "loading"}>
            Paste
          </button>
        </div>

        <button className="download-button" type="submit" disabled={status === "loading" || !url.trim()}>
          <span>{status === "loading" ? "Preparing MP3…" : "Download MP3"}</span>
          {status === "loading" ? <span className="spinner" aria-hidden="true" /> : <ArrowIcon />}
        </button>
      </form>

      <div id="form-status" className={`status status-${status}`} role="status" aria-live="polite">
        {status === "loading" && (
          <><strong>Working on it</strong><span>Reading the song and preparing its audio. Keep this tab open.</span></>
        )}
        {status === "error" && (
          <><strong>That didn’t work</strong><span>{message}</span></>
        )}
        {status === "success" && song && (
          <div className="success-row">
            <span className="success-icon" aria-hidden="true">✓</span>
            <span><strong>{song.title}</strong><small>{song.artist} · MP3 downloaded</small></span>
            <button type="button" onClick={reset}>Another</button>
          </div>
        )}
      </div>

      <div className="process-note">
        <span>Tagged MP3</span><i aria-hidden="true" /><span>Cover included</span><i aria-hidden="true" /><span>No sign-in</span>
      </div>
    </div>
  );
}
