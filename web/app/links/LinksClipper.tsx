"use client";

import { useMemo, useState } from "react";
import { api, type Candidate, type LinkJob } from "@/lib/api";
import { useFlash, usePoll } from "@/lib/hooks";
import { ago, duration, hms, parseTime, PLATFORM_LABEL } from "@/lib/format";
import ClipThumb from "@/components/ClipThumb";
import styles from "./links.module.css";

const SOURCE_LABEL: Record<string, string> = { chat: "chat spike", replayed: "most replayed", loud: "loud" };
const STATUS_CHIP: Record<string, string> = {
  queued: "", analyzing: "chip-info", ready: "chip-accent", rendering: "chip-info", done: "chip-ok", failed: "chip-live",
};

function sourceAt(job: LinkJob, t: number) {
  const url = job.info.webpage_url || job.url;
  return `${url}${url.includes("?") ? "&" : "?"}t=${Math.max(0, Math.floor(t))}`;
}

function Timeline({ job, picked, onToggle }: { job: LinkJob; picked: Set<number>; onToggle: (i: number) => void }) {
  const D = job.duration || Math.max(1, ...job.candidates.map((c) => c.end));
  return (
    <div className={styles.timeline} aria-label="Candidate moments along the video">
      <div className={styles.track} />
      {job.candidates.map((c, i) => (
        <button key={i} type="button" title={`${hms(c.moment)} · ${c.details.join(", ")}`}
                className={`${styles.mark} ${picked.has(i) ? styles.markOn : ""}`}
                style={{ left: `${(c.moment / D) * 100}%`, height: `${14 + c.score * 26}px`,
                         background: `var(--${c.sources[0]})` }}
                onClick={() => onToggle(i)} aria-label={`Moment at ${hms(c.moment)}`} />
      ))}
      <div className={styles.ticks}>
        {[0, 0.25, 0.5, 0.75, 1].map((f) => <span key={f} style={{ left: `${f * 100}%` }}>{hms(D * f)}</span>)}
      </div>
    </div>
  );
}

function CandidateRow({ c, i, job, on, onToggle, onEdit }: {
  c: Candidate; i: number; job: LinkJob; on: boolean; onToggle: () => void; onEdit: (start: number, end: number) => void;
}) {
  const [s, setS] = useState(hms(c.start));
  const [e, setE] = useState(hms(c.end));
  const commit = () => {
    const a = parseTime(s), b = parseTime(e);
    if (!Number.isNaN(a) && !Number.isNaN(b) && b > a) onEdit(a, b); else { setS(hms(c.start)); setE(hms(c.end)); }
  };
  return (
    <tr className={on ? styles.rowOn : ""}>
      <td><input type="checkbox" checked={on} onChange={onToggle} aria-label={`Pick moment ${i + 1}`} /></td>
      <td className="mono"><a href={sourceAt(job, c.start)} target="_blank" rel="noreferrer" title="Open the source here">{hms(c.moment)}</a></td>
      <td>
        <div className={styles.window}>
          <input className="mono" value={s} onChange={(ev) => setS(ev.target.value)} onBlur={commit} aria-label="Start" />
          <span className="faint">–</span>
          <input className="mono" value={e} onChange={(ev) => setE(ev.target.value)} onBlur={commit} aria-label="End" />
          <span className="faint small">{Math.round(c.end - c.start)}s</span>
        </div>
      </td>
      <td>
        <div className={styles.score}><i style={{ width: `${Math.round(c.score * 100)}%` }} /></div>
      </td>
      <td>
        <div className="row" style={{ gap: 4 }}>
          {c.sources.map((src, k) => (
            <span key={k} className="chip" style={{ color: `var(--${src})` }} title={c.details[k]}>{SOURCE_LABEL[src] ?? src}</span>
          ))}
        </div>
        {c.top_messages?.length ? (
          <div className="faint small" style={{ marginTop: 3 }}>{c.top_messages.slice(0, 3).map(([t]) => t).join(" · ")}</div>
        ) : null}
      </td>
    </tr>
  );
}

