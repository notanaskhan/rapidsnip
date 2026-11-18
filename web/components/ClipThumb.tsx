import type { Clip } from "@/lib/api";
import { dayTime, hms } from "@/lib/format";
import styles from "./ClipThumb.module.css";

const STATUS_CHIP: Record<string, string> = {
  new: "chip-accent", approved: "chip-ok", posted: "chip-info", rejected: "",
};

interface Props {
  clip: Clip;
  selected?: boolean;
  onClick?: () => void;
  href?: string;
}

export default function ClipThumb({ clip, selected, onClick, href }: Props) {
  const body = (
    <>
      <div className={styles.media}>
        {clip.files.thumb ? (
          // eslint-disable-next-line @next/next/no-img-element -- local thumbnails, already small
          <img src={clip.files.thumb} alt="" loading="lazy" />
        ) : (
          <div className={styles.noThumb}>no preview</div>
        )}
        <span className={styles.len}>{Math.round(clip.seconds)}s</span>
        {clip.source === "live" && clip.ratio > 0 && <span className={styles.ratio}>{clip.ratio.toFixed(1)}×</span>}
        {clip.source === "link" && <span className={styles.ratio}>link</span>}
      </div>
      <div className={styles.meta}>
        <div className={styles.top}>
          <strong className={styles.name}>{clip.display_name}</strong>
          <span className={`chip ${STATUS_CHIP[clip.status]}`}>{clip.status}</span>
        </div>
        <div className={styles.title}>{clip.title || clip.transcript || "Untitled"}</div>
        <div className="faint small">
          {clip.source === "link" && clip.source_offset != null
            ? `at ${hms(clip.source_offset)} · ${clip.source_title}`
            : dayTime(clip.moment)}
        </div>
      </div>
    </>
  );
  const cls = `${styles.thumb} ${selected ? styles.selected : ""} ${clip.status === "rejected" ? styles.dim : ""}`;
  if (href) return <a href={href} className={cls}>{body}</a>;
  return <button type="button" className={cls} onClick={onClick}>{body}</button>;
}
