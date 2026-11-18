"use client";

import { useState } from "react";
import { api, type Box, type Platform, type Streamer } from "@/lib/api";
import { useFlash, usePoll } from "@/lib/hooks";
import { PLATFORM_LABEL } from "@/lib/format";
import LayoutEditor from "@/components/LayoutEditor";
import styles from "./streamers.module.css";

const PLATFORMS: { key: Platform; live: boolean }[] = [
  { key: "twitch", live: true },
  { key: "youtube", live: false },
  { key: "kick", live: false },
];

const OVERRIDES: { key: string; label: string; hint: string; step: number }[] = [
  { key: "ratio_threshold", label: "Spike threshold", hint: "× normal chat (default 3). Higher = fewer clips", step: 0.1 },
  { key: "chat_delay_seconds", label: "Chat delay", hint: "seconds chat lags the video (default 6)", step: 1 },
  { key: "min_unique_chatters", label: "Min chatters", hint: "per 10 s window (default 8)", step: 1 },
  { key: "pre_seconds", label: "Before moment", hint: "seconds (default 20)", step: 1 },
  { key: "post_seconds", label: "After moment", hint: "seconds (default 10)", step: 1 },
];

type Draft = {
  platform: Platform; login: string; display_name: string; permission: string; credit_url: string;
  enabled: boolean; facecam: string; gameplay: string; overrides: Record<string, string>;
};

const emptyDraft: Draft = {
  platform: "twitch", login: "", display_name: "", permission: "", credit_url: "", enabled: true,
  facecam: "", gameplay: "", overrides: {},
};

function boxText(b: Box | null) { return b ? b.join(", ") : ""; }
function parseBox(t: string): Box | null | "bad" {
  if (!t.trim()) return null;
  const n = t.split(/[\s,]+/).filter(Boolean).map(Number);
  return n.length === 4 && n.every((x) => Number.isInteger(x) && x >= 0) ? (n as Box) : "bad";
}

function toDraft(s: Streamer): Draft {
  return {
    platform: s.platform, login: s.login, display_name: s.display_name, permission: s.permission,
    credit_url: s.credit_url, enabled: s.enabled, facecam: boxText(s.facecam), gameplay: boxText(s.gameplay),
    overrides: Object.fromEntries(Object.entries(s.overrides).map(([k, v]) => [k, String(v)])),
  };
}

