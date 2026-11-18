import LinksClipper from "./LinksClipper";

export default function LinksPage() {
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Links</h1>
          <p>After the stream: paste a VOD or video link. Chat replay, &ldquo;most replayed&rdquo; and loudness point to the best moments.</p>
        </div>
      </div>
      <LinksClipper />
    </div>
  );
}
