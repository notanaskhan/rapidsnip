"use client";

import { api } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import styles from "./settings.module.css";

const LABELS: Record<string, string> = {
  window_seconds: "Window", step_seconds: "Step", baseline_minutes: "Baseline span", warmup_minutes: "Warm-up",
  ratio_threshold: "Spike threshold (× normal)", z_threshold: "Spread threshold (× MAD)", min_unique_chatters: "Min chatters",
  cooldown_seconds: "Cooldown", pause_after_event_seconds: "Pause after raids/gift floods",
  pre_seconds: "Before moment", post_seconds: "After moment", chat_delay_seconds: "Chat delay",
  max_length_seconds: "Max clip length", merge_gap_seconds: "Merge spikes closer than",
};

function unit(k: string) {
  if (k.endsWith("_seconds")) return " s";
  if (k.endsWith("_minutes")) return " min";
  return "";
}

export default function SettingsView() {
  const { data: s, error } = usePoll(api.settings, 60000);
  const { data: st } = usePoll(api.status, 10000);
  if (error) return <div className="error-box">{error}</div>;
  if (!s) return <div className="muted">Loading…</div>;
  const det = Object.entries(s.detector).filter(([, v]) => typeof v === "number");
  const reactions = (s.detector.reactions as string[]) ?? [];

  return (
    <div className={styles.grid}>
      <section className="card card-pad">
        <h2>Detector</h2>
        <dl className={styles.kv}>
          {det.map(([k, v]) => <div key={k}><dt>{LABELS[k] ?? k}</dt><dd>{String(v)}{unit(k)}</dd></div>)}
        </dl>
        <h3 style={{ margin: "14px 0 8px" }}>Reactions that boost the score</h3>
        <div className="row" style={{ gap: 5 }}>{reactions.map((r) => <span key={r} className="chip">{r}</span>)}</div>
      </section>
      <section className="card card-pad">
        <h2>Clips</h2>
        <dl className={styles.kv}>
          {Object.entries(s.clip).map(([k, v]) => <div key={k}><dt>{LABELS[k] ?? k}</dt><dd>{v}{unit(k)}</dd></div>)}
          <div><dt>Rolling buffer</dt><dd>{s.buffer_minutes} min per channel</dd></div>
          <div><dt>Quality</dt><dd className="mono">{s.quality}</dd></div>
          <div><dt>Captions</dt><dd>{s.captions.enabled ? `on · ${s.captions.words_per_caption} words` : "off"}</dd></div>
          <div><dt>Whisper model</dt><dd className="mono">{s.captions.model.split("/").pop()}</dd></div>
          <div><dt>Vertical layout</dt><dd>{s.layout.enabled ? `${s.layout.output[0]}×${s.layout.output[1]}` : "off"}</dd></div>
        </dl>
      </section>
      <section className="card card-pad">
        <h2>Machine</h2>
        <dl className={styles.kv}>
          <div><dt>Disk free</dt><dd>{st ? `${st.disk.free_gb} of ${st.disk.total_gb} GB` : "…"}</dd></div>
          <div><dt>Refuses to start below</dt><dd>{st?.disk.min_free_gb} GB</dd></div>
          <div><dt>Live recording</dt><dd>{st ? (st.live_recording ? "on" : "off (--no-live)") : "…"}</dd></div>
          <div><dt>Live platforms</dt><dd>{s.live_platforms.join(", ")}</dd></div>
        </dl>
      </section>
      <section className="card card-pad">
        <h2>Changing these</h2>
        <p className="muted small">
          Global settings live in <code>config.yaml</code> and apply after a restart. Per-streamer threshold, chat delay
          and layout are on the Streamers page and apply immediately. Tune with real chat first:
        </p>
        <pre className={styles.pre}>uv run python tools/replay.py data/chat/&lt;channel&gt;/&lt;date&gt;.jsonl --ratio 3.5 --peaks 10</pre>
        <h3 style={{ margin: "14px 0 6px" }}>Running the app</h3>
        <pre className={styles.pre}>{`uv run python -m clipper serve        # API + live recording, :8000
cd web && npm run dev                  # this UI, :3210
uv run python -m clipper serve --no-live   # links and review only`}</pre>
      </section>
    </div>
  );
}