function Form({ initial, onSubmit, onCancel, submitLabel, isNew }: {
  initial: Draft; onSubmit: (d: Draft) => Promise<void>; onCancel?: () => void; submitLabel: string; isNew?: boolean;
}) {
  const [d, setD] = useState<Draft>(initial);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setD((p) => ({ ...p, [k]: v }));
  const platform = PLATFORMS.find((p) => p.key === d.platform)!;

  return (
    <form className={styles.form} onSubmit={async (e) => {
      e.preventDefault();
      setErr("");
      if (parseBox(d.facecam) === "bad" || parseBox(d.gameplay) === "bad") {
        setErr("Layout boxes are four whole numbers: x, y, width, height");
        return;
      }
      setBusy(true);
      try { await onSubmit(d); } catch (ex) { setErr(ex instanceof Error ? ex.message : String(ex)); } finally { setBusy(false); }
    }}>
      <div className={styles.cols}>
        <label className="field">
          <span>Platform</span>
          <select value={d.platform} onChange={(e) => set("platform", e.target.value as Platform)}>
            {PLATFORMS.map((p) => <option key={p.key} value={p.key}>{PLATFORM_LABEL[p.key]}{p.live ? "" : " (links only for now)"}</option>)}
          </select>
        </label>
        <label className="field">
          <span>Channel <span className="hint">name or URL</span></span>
          <input required value={d.login} onChange={(e) => set("login", e.target.value)}
                 placeholder={d.platform === "youtube" ? "@handle or channel URL" : "e.g. kaicenat"} />
        </label>
        <label className="field">
          <span>Display name</span>
          <input value={d.display_name} onChange={(e) => set("display_name", e.target.value)} placeholder="Shown in titles" />
        </label>
      </div>
      {!platform.live && (
        <p className={styles.note}>Live recording on {PLATFORM_LABEL[d.platform]} isn&apos;t supported yet. You can save this streamer and clip their videos and past streams from Links.</p>
      )}
      <label className="field">
        <span>Permission <span className="hint">required: who agreed, how and when (e.g. &quot;yes, Discord DM 2026-11-18, screenshot in Drive&quot;)</span></span>
        <textarea rows={2} value={d.permission} onChange={(e) => set("permission", e.target.value)}
                  placeholder="Nothing is recorded or clipped for this streamer until this is filled in" />
      </label>
      <label className="field">
        <span>Credit link <span className="hint">goes in every description; defaults to their channel</span></span>
        <input value={d.credit_url} onChange={(e) => set("credit_url", e.target.value)} placeholder="https://…" />
      </label>

      <details className={styles.more} open={!isNew}>
        <summary>Tuning and layout</summary>
        <div className={styles.cols}>
          {OVERRIDES.map((o) => (
            <label key={o.key} className="field">
              <span>{o.label}</span>
              <input type="number" step={o.step} min={0} value={d.overrides[o.key] ?? ""} placeholder="default"
                     onChange={(e) => set("overrides", { ...d.overrides, [o.key]: e.target.value })} />
              <span className="hint">{o.hint}</span>
            </label>
          ))}
        </div>
        <div className={styles.cols2}>
          <label className="field">
            <span>Facecam box <span className="hint">x, y, w, h in 1920×1080 px · empty = no facecam</span></span>
            <input className="mono" value={d.facecam} onChange={(e) => set("facecam", e.target.value)} placeholder="0, 460, 388, 242" />
          </label>
          <label className="field">
            <span>Gameplay box <span className="hint">empty = full frame</span></span>
            <input className="mono" value={d.gameplay} onChange={(e) => set("gameplay", e.target.value)} placeholder="400, 0, 1300, 1080" />
          </label>
        </div>
        <p className="faint small" style={{ margin: 0 }}>
          Easier: save, then use the <strong>Layout</strong> button to draw the boxes on a frame from their stream.
        </p>
      </details>

      <div className="row">
        <label className="row small" style={{ gap: 6 }}>
          <input type="checkbox" checked={d.enabled} onChange={(e) => set("enabled", e.target.checked)} /> Enabled
        </label>
        <span className="grow" />
        {err && <span className={styles.err}>{err}</span>}
        {onCancel && <button type="button" className="btn btn-ghost" onClick={onCancel}>Cancel</button>}
        <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Saving…" : submitLabel}</button>
      </div>
    </form>
  );
}

function payload(d: Draft) {
  const overrides: Record<string, number> = {};
  for (const [k, v] of Object.entries(d.overrides)) if (v !== "" && !Number.isNaN(Number(v))) overrides[k] = Number(v);
  const facecam = parseBox(d.facecam);
  const gameplay = parseBox(d.gameplay);
  return {
    platform: d.platform, login: d.login, display_name: d.display_name || undefined, permission: d.permission,
    credit_url: d.credit_url || undefined, enabled: d.enabled, overrides,
    facecam: facecam === "bad" ? null : facecam, gameplay: gameplay === "bad" ? null : gameplay,
  };
}

