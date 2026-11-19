// Typed client for the Python API (clipper serve). All paths are proxied by next.config.ts.

export type Platform = "twitch" | "youtube" | "kick";
export type ClipStatus = "new" | "approved" | "posted" | "rejected";
export type Box = [number, number, number, number];

export interface ChannelStatus {
  name: string;
  display_name: string;
  platform: Platform;
  live: boolean;
  since?: number;
  warmup?: boolean;
  buffer_seconds?: number;
  restarts?: number;
  score?: number;
  baseline?: number;
  mad?: number;
  threshold?: number;
  ratio_threshold?: number;
  paused?: boolean;
  clips?: number;
  dropped?: number;
  last_trigger?: { moment: number; ratio: number; final: boolean } | null;
  history?: [number, number, number][]; // [time, score, baseline]
  chat_connected?: boolean;
}

export interface Status {
  running: boolean;
  live_recording: boolean;
  started_at: number;
  chat: { connected: boolean; reconnects: number };
  queue: number;
  working_on: { channel: string; source: string } | null;
  clips_this_run: number;
  channels: ChannelStatus[];
  disk: { free_gb: number; total_gb: number; min_free_gb: number };
  clips: Partial<Record<ClipStatus, number>>;
  now: number;
}

export interface Streamer {
  id: number;
  platform: Platform;
  login: string;
  display_name: string;
  permission: string;
  credit_url: string;
  enabled: boolean;
  facecam: Box | null;
  gameplay: Box | null;
  overrides: Record<string, number>;
  live: boolean;
  recordable: boolean;
  note: string;
}

export interface Clip {
  id: number;
  folder: string;
  channel: string;
  display_name: string;
  source: "live" | "link";
  link_job_id: number | null;
  moment: number;
  ratio: number;
  seconds: number;
  status: ClipStatus;
  title: string;
  description: string;
  created: number;
  credit_url: string;
  permission: string;
  files: Partial<Record<"vertical" | "horizontal" | "thumb", string>>;
  exists: boolean;
  top_messages: [string, number][];
  reaction_counts: Record<string, number>;
  transcript: string;
  timings: Record<string, number>;
  ready_after: number | null;
  source_url: string;
  source_title: string;
  source_offset: number | null;
  suggested_title: string;
}

export interface Candidate {
  moment: number;
  start: number;
  end: number;
  score: number;
  sources: ("chat" | "replayed" | "loud")[];
  details: string[];
  ratio?: number;
  top_messages?: [string, number][];
}

export type LinkStatus = "queued" | "analyzing" | "ready" | "rendering" | "done" | "failed";

export interface LinkJob {
  id: number;
  url: string;
  streamer_id: number | null;
  permission: string;
  status: LinkStatus;
  progress: number;
  message: string;
  title: string;
  duration: number;
  info: {
    uploader?: string;
    extractor?: string;
    webpage_url?: string;
    thumbnail?: string;
    was_live?: boolean;
    has_heatmap?: boolean;
    signals?: Record<string, number>;
    chat_messages?: number;
    chat_source?: string;
    chat_pending?: boolean;
    loud_covered?: number;
    chat_progress?: number;
    chat_status?: string;
  };
  candidates: Candidate[];
  clip_ids: number[];
  created: number;
}

export interface Settings {
  detector: Record<string, number | string[]>;
  clip: Record<string, number>;
  captions: { enabled: boolean; model: string; words_per_caption: number };
  layout: { enabled: boolean; output: [number, number] };
  buffer_minutes: number;
  quality: string;
  platforms: Platform[];
  live_platforms: Platform[];
  override_keys: string[];
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      ...init,
      headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, "Can't reach the clipper API. Is `uv run python -m clipper serve` running?");
  }
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  const body = text ? JSON.parse(text) : undefined;
  if (!res.ok) {
    const detail = body?.detail;
    const msg = typeof detail === "string" ? detail
      : Array.isArray(detail) ? detail.map((d: { msg: string }) => d.msg).join("; ")
      : `Request failed (${res.status})`;
    throw new ApiError(res.status, msg);
  }
  return body as T;
}

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  body: body === undefined ? undefined : JSON.stringify(body),
});

export const api = {
  status: () => call<Status>("/api/status"),
  settings: () => call<Settings>("/api/settings"),

  streamers: () => call<Streamer[]>("/api/streamers"),
  addStreamer: (s: Partial<Streamer>) => call<Streamer>("/api/streamers", json("POST", s)),
  updateStreamer: (id: number, s: Partial<Streamer> & { clear_facecam?: boolean; clear_gameplay?: boolean }) =>
    call<Streamer>(`/api/streamers/${id}`, json("PATCH", s)),
  deleteStreamer: (id: number) => call<void>(`/api/streamers/${id}`, json("DELETE")),

  clips: (q: { status?: string; channel?: string; source?: string } = {}) => {
    const params = new URLSearchParams(Object.entries(q).filter(([, v]) => v) as [string, string][]);
    return call<Clip[]>(`/api/clips?${params}`);
  },
  /** Setting status "rejected" deletes the clip everywhere after `delete_in` seconds unless undone. */
  updateClip: (id: number, c: Partial<Pick<Clip, "status" | "title" | "description">>) =>
    call<Clip & { delete_in: number | null }>(`/api/clips/${id}`, json("PATCH", c)),
  deleteClip: (id: number) => call<void>(`/api/clips/${id}`, json("DELETE")),
  downloadUrl: (id: number, kind: "vertical" | "horizontal" | "thumb") => `/api/clips/${id}/download/${kind}`,

  links: () => call<LinkJob[]>("/api/links"),
  link: (id: number) => call<LinkJob>(`/api/links/${id}`),
  addLink: (url: string, streamer_id: number | null, permission: string) =>
    call<LinkJob>("/api/links", json("POST", { url, streamer_id, permission })),
  renderLink: (id: number, items: { start: number; end: number; label?: string; candidate?: number }[],
               layout: "full" | "streamer" = "full") =>
    call<{ queued: number }>(`/api/links/${id}/render`, json("POST", { items, layout })),
  retryLink: (id: number) => call<LinkJob>(`/api/links/${id}/retry`, json("POST")),
  deleteLink: (id: number) => call<void>(`/api/links/${id}`, json("DELETE")),
};
