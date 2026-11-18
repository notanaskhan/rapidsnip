import SettingsView from "./SettingsView";

export default function SettingsPage() {
  return (
    <div className="page">
      <div className="page-head">
        <div>
          <h1>Settings</h1>
          <p>How the detector and renderer are set up right now.</p>
        </div>
      </div>
      <SettingsView />
    </div>
  );
}
