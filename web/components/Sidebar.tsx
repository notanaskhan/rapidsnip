"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { api } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import styles from "./Sidebar.module.css";

const NAV = [
  { href: "/", label: "Live", icon: "M12 12m-3 0a3 3 0 1 0 6 0a3 3 0 1 0 -6 0M5.6 5.6a9 9 0 0 0 0 12.8M18.4 18.4a9 9 0 0 0 0-12.8" },
  { href: "/clips", label: "Clips", icon: "M4 5h16v14H4zM10 9l5 3-5 3z" },
  { href: "/links", label: "Links", icon: "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" },
  { href: "/streamers", label: "Streamers", icon: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4 20a8 8 0 0 1 16 0" },
  { href: "/settings", label: "Settings", icon: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z" },
];

export default function Sidebar() {
  const path = usePathname();
  const { data: status, error } = usePoll(api.status, 5000);
  const live = status?.channels.filter((c) => c.live).length ?? 0;
  const fresh = status?.clips.new ?? 0;

  return (
    <aside className={styles.sidebar}>
      <div className={styles.brand}>
        <span className={styles.logo} aria-hidden>▶</span>
        <span>Clipper</span>
      </div>
      <nav className={styles.nav}>
        {NAV.map((n) => {
          const on = n.href === "/" ? path === "/" : path.startsWith(n.href);
          return (
            <Link key={n.href} href={n.href} className={`${styles.item} ${on ? styles.on : ""}`}>
              <svg viewBox="0 0 24 24" width="17" height="17" aria-hidden><path d={n.icon} /></svg>
              <span>{n.label}</span>
              {n.href === "/" && live > 0 && <span className={styles.badgeLive}>{live}</span>}
              {n.href === "/clips" && fresh > 0 && <span className={styles.badge}>{fresh}</span>}
            </Link>
          );
        })}
      </nav>
      <div className={styles.foot}>
        {error ? (
          <span className={styles.down}>API offline</span>
        ) : status ? (
          <>
            <span className={status.live_recording ? styles.up : styles.idle}>
              {status.live_recording ? "Recording on" : "Recording off"}
            </span>
            <span>{status.disk.free_gb} GB free</span>
          </>
        ) : (
          <span>Connecting…</span>
        )}
      </div>
    </aside>
  );
}
