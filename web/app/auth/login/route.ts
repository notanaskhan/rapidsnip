import { NextResponse } from "next/server";
import { SESSION_COOKIE, sessionToken } from "@/proxy";

export async function POST(req: Request) {
  const { password } = (await req.json().catch(() => ({}))) as { password?: string };
  const expected = process.env.CLIPPER_PASSWORD ?? "";
  if (!expected) return NextResponse.json({ ok: true });            // no password set (local)
  // Compare HMACs rather than the strings, so timing doesn't leak the password.
  const enc = new TextEncoder();
  const key = await crypto.subtle.importKey("raw", enc.encode("cmp"), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const [a, b] = await Promise.all([
    crypto.subtle.sign("HMAC", key, enc.encode(password ?? "")),
    crypto.subtle.sign("HMAC", key, enc.encode(expected)),
  ]);
  const same = Buffer.from(a).equals(Buffer.from(b));
  if (!same) {
    await new Promise((r) => setTimeout(r, 600));                     // slow down guessing
    return NextResponse.json({ detail: "Wrong password" }, { status: 401 });
  }
  const res = NextResponse.json({ ok: true });
  res.cookies.set(SESSION_COOKIE, await sessionToken(), {
    httpOnly: true, secure: process.env.NODE_ENV === "production", sameSite: "lax", path: "/",
    maxAge: 60 * 60 * 24 * 30,
  });
  return res;
}
