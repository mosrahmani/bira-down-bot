import { DownloadForm } from "./download-form";

function WaveMark() {
  return (
    <svg className="wave-mark" viewBox="0 0 30 24" aria-hidden="true">
      <path d="M2 13h3l2-7 4 15 4-19 4 17 3-10 2 4h4" />
    </svg>
  );
}

export default function Home() {
  return (
    <main>
      <header className="site-header">
        <a className="brand" href="#top" aria-label="BiraDown home">
          <WaveMark />
          <span>BiraDown</span>
        </a>
        <span className="format-badge">Suno → MP3</span>
      </header>

      <section className="workspace" id="top">
        <div className="intro">
          <p className="eyebrow">A small tool for your music</p>
          <h1>Keep the song.<br />Skip the friction.</h1>
          <p className="lede">
            Paste a public Suno link. We’ll prepare a tagged MP3 with its title,
            artist, and cover art intact.
          </p>
        </div>

        <DownloadForm />
      </section>

      <footer>
        <p>For public songs you own or have permission to download.</p>
        <p className="footer-mark"><span aria-hidden="true">●</span> Files are prepared on demand</p>
      </footer>
    </main>
  );
}
