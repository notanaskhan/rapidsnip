"use client";

// Drag-to-set facecam and gameplay boxes on a real frame, with a live 9:16 preview.
// Boxes are in 1920x1080 source pixels, the units clipper/layout.py uses.

import { useCallback, useEffect, useRef, useState } from "react";
import { api, type Box, type Streamer } from "@/lib/api";
import styles from "./LayoutEditor.module.css";

const SRC_W = 1920;
const SRC_H = 1080;
const OUT_W = 1080;
const OUT_H = 1920;
const TOP_H = Math.round((OUT_H * 0.35) / 2) * 2;   // 672: facecam band (matches layout.py)
const BOT_H = OUT_H - TOP_H;                          // 1248: gameplay band
const FONT = Math.round(OUT_W * 0.074);               // caption size at 1080 wide
const MIN = 40;

type Which = "facecam" | "gameplay";
type Drag =
  | { kind: "draw"; which: Which; x0: number; y0: number }
  | { kind: "move"; which: Which; dx: number; dy: number }
  | { kind: "resize"; which: Which; ax: number; ay: number };

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));
const even = (v: number) => Math.round(v / 2) * 2;

function norm(x1: number, y1: number, x2: number, y2: number): Box {
  const x = clamp(Math.min(x1, x2), 0, SRC_W - MIN);
  const y = clamp(Math.min(y1, y2), 0, SRC_H - MIN);
  const w = clamp(Math.abs(x2 - x1), MIN, SRC_W - x);
  const h = clamp(Math.abs(y2 - y1), MIN, SRC_H - y);
  return [even(x), even(y), even(w), even(h)];
}

/** Draw the part of `box` that "cover" scaling keeps for a WxH target (centre crop, no stretch). */
function drawCover(ctx: CanvasRenderingContext2D, img: HTMLImageElement, box: Box,
                   dx: number, dy: number, W: number, H: number) {
  const [bx, by, bw, bh] = box;
  let sx = bx, sy = by, sw = bw, sh = bh;
  if (bw / bh > W / H) { sw = bh * (W / H); sx = bx + (bw - sw) / 2; }
  else { sh = bw * (H / W); sy = by + (bh - sh) / 2; }
  const k = img.naturalWidth / SRC_W;
  ctx.drawImage(img, sx * k, sy * k, sw * k, sh * k, dx, dy, W, H);
}

function Preview({ img, facecam, gameplay }: { img: HTMLImageElement | null; facecam: Box | null; gameplay: Box | null }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    const c = ref.current;
    if (!c || !img) return;
    const ctx = c.getContext("2d")!;
    const s = c.width / OUT_W;
    const game: Box = gameplay ?? [0, 0, SRC_W, SRC_H];
    ctx.save();
    ctx.scale(s, s);
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, OUT_W, OUT_H);
    let capY: number;
    if (facecam) {
      drawCover(ctx, img, facecam, 0, 0, OUT_W, TOP_H);
      drawCover(ctx, img, game, 0, TOP_H, OUT_W, BOT_H);
      capY = TOP_H + Math.round(FONT * 1.1);
    } else {
      ctx.filter = "blur(18px) brightness(0.88)";
      drawCover(ctx, img, game, -40, -40, OUT_W + 80, OUT_H + 80);
      ctx.filter = "none";
      const fgH = even((OUT_W * game[3]) / game[2]);
      const fgY = Math.floor((OUT_H - fgH) / 2);
      const k = img.naturalWidth / SRC_W;
      ctx.drawImage(img, game[0] * k, game[1] * k, game[2] * k, game[3] * k, 0, fgY, OUT_W, fgH);
      capY = fgY + fgH + Math.round(FONT * 1.4);
    }
    // Caption sample, where captions will sit
    ctx.font = `900 ${FONT}px "Arial Black", Arial, sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    ctx.lineJoin = "round";
    ctx.lineWidth = Math.max(5, Math.floor(FONT / 9)) * 2;
    ctx.strokeStyle = "#000";
    ctx.strokeText("CAPTIONS HERE", OUT_W / 2, capY);
    ctx.fillStyle = "#fff";
    ctx.fillText("CAPTIONS HERE", OUT_W / 2, capY);
    ctx.restore();
  }, [img, facecam, gameplay]);
  return <canvas ref={ref} width={270} height={480} className={styles.preview} aria-label="Vertical preview" />;
}

type FrameResult = { url: string; source: string } | { error: string };

async function getFrame(id: number, source: string): Promise<FrameResult> {
  try {
    const res = await fetch(`/api/streamers/${id}/frame?source=${source}&t=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      return { error: body.detail ?? `Couldn't get a frame (${res.status})` };
    }
    return { url: URL.createObjectURL(await res.blob()), source: res.headers.get("X-Frame-Source") ?? "" };
  } catch {
    return { error: "Can't reach the clipper API" };
  }
}

