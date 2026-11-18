import StreamersManager from "./StreamersManager";

export default function StreamersPage() {
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Streamers</h1>
          <p>Who gets recorded. Only streamers with a permission note are ever recorded or clipped.</p>
        </div>
      </div>
      <StreamersManager />
    </div>
  );
}
