import LiveDashboard from "./LiveDashboard";

export default function LivePage() {
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Live</h1>
          <p>Streamers being recorded right now, how their chat is moving, and what just got clipped.</p>
        </div>
      </div>
      <LiveDashboard />
    </div>
  );
}
