import { NextResponse, type NextRequest } from "next/server";

// Everything the browser asks of /api and /media goes to the clipper API on the Mac.
// Hosted (Vercel): the Mac is reached through a tunnel. The browser must hold a session cookie
// (from /login), and the server adds the shared key the Mac checks. The key never reaches the browser.
const API = process.env.CLIPPER_API ?? "http://127.0.0.1:8000";
const KEY = process.env.CLIPPER_KEY ?? "";
const PASSWORD = process.env.CLIPPER_PASSWORD ?? "";
export const SESSION_COOKIE = "clipper_session";

export async function sessionToken(): Promise<string> {
  const enc = new TextEncoder();
  const k = await crypto.subtle.importKey("raw", enc.encode(`${PASSWORD}\u0000${KEY}`),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const sig = await crypto.subtle.sign("HMAC", k, enc.encode("clipper-session-v1"));
  return Array.from(new Uint8Array(sig), (b) => b.toString(16).padStart(2, "0")).join("");
}

export async function proxy(req: NextRequest) {
  const { pathname, search } = req.nextUrl;
  if (pathname === "/login" || pathname.startsWith("/auth/")) return NextResponse.next();

  if (PASSWORD && req.cookies.get(SESSION_COOKIE)?.value !== (await sessionToken())) {
    if (pathname.startsWith("/api/") || pathname.startsWith("/media/")) {
      return NextResponse.json({ detail: "Sign in first" }, { status: 401 });
    }
    const login = new URL("/login", req.url);
    login.searchParams.set("next", pathname + search);
    return NextResponse.redirect(login);
  }

  if (pathname.startsWith("/api/") || pathname.startsWith("/media/")) {
    const headers = new Headers(req.headers);
    headers.delete("cookie");                              // the Mac doesn't need the session
    if (KEY) headers.set("x-clipper-key", KEY);
    headers.set("ngrok-skip-browser-warning", "1");        // ngrok's free tier interstitial
    return NextResponse.rewrite(new URL(pathname + search, API), { request: { headers } });
  }
  return NextResponse.next();
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