function JobDetail({ job, onChanged, flash, hasFacecam }: {
  job: LinkJob; onChanged: () => void; flash: (t: string, k?: "ok" | "err") => void; hasFacecam: boolean;
}) {
  const [layout, setLayout] = useState<"full" | "streamer">("full");
  // Preselect the strongest moments. JobDetail is keyed by job + candidate count, so this reruns per analysis.
  const [picked, setPicked] = useState<Set<number>>(() => new Set(
    job.candidates.map((c, i) => [c.score, i] as const).filter(([sc]) => sc >= 0.5).map(([, i]) => i).slice(0, 5)));
  const [edits, setEdits] = useState<Record<number, { start: number; end: number }>>({});
  const [custom, setCustom] = useState({ start: "", end: "", label: "" });
  const [busy, setBusy] = useState(false);
  const { data: clips } = usePoll(() => api.clips({ source: "link" }), 5000, [job.id]);
  const mine = useMemo(() => (clips ?? []).filter((c) => c.link_job_id === job.id), [clips, job.id]);

  const cands = job.candidates.map((c, i) => (edits[i] ? { ...c, ...edits[i] } : c));
  const toggle = (i: number) => setPicked((p) => { const n = new Set(p); if (n.has(i)) n.delete(i); else n.add(i); return n; });
  const analyzing = job.status === "queued" || job.status === "analyzing";

  const render = async (items: { start: number; end: number; label?: string; candidate?: number }[]) => {
    setBusy(true);
    try {
      const r = await api.renderLink(job.id, items, layout);
      flash(`Rendering ${r.queued} clip${r.queued === 1 ? "" : "s"}. Live clips still go first.`);
      onChanged();
    } catch (e) {
      flash(e instanceof Error ? e.message : String(e), "err");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={styles.detail}>
      <div className={styles.head}>
        {job.info.thumbnail && (
          // eslint-disable-next-line @next/next/no-img-element -- remote video thumbnail
          <img src={job.info.thumbnail} alt="" className={styles.poster} />
        )}
        <div className="grow">
          <h2 className={styles.jobTitle}>{job.title || job.url}</h2>
          <div className="faint small">
            {job.info.uploader && <>{job.info.uploader} · </>}
            {job.info.extractor && <>{job.info.extractor.replace(/:.*/, "")} · </>}
            {job.duration ? duration(job.duration) : ""}
            {job.info.was_live && " · past livestream"}
          </div>
          <div className="row" style={{ marginTop: 8 }}>
            <span className={`chip ${STATUS_CHIP[job.status]}`}>{job.status}</span>
            <span className="small muted">{job.message}</span>
          </div>
          {analyzing && (
            <div className={styles.progress}><i style={{ width: `${Math.round(job.progress * 100)}%` }} /></div>
          )}
        </div>
        <div className="row">
          <a className="btn btn-sm" href={job.info.webpage_url || job.url} target="_blank" rel="noreferrer">Open source</a>
          {job.status === "failed" && <button type="button" className="btn btn-sm" onClick={async () => { await api.retryLink(job.id); onChanged(); }}>Retry</button>}
        </div>
      </div>

      {job.info.signals && (
        <div className="row small">
          <span className="faint">Found:</span>
          {"chat" in job.info.signals && <span className="chip" style={{ color: "var(--chat)" }}>{job.info.signals.chat} chat spikes{job.info.chat_messages ? ` from ${job.info.chat_messages.toLocaleString()} messages` : ""}{job.info.chat_source ? ` · ${job.info.chat_source}` : ""}</span>}
          {job.info.chat_pending ? (
            <span className="chip chip-info" title="Long streams take a while; chat spikes are added when this finishes. You can render meanwhile.">
              reading chat replay {Math.round((job.info.chat_progress ?? 0) * 100)}%
            </span>
          ) : !("chat" in job.info.signals) && (
            <span className="chip" title="Chat is used when this app recorded the stream live, or the platform offers chat replay">no chat available</span>
          )}
          <span className="chip" style={{ color: "var(--replayed)" }}>{job.info.signals.replayed ?? 0} most-replayed peaks{job.info.has_heatmap ? "" : " (not available)"}</span>
          <span className="chip" style={{ color: "var(--loud)" }}
                title={job.info.loud_covered != null ? "The source was slow to read, so loudness stopped early. On YouTube, installing deno removes the throttling." : undefined}>
            {job.info.signals.loud ?? 0} loud moments{job.info.loud_covered != null ? ` (first ${Math.round(job.info.loud_covered * 100)}% only)` : ""}
          </span>
        </div>
      )}

      {job.candidates.length > 0 && (
        <>
          <Timeline job={{ ...job, candidates: cands }} picked={picked} onToggle={toggle} />
          <div className={styles.tableWrap}>
            <table className="table">
              <thead><tr><th /><th>Moment</th><th>Clip window</th><th>Strength</th><th>Why</th></tr></thead>
              <tbody>
                {cands.map((c, i) => (
                  <CandidateRow key={`${i}-${c.start}-${c.end}`} c={c} i={i} job={job} on={picked.has(i)} onToggle={() => toggle(i)}
                                onEdit={(start, end) => setEdits((p) => ({ ...p, [i]: { start, end } }))} />
                ))}
              </tbody>
            </table>
          </div>
          <div className="row">
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => setPicked(new Set(cands.map((_, i) => i)))}>Pick all</button>
            <button type="button" className="btn btn-sm btn-ghost" onClick={() => setPicked(new Set())}>Clear</button>
            <span className="grow" />
            <label className="row small" style={{ gap: 6 }}>
              <span className="muted">Vertical layout</span>
              <select value={layout} onChange={(e) => setLayout(e.target.value as "full" | "streamer")} style={{ width: "auto" }}>
                <option value="full">Full frame</option>
                <option value="streamer" disabled={!hasFacecam}>Streamer&apos;s facecam layout{hasFacecam ? "" : " (none saved)"}</option>
              </select>
            </label>
            <button type="button" className="btn btn-primary" disabled={busy || analyzing || !picked.size}
                    onClick={() => render([...picked].sort((a, b) => a - b).map((i) => ({ start: cands[i].start, end: cands[i].end, candidate: i })))}>
              Render {picked.size} clip{picked.size === 1 ? "" : "s"}
            </button>
          </div>
        </>
      )}
      {!analyzing && job.status !== "failed" && job.candidates.length === 0 && (
        <div className="empty">No standout moments found automatically. Set your own start and end below.</div>
      )}

      <form className={styles.custom} onSubmit={(e) => {
        e.preventDefault();
        const a = parseTime(custom.start), b = parseTime(custom.end);
        if (Number.isNaN(a) || Number.isNaN(b) || b <= a) { flash("Use times like 1:23:45 or 12:30, end after start", "err"); return; }
        render([{ start: a, end: b, label: custom.label }]);
      }}>
        <h3>Your own clip</h3>
        <div className={styles.customRow}>
          <label className="field"><span>Start</span><input className="mono" placeholder="1:02:30" value={custom.start} onChange={(e) => setCustom({ ...custom, start: e.target.value })} /></label>
          <label className="field"><span>End</span><input className="mono" placeholder="1:03:05" value={custom.end} onChange={(e) => setCustom({ ...custom, end: e.target.value })} /></label>
          <label className="field grow"><span>Note <span className="hint">optional</span></span><input value={custom.label} onChange={(e) => setCustom({ ...custom, label: e.target.value })} placeholder="what happens" /></label>
          <button type="submit" className="btn" disabled={busy || analyzing}>Render</button>
        </div>
        <p className="faint small" style={{ margin: 0 }}>3 seconds to 3 minutes. Only that part of the video is downloaded.</p>
      </form>

      {mine.length > 0 && (
        <div>
          <h3 style={{ marginBottom: 10 }}>Clips from this link ({mine.length})</h3>
          <div className={styles.made}>{mine.map((c) => <ClipThumb key={c.id} clip={c} href={`/clips#${c.id}`} />)}</div>
        </div>
      )}

      <div className={styles.footer}>
        <span className="faint small">Permission: {job.permission || "from streamer"}</span>
        <button type="button" className="btn btn-sm btn-ghost btn-danger" onClick={async () => {
          if (!confirm("Remove this link? Clips made from it stay.")) return;
          await api.deleteLink(job.id);
          onChanged();
        }}>Remove link</button>
      </div>
    </div>
  );
}

