"use client";

import { useState } from "react";
import styles from "./login.module.css";

export default function LoginForm() {
  const [password, setPassword] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  return (
    <form className={`card ${styles.card}`} onSubmit={async (e) => {
      e.preventDefault();
      setBusy(true);
      setErr("");
      const res = await fetch("/auth/login", { method: "POST", headers: { "content-type": "application/json" },
                                               body: JSON.stringify({ password }) });
      if (res.ok) {
        const next = new URLSearchParams(window.location.search).get("next") || "/";
        window.location.assign(next.startsWith("/") ? next : "/");
      } else {
        setErr((await res.json().catch(() => ({}))).detail ?? "Couldn't sign in");
        setBusy(false);
      }
    }}>
      <div className={styles.brand}><span className={styles.logo} aria-hidden>▶</span> Clipper</div>
      <label className="field">
        <span>Password</span>
        <input type="password" autoFocus required autoComplete="current-password" value={password}
               onChange={(e) => setPassword(e.target.value)} />
      </label>
      {err && <div className={styles.err} role="alert">{err}</div>}
      <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
    </form>
  );
}