export default function StreamersManager() {
  const { data, error, reload } = usePoll(api.streamers, 10000);
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<number | null>(null);
  const [layoutFor, setLayoutFor] = useState<Streamer | null>(null);
  const { msg, flash } = useFlash();

  return (
    <>
      <div className="row" style={{ marginBottom: 14 }}>
        <span className="grow" />
        {!adding && <button type="button" className="btn btn-primary" onClick={() => setAdding(true)}>+ Add streamer</button>}
      </div>
      {adding && (
        <div className="card card-pad" style={{ marginBottom: 16 }}>
          <h2 style={{ marginBottom: 12 }}>Add a streamer</h2>
          <Form initial={emptyDraft} submitLabel="Add streamer" isNew onCancel={() => setAdding(false)} onSubmit={async (d) => {
            const s = await api.addStreamer(payload(d));
            flash(s.recordable ? `${s.display_name} added. Recording starts when they go live.` : `${s.display_name} saved (${s.note})`);
            setAdding(false);
            reload();
          }} />
        </div>
      )}
      {error && <div className="error-box">{error}</div>}
      <div className={styles.list}>
        {data?.map((s) => (
          <div key={s.id} className="card">
            {editing === s.id ? (
              <div className="card-pad">
                <h2 style={{ marginBottom: 12 }}>Edit {s.display_name}</h2>
                <Form initial={toDraft(s)} submitLabel="Save" onCancel={() => setEditing(null)} onSubmit={async (d) => {
                  const p = payload(d);
                  await api.updateStreamer(s.id, { ...p, clear_facecam: p.facecam === null, clear_gameplay: p.gameplay === null });
                  flash("Saved. The recorder picked up the change.");
                  setEditing(null);
                  reload();
                }} />
              </div>
            ) : (
              <div className={styles.rowItem}>
                <div className={styles.who}>
                  <div className="row">
                    <strong>{s.display_name}</strong>
                    <span className="chip">{PLATFORM_LABEL[s.platform]}</span>
                    {s.live && <span className="chip chip-live">LIVE</span>}
                    {s.recordable ? <span className="chip chip-ok">recording when live</span>
                      : <span className="chip chip-warn">{s.note}</span>}
                  </div>
                  <div className="faint small">
                    {s.login} · <a href={s.credit_url} target="_blank" rel="noreferrer">{s.credit_url}</a>
                  </div>
                  <div className="small muted">Permission: {s.permission || <span style={{ color: "var(--live)" }}>missing</span>}</div>
                  <div className="faint small">
                    Layout: {s.facecam ? `facecam ${s.facecam.join(",")}` : "no facecam"} · {s.gameplay ? `gameplay ${s.gameplay.join(",")}` : "full frame"}
                    {Object.keys(s.overrides).length > 0 && <> · {Object.entries(s.overrides).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(" · ")}</>}
                  </div>
                </div>
                <div className="row">
                  <label className="row small" style={{ gap: 6 }} title="Turn recording on or off without deleting">
                    <input type="checkbox" checked={s.enabled} onChange={async (e) => {
                      await api.updateStreamer(s.id, { enabled: e.target.checked });
                      flash(e.target.checked ? "Enabled" : "Disabled; any recording for them stops now");
                      reload();
                    }} /> Enabled
                  </label>
                  <button type="button" className="btn btn-sm" onClick={() => setLayoutFor(s)}
                          title="Draw the facecam and gameplay boxes on a frame">Layout</button>
                  <button type="button" className="btn btn-sm" onClick={() => setEditing(s.id)}>Edit</button>
                  <button type="button" className="btn btn-sm btn-danger" onClick={async () => {
                    if (!confirm(`Remove ${s.display_name}? Their existing clips stay.`)) return;
                    await api.deleteStreamer(s.id);
                    flash("Removed");
                    reload();
                  }}>Remove</button>
                </div>
              </div>
            )}
          </div>
        ))}
        {data && !data.length && <div className="empty">No streamers yet. Add someone who agreed to be clipped.</div>}
      </div>
      {layoutFor && (
        <LayoutEditor streamer={layoutFor} onClose={() => setLayoutFor(null)} onSaved={(s) => {
          setLayoutFor(null);
          flash(`Layout saved for ${s.display_name}. New clips use it.`);
          reload();
        }} />
      )}
      {msg && <div className={`toast ${msg.kind}`} role="status">{msg.text}</div>}
    </>
  );
}