export default function LinksClipper() {
  const { data: jobs, reload } = usePoll(api.links, 2500);
  const { data: streamers } = usePoll(api.streamers, 30000);
  const [url, setUrl] = useState("");
  const [who, setWho] = useState<string>("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [picked, setSelected] = useState<number | null>(null);
  const { msg, flash } = useFlash();

  const permitted = (streamers ?? []).filter((s) => s.permission.trim());
  const selected = picked ?? jobs?.[0]?.id ?? null;
  const current = jobs?.find((j) => j.id === selected);

  return (
    <>
      <form className={`card card-pad ${styles.add}`} onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        try {
          const j = await api.addLink(url.trim(), who && who !== "other" ? Number(who) : null, who === "other" ? note : "");
          setUrl(""); setNote("");
          setSelected(j.id);
          flash("Added. Reading the video, chat replay and loudness…");
          reload();
        } catch (ex) {
          flash(ex instanceof Error ? ex.message : String(ex), "err");
        } finally {
          setBusy(false);
        }
      }}>
        <label className="field grow">
          <span>Video link <span className="hint">YouTube video or past stream, Twitch VOD, Kick VOD, and most other sites</span></span>
          <input required type="url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://www.youtube.com/watch?v=…" />
        </label>
        <label className="field">
          <span>Streamer</span>
          <select required value={who} onChange={(e) => setWho(e.target.value)}>
            <option value="" disabled>Pick who gave permission</option>
            {permitted.map((s) => <option key={s.id} value={s.id}>{s.display_name} ({PLATFORM_LABEL[s.platform]})</option>)}
            <option value="other">Someone else…</option>
          </select>
        </label>
        {who === "other" && (
          <label className="field grow">
            <span>Permission note <span className="hint">required</span></span>
            <input required value={note} onChange={(e) => setNote(e.target.value)} placeholder="Who agreed, how, when" />
          </label>
        )}
        <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Adding…" : "Find clips"}</button>
      </form>

      <div className={styles.layout}>
        <div className={styles.jobs}>
          {jobs?.map((j) => (
            <button key={j.id} type="button" className={`${styles.job} ${j.id === selected ? styles.jobOn : ""}`} onClick={() => setSelected(j.id)}>
              <div className="row" style={{ justifyContent: "space-between" }}>
                <span className={`chip ${STATUS_CHIP[j.status]}`}>{j.status}</span>
                <span className="faint small">{ago(j.created)}</span>
              </div>
              <div className={styles.jobName}>{j.title || j.url}</div>
              <div className="faint small">
                {j.candidates.length ? `${j.candidates.length} moments` : j.message}
                {j.clip_ids.length ? ` · ${j.clip_ids.length} clips` : ""}
              </div>
              {(j.status === "analyzing" || j.status === "queued") && (
                <div className={styles.progress}><i style={{ width: `${Math.round(j.progress * 100)}%` }} /></div>
              )}
            </button>
          ))}
          {jobs && !jobs.length && <div className="empty">Paste a link above to get started.</div>}
        </div>
        <div className="card">
          {current ? <JobDetail key={`${current.id}-${current.candidates.length}`} job={current}
                                hasFacecam={!!streamers?.find((s) => s.id === current.streamer_id)?.facecam} onChanged={reload} flash={flash} />
            : <div className="muted" style={{ padding: 20 }}>Pick a link.</div>}
        </div>
      </div>
      {msg && <div className={`toast ${msg.kind}`} role="status">{msg.text}</div>}
    </>
  );
}
