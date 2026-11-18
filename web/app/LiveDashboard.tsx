"use client";

import Link from "next/link";
import { api, type ChannelStatus } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import { ago, clock, PLATFORM_LABEL } from "@/lib/format";
import ClipThumb from "@/components/ClipThumb";
import Sparkline from "@/components/Sparkline";
import styles from "./live.module.css";

function ChannelCard({ c, now }: { c: ChannelStatus; now: number }) {
  const ratio = c.baseline ? (c.score ?? 0) / c.baseline : 0;
  const lt = c.last_trigger;
  return (
    <article className={`card ${styles.channel}`}>
      <header className={styles.chHead}>
        <div className="grow">
          <h2 className={styles.chName}>{c.display_name}</h2>
          <div className="faint small">{PLATFORM_LABEL[c.platform]} · {c.name}</div>
        </div>
        <div className="row">
          {c.warmup && <span className="chip chip-warn" title="The detector learns this chat's normal level for 3 minutes">warm-up</span>}
          {c.paused && <span className="chip chip-info" title="Paused after a raid or gift-sub flood">paused</span>}
          <span className="chip chip-live">LIVE</span>
        </div>
      </header>
      <div className={styles.spark}>
        <Sparkline history={c.history ?? []} ratio={c.ratio_threshold ?? 3} />
      </div>
      <div className={styles.legend}>
        <span><i style={{ background: "var(--accent)" }} />chat score</span>
        <span><i style={{ background: "var(--faint)" }} />normal</span>
        <span><i className={styles.dash} />trigger</span>
      </div>
      <dl className={styles.stats}>
        <div><dt>Score</dt><dd>{c.score?.toFixed(0)} <span className="faint">/ {c.threshold?.toFixed(0)}</span></dd></div>
        <div><dt>vs normal</dt><dd className={ratio >= (c.ratio_threshold ?? 3) ? styles.hot : ""}>{ratio.toFixed(1)}×</dd></div>
        <div><dt>Clips</dt><dd>{c.clips}{c.dropped ? <span className="faint"> · {c.dropped} dropped</span> : null}</dd></div>
        <div><dt>Buffer</dt><dd>{((c.buffer_seconds ?? 0) / 60).toFixed(1)} min{c.restarts ? <span className="faint"> · {c.restarts} restarts</span> : null}</dd></div>
      </dl>
      <footer className={styles.chFoot}>
        {lt ? (
          <span>Last spike <strong>{lt.ratio.toFixed(1)}×</strong> at {clock(lt.moment)}{lt.final ? "" : " (open)"} · {ago(lt.moment, now)}</span>
        ) : (
          <span className="faint">No spikes yet{c.since ? ` · live here since ${clock(c.since)}` : ""}</span>
        )}
      </footer>
    </article>
  );
}

export default function LiveDashboard() {
  const { data: st, error } = usePoll(api.status, 2000);
  const { data: clips } = usePoll(() => api.clips({}), 10000);

  if (error && !st) return <div className="error-box">{error}</div>;
  if (!st) return <div className="muted">Loading…</div>;

  const live = st.channels.filter((c) => c.live);
  const offline = st.channels.filter((c) => !c.live);
  const latest = (clips ?? []).slice(0, 8);

  return (
    <>
      <div className={styles.summary}>
        <div className={`card ${styles.kpi}`}>
          <span className={styles.kpiLabel}>Recording</span>
          <span className={styles.kpiValue}>
            {st.live_recording ? `${live.length} live` : "off"}
          </span>
          <span className="faint small">{st.live_recording ? `${st.channels.length} watched` : "started with --no-live"}</span>
        </div>
        <div className={`card ${styles.kpi}`}>
          <span className={styles.kpiLabel}>Chat</span>
          <span className={styles.kpiValue} style={{ color: st.chat.connected || !st.live_recording ? undefined : "var(--live)" }}>
            {!st.live_recording ? "–" : st.chat.connected ? "connected" : "reconnecting"}
          </span>
          <span className="faint small">{st.chat.reconnects} reconnects</span>
        </div>
        <div className={`card ${styles.kpi}`}>
          <span className={styles.kpiLabel}>Render queue</span>
          <span className={styles.kpiValue}>{st.queue}</span>
          <span className="faint small">{st.working_on ? `rendering ${st.working_on.channel} (${st.working_on.source})` : "idle"}</span>
        </div>
        <div className={`card ${styles.kpi}`}>
          <span className={styles.kpiLabel}>To review</span>
          <span className={styles.kpiValue}>{st.clips.new ?? 0}</span>
          <span className="faint small">{st.clips.approved ?? 0} approved · {st.clips.posted ?? 0} posted</span>
        </div>
        <div className={`card ${styles.kpi}`}>
          <span className={styles.kpiLabel}>Disk free</span>
          <span className={styles.kpiValue} style={{ color: st.disk.free_gb < st.disk.min_free_gb * 1.5 ? "var(--warn)" : undefined }}>
            {st.disk.free_gb} GB
          </span>
          <span className="faint small">needs {st.disk.min_free_gb} GB to start</span>
        </div>
      </div>

      <div className="section-title"><h2>Live now</h2></div>
      {live.length ? (
        <div className={styles.grid}>{live.map((c) => <ChannelCard key={c.name} c={c} now={st.now} />)}</div>
      ) : (
        <div className="empty">
          {st.live_recording
            ? "None of your streamers are live. Channels are checked every minute."
            : <>Live recording is off. Start the API without <code>--no-live</code> to record.</>}
        </div>
      )}

      {offline.length > 0 && (
        <div className={styles.offline}>
          <span className="faint small">Watching, offline:</span>
          {offline.map((c) => <span key={c.name} className="chip">{c.display_name}</span>)}
          <Link href="/streamers" className="small">Manage streamers</Link>
        </div>
      )}

      <div className="section-title">
        <h2>Latest clips</h2>
        <Link href="/clips" className="small">All clips →</Link>
      </div>
      {latest.length ? (
        <div className={styles.clips}>
          {latest.map((c) => <ClipThumb key={c.id} clip={c} href={`/clips#${c.id}`} />)}
        </div>
      ) : (
        <div className="empty">No clips yet. They appear here a minute or two after a chat spike.</div>
      )}
    </>
  );
}