interface Props {
  streamer: Streamer;
  onClose: () => void;
  onSaved: (s: Streamer) => void;
}

export default function LayoutEditor({ streamer, onClose, onSaved }: Props) {
  const [facecam, setFacecam] = useState<Box | null>(streamer.facecam);
  const [gameplay, setGameplay] = useState<Box | null>(streamer.gameplay);
  const [active, setActive] = useState<Which>(streamer.facecam || !streamer.gameplay ? "facecam" : "gameplay");
  const [frameUrl, setFrameUrl] = useState<string | null>(null);
  const [frameSource, setFrameSource] = useState("");
  const [frameErr, setFrameErr] = useState("");
  const [loading, setLoading] = useState(true);
  const [img, setImg] = useState<HTMLImageElement | null>(null);
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const stage = useRef<HTMLDivElement>(null);
  const drag = useRef<Drag | null>(null);

  const apply = useCallback((r: FrameResult) => {
    if ("error" in r) {
      setFrameErr(r.error);
    } else {
      setFrameErr("");
      setFrameSource(r.source);
      setFrameUrl((old) => { if (old) URL.revokeObjectURL(old); return r.url; });
      const im = new Image();
      im.onload = () => setImg(im);
      im.src = r.url;
    }
    setLoading(false);
  }, []);

  const load = (source = "auto") => {
    setLoading(true);
    getFrame(streamer.id, source).then(apply);
  };

  useEffect(() => {
    let alive = true;
    getFrame(streamer.id, "auto").then((r) => { if (alive) apply(r); });
    return () => { alive = false; };
  }, [streamer.id, apply]);

  const setBox = (which: Which, b: Box | null) => (which === "facecam" ? setFacecam(b) : setGameplay(b));
  const boxOf = (which: Which) => (which === "facecam" ? facecam : gameplay);

  const toSrc = (e: { clientX: number; clientY: number }) => {
    const r = stage.current!.getBoundingClientRect();
    return [clamp(((e.clientX - r.left) / r.width) * SRC_W, 0, SRC_W), clamp(((e.clientY - r.top) / r.height) * SRC_H, 0, SRC_H)];
  };

  const onDown = (e: React.PointerEvent, target: { which: Which; part: "body" | "nw" | "ne" | "sw" | "se" } | null) => {
    if (e.button !== 0) return;
    e.preventDefault();
    e.stopPropagation();
    stage.current!.setPointerCapture(e.pointerId);
    const [x, y] = toSrc(e);
    // Inside the selected box: move it. Anywhere else (even over the other box): draw the selected one.
    // The other box is picked by its label or a corner handle, so a big gameplay box never blocks drawing.
    if (!target || (target.part === "body" && target.which !== active)) {
      drag.current = { kind: "draw", which: active, x0: x, y0: y };
      setBox(active, norm(x, y, x + MIN, y + MIN));
      return;
    }
    setActive(target.which);
    const b = boxOf(target.which)!;
    if (target.part === "body") {
      drag.current = { kind: "move", which: target.which, dx: x - b[0], dy: y - b[1] };
    } else {
      const ax = target.part.includes("w") ? b[0] + b[2] : b[0];
      const ay = target.part.includes("n") ? b[1] + b[3] : b[1];
      drag.current = { kind: "resize", which: target.which, ax, ay };
    }
  };

  const onMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const [x, y] = toSrc(e);
    if (d.kind === "draw") setBox(d.which, norm(d.x0, d.y0, x, y));
    else if (d.kind === "resize") setBox(d.which, norm(d.ax, d.ay, x, y));
    else {
      const b = boxOf(d.which)!;
      setBox(d.which, [even(clamp(x - d.dx, 0, SRC_W - b[2])), even(clamp(y - d.dy, 0, SRC_H - b[3])), b[2], b[3]]);
    }
  };
  const onUp = () => { drag.current = null; };

  // Arrow keys nudge the active box (Shift = 10 px).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { onClose(); return; }
      if (e.target instanceof HTMLInputElement) return;
      const step = e.shiftKey ? 10 : 2;
      const delta: Record<string, [number, number]> = { ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step] };
      const dv = delta[e.key];
      const b = active === "facecam" ? facecam : gameplay;
      if (!dv || !b) return;
      e.preventDefault();
      setBox(active, [clamp(b[0] + dv[0], 0, SRC_W - b[2]), clamp(b[1] + dv[1], 0, SRC_H - b[3]), b[2], b[3]]);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  const fitFacecam = () => {
    if (!facecam) return;
    const [x, y, w, h] = facecam;
    const target = OUT_W / TOP_H;               // band shape, so nothing gets cropped away
    let nw = w, nh = h;
    if (w / h > target) nw = h * target; else nh = w / target;
    const cx = x + w / 2, cy = y + h / 2;
    setFacecam(norm(cx - nw / 2, cy - nh / 2, cx + nw / 2, cy + nh / 2));
  };

  const save = async () => {
    setSaving(true);
    setErr("");
    try {
      const s = await api.updateStreamer(streamer.id, {
        facecam: facecam ?? undefined, gameplay: gameplay ?? undefined,
        clear_facecam: !facecam, clear_gameplay: !gameplay,
      });
      onSaved(s);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  const pct = (b: Box) => ({
    left: `${(b[0] / SRC_W) * 100}%`, top: `${(b[1] / SRC_H) * 100}%`,
    width: `${(b[2] / SRC_W) * 100}%`, height: `${(b[3] / SRC_H) * 100}%`,
  });

  const renderBox = (which: Which, b: Box | null) => b && (
    <div className={`${styles.box} ${styles[which]} ${active === which ? styles.on : ""}`} style={pct(b)}
         onPointerDown={(e) => onDown(e, { which, part: "body" })}>
      <span className={styles.tag} onPointerDown={(e) => { e.stopPropagation(); setActive(which); }}>
        {which === "facecam" ? "Facecam" : "Gameplay"}
      </span>
      {(["nw", "ne", "sw", "se"] as const).map((h) => (
        <span key={h} className={`${styles.handle} ${styles[h]}`} onPointerDown={(e) => onDown(e, { which, part: h })} />
      ))}
    </div>
  );

  const editBox = active === "facecam" ? facecam : gameplay;
  const setField = (i: number, v: string) => {
    const n = Number(v);
    if (!editBox || Number.isNaN(n)) return;
    const b = [...editBox] as Box;
    b[i] = Math.round(n);
    setBox(active, norm(b[0], b[1], b[0] + b[2], b[1] + b[3]));
  };

  return (
    <div className={styles.backdrop} role="dialog" aria-modal="true" aria-label={`Layout for ${streamer.display_name}`}
         onPointerDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={styles.dialog}>
        <header className={styles.head}>
          <div>
            <h2>Layout · {streamer.display_name}</h2>
            <p className="faint small">
              Drag on the frame to draw the selected box. Drag inside it to move, pull a corner to resize, arrow keys to nudge.
              Click a box&apos;s label to select it.
            </p>
          </div>
          <button type="button" className="btn btn-ghost" onClick={onClose} aria-label="Close">✕</button>
        </header>

        <div className={styles.body}>
          <div className={styles.left}>
            <div className={styles.toolbar}>
              <div className="row" role="group" aria-label="Box to draw">
                <button type="button" className={`btn btn-sm ${active === "facecam" ? "active" : ""}`} onClick={() => setActive("facecam")}>
                  <i className={styles.dotFace} /> Facecam
                </button>
                <button type="button" className={`btn btn-sm ${active === "gameplay" ? "active" : ""}`} onClick={() => setActive("gameplay")}>
                  <i className={styles.dotGame} /> Gameplay
                </button>
              </div>
              <span className="grow" />
              <span className="faint small">{loading ? "Grabbing a frame…" : frameSource && `Frame from ${frameSource}`}</span>
              <select className={styles.sourceSel} defaultValue="auto" onChange={(e) => load(e.target.value)} aria-label="Frame source">
                <option value="auto">Best frame</option>
                <option value="recording">From recording</option>
                <option value="stream">From live stream</option>
                <option value="clip">From latest clip</option>
              </select>
              <button type="button" className="btn btn-sm" onClick={() => load()} disabled={loading}>New frame</button>
            </div>

            <div className={styles.stageWrap}>
              {frameUrl ? (
                <div ref={stage} className={styles.stage} onPointerDown={(e) => onDown(e, null)}
                     onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}>
                  {/* eslint-disable-next-line @next/next/no-img-element -- blob URL of a frame */}
                  <img src={frameUrl} alt="Stream frame" draggable={false} />
                  {renderBox("gameplay", gameplay)}
                  {renderBox("facecam", facecam)}
                </div>
              ) : (
                <div className={styles.noFrame}>{loading ? "Grabbing a frame…" : frameErr || "No frame"}</div>
              )}
            </div>

            <div className={styles.controls}>
              <div className={styles.fields}>
                <span className="small muted">{active === "facecam" ? "Facecam" : "Gameplay"}</span>
                {["x", "y", "w", "h"].map((k, i) => (
                  <label key={k} className={styles.num}>
                    <span>{k}</span>
                    <input type="number" value={editBox ? editBox[i] : ""} disabled={!editBox}
                           onChange={(e) => setField(i, e.target.value)} />
                  </label>
                ))}
              </div>
              <div className="row">
                {active === "facecam" ? (
                  <>
                    <button type="button" className="btn btn-sm" disabled={!facecam} onClick={fitFacecam}
                            title="Match the facecam band's shape so nothing is cropped">Fit to band</button>
                    <button type="button" className="btn btn-sm btn-ghost" disabled={!facecam} onClick={() => setFacecam(null)}>No facecam</button>
                  </>
                ) : (
                  <button type="button" className="btn btn-sm btn-ghost" disabled={!gameplay} onClick={() => setGameplay(null)}>Use full frame</button>
                )}
              </div>
            </div>
          </div>

          <aside className={styles.right}>
            <Preview img={img} facecam={facecam} gameplay={gameplay} />
            <p className="faint small" style={{ margin: 0, textAlign: "center" }}>
              {facecam ? "Facecam on top, gameplay below" : "No facecam: gameplay over a blurred copy"}
            </p>
          </aside>
        </div>

        <footer className={styles.foot}>
          <span className="faint small mono">
            facecam {facecam ? `[${facecam.join(", ")}]` : "none"} · gameplay {gameplay ? `[${gameplay.join(", ")}]` : "full frame"}
          </span>
          <span className="grow" />
          {err && <span style={{ color: "var(--live)" }} className="small">{err}</span>}
          <button type="button" className="btn btn-ghost" onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-primary" onClick={save} disabled={saving}>{saving ? "Saving…" : "Save layout"}</button>
        </footer>
      </div>
    </div>
  );
}
