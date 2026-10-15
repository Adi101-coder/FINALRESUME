export const API_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(
  /\/+$/,
  "",
);

const RETRYABLE_STATUS = new Set([502, 503, 504]);
const RETRY_PAUSES_MS = [6_000, 12_000, 24_000];

class ApiError extends Error {}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function apiFetch(path: string, init?: RequestInit, timeoutMs = 120_000): Promise<Response> {
  let lastError: Error | undefined;

  for (let attempt = 0; attempt <= RETRY_PAUSES_MS.length; attempt += 1) {
    if (attempt > 0) {
      await sleep(RETRY_PAUSES_MS[attempt - 1]);
    }
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);

    try {
      const res = await fetch(`${API_URL}${path}`, {
        ...init,
        signal: controller.signal,
      });
      if (RETRYABLE_STATUS.has(res.status)) {
        // Backend unreachable / Render cold start — request was not processed.
        lastError = new Error(
          `Backend unavailable (${res.status}) — waiting for it to come up (attempt ${attempt + 1}/${RETRY_PAUSES_MS.length + 1})…`,
        );
        continue;
      }
      if (res.status === 404) {
        const detail = await res.text().catch(() => "");
        throw new ApiError(detail || "Resource not found. Please check your input and try again.");
      }
      if (res.status >= 500) {
        const detail = await res.text().catch(() => "");
        throw new ApiError(detail || `Server error (${res.status}). Please try again later.`);
      }
      return res;
    } catch (err) {
      if (err instanceof ApiError) {
        throw err;
      }
      if (err instanceof Error && err.name === "AbortError") {
        throw new Error(
          `Request timed out after ${timeoutMs / 1000}s. The backend may be waking up (free tier) — please wait a minute and retry.`,
        );
      }
      // Network-level failure (backend down or cold starting) — safe to retry.
      lastError = new Error(
        `Cannot connect to backend at ${API_URL} (retried ${attempt + 1}/${RETRY_PAUSES_MS.length + 1}×). The server may be waking up — if this keeps happening, check ${API_URL}/health.`,
      );
    } finally {
      clearTimeout(timer);
    }
  }

  throw lastError ?? new Error(`Request failed: ${path}`);
}

async function apiFetchWithRetry(
  path: string,
  init?: RequestInit,
  timeoutMs = 120_000,
  retries = 2,
): Promise<Response> {
  let lastError: Error | undefined;
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    try {
      return await apiFetch(path, init, timeoutMs);
    } catch (err) {
      lastError = err instanceof Error ? err : new Error(String(err));
      if (attempt < retries) {
        await new Promise((resolve) => setTimeout(resolve, 4000 * (attempt + 1)));
      }
    }
  }
  throw lastError ?? new Error(`Request failed: ${path}`);
}

export type CandidateProfile = {
  name: string;
  email: string;
  phone?: string;
  location?: string;
  notice_period?: string;
  preferred_roles?: string[];
  skills: string[];
  experience: Array<{ company: string; role: string; bullets: string[]; location?: string }>;
  projects: Array<{ name: string; description: string; technologies: string[] }>;
  education: Array<{ institution: string; degree: string; field?: string; cgpa?: string }>;
  certifications?: string[];
  linkedin: string;
  github: string;
  summary?: string;
};

export type MatchResult = {
  job_id: number;
  title: string;
  company: string;
  location: string;
  apply_url: string;
  score: number;
  relevance_score: number;
  skills_score: number;
  experience_score: number;
  embedding_score: number;
  location_score: number;
  passed_threshold: boolean;
};

export type Application = {
  id: number;
  candidate_id: number;
  job_id: number;
  status: string;
  applied_at: string | null;
  ats_score: number | null;
  notes: string;
  job_title: string;
  job_company: string;
  job_location: string;
  apply_url: string;
  match_score: number | null;
};

export type DiscoveryResult = {
  candidate_id: number;
  role?: string;
  search_queries: string[];
  search_location?: string;
  location_mode?: "all" | "preferred" | "resume";
  scraped: number;
  created: number;
  skipped_duplicates: number;
  candidate_jobs: number;
  total_jobs_in_db: number;
  total_available?: number;
  fresh?: boolean;
  platform_counts?: Record<string, { fetched: number; available: number; errors: number; error?: string }>;
  sources?: Record<string, { fetched: number; errors: number }>;
};

export type JobSource = {
  key: string;
  name: string;
  auth: string;
  status: string;
  enabled: boolean;
};

export function logClick(
  action: string,
  detail = "",
  meta: Record<string, string | number | boolean> = {},
  page = typeof window !== "undefined" ? window.location.pathname : "",
) {
  void apiFetch("/api/events/client", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, page, detail, meta }),
  }, 10_000).catch(() => {});
}

export async function checkBackendHealth(): Promise<boolean> {
  const res = await apiFetchWithRetry("/health", undefined, 20_000, 0);
  return res.ok;
}

export async function uploadResume(file: File) {
  logClick("upload_resume", file.name, { size: file.size });
  const form = new FormData();
  form.append("file", file);
  const res = await apiFetch("/api/resumes/upload", {
    method: "POST",
    body: form,
  }, 90_000);
  if (!res.ok) {
    const detail = await res.text();
    if (res.status === 502 || res.status === 503) {
      throw new Error(
        `Upload failed: backend timed out (${res.status}). Open ${API_URL}/health — if ok, retry. A CORS error in DevTools usually means the same thing.`,
      );
    }
    throw new Error(detail || `Upload failed (${res.status})`);
  }
  return res.json() as Promise<{
    candidate_id: number;
    resume_id: number;
    profile: CandidateProfile;
  }>;
}

export async function getJobSources() {
  const res = await apiFetchWithRetry("/api/jobs/sources", undefined, 20_000, 0);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<{ sources: JobSource[] }>;
}

export async function discoverJobs(
  candidateId: number,
  platforms?: string[],
  remoteType?: string,
  fresh = true,
  opts?: { location?: string; allLocations?: boolean },
) {
  logClick("discover_jobs", "profile-based discovery", { candidateId });
  const params = new URLSearchParams();
  if (platforms && platforms.length > 0) {
    params.set("sources", platforms.join(","));
  }
  if (remoteType) {
    params.set("remote_type", remoteType);
  }
  if (opts?.allLocations) {
    params.set("all_locations", "true");
  } else if (opts?.location?.trim()) {
    params.set("location", opts.location.trim());
  }
  params.set("fresh", fresh ? "true" : "false");
  const qs = params.toString();
  const url = `/api/jobs/discover/${candidateId}${qs ? `?${qs}` : ""}`;
  const res = await apiFetch(url, { method: "POST" }, 180_000);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<DiscoveryResult>;
}

/** @deprecated Scoring archived — use listDiscoveredJobs after discovery. */
export async function matchJobs(candidateId: number) {
  logClick("run_matching", "score all jobs", { candidateId });
  const res = await apiFetch(`/api/matching/${candidateId}`, { method: "POST" }, 180_000);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<MatchResult[]>;
}

export async function listDiscoveredJobs(candidateId: number) {
  const res = await apiFetch(`/api/jobs/discovered/${candidateId}`, undefined, 60_000);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<MatchResult[]>;
}

export async function personalizeResume(candidateId: number, jobId: number) {
  logClick("personalize_resume", `job ${jobId}`, { candidateId, jobId });
  const res = await apiFetch(`/api/matching/${candidateId}/personalize/${jobId}`, {
    method: "POST",
  }, 120_000);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function createApplication(
  candidateId: number,
  jobId: number,
  atsScore?: number,
) {
  logClick("apply_to_job", `job ${jobId}`, { candidateId, jobId });
  const res = await apiFetch("/api/applications", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      candidate_id: candidateId,
      job_id: jobId,
      status: "applied",
      ats_score: atsScore ?? null,
    }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<Application>;
}

export async function getApplications(candidateId: number) {
  const res = await apiFetch(`/api/applications/candidate/${candidateId}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<Application[]>;
}

export async function updateApplicationStatus(applicationId: number, status: string) {
  logClick("update_application_status", status, { applicationId });
  const res = await apiFetch(
    `/api/applications/${applicationId}/status?status=${status}`,
    { method: "PATCH" },
  );
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<Application>;
}

export async function getAnalytics(candidateId: number) {
  const res = await apiFetch(`/api/applications/analytics/${candidateId}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export type AutoApplySession = {
  platform: string;
  logged_in: boolean;
  updated_at: string | null;
};

export type AutoApplyConfig = {
  candidate_id: number;
  job_titles: string[];
  location: string;
  remote_type?: string;
  resume_uploaded: boolean;
  resume_filename: string;
  resume_path: string;
  updated_at: string | null;
  sessions?: AutoApplySession[];
  run_command?: string;
};

export async function getAutoApplyConfig(candidateId: number) {
  const res = await apiFetch(`/api/auto-apply/${candidateId}/config`);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<AutoApplyConfig>;
}

export async function updateAutoApplyConfig(
  candidateId: number,
  jobTitles: string[],
  location: string,
  remoteType?: string,
) {
  const res = await apiFetch(`/api/auto-apply/${candidateId}/config`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ job_titles: jobTitles, location, remote_type: remoteType || null }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<AutoApplyConfig>;
}

export async function uploadAutoApplyResume(candidateId: number, file: File) {
  const form = new FormData();
  form.append("file", file);
  const res = await apiFetch(`/api/auto-apply/${candidateId}/resume`, {
    method: "POST",
    body: form,
  }, 90_000);
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export type BotAvailability = {
  available: boolean;
  message: string;
  platforms: string[];
};

export type BotEvent = {
  ts: string;
  level: string;
  message: string;
  result?: Record<string, unknown>;
};

export type BotStatus = {
  candidate_id: number;
  state: "idle" | "running" | "stopping" | "error";
  available?: boolean;
  run_id?: string;
  platform?: string;
  submitted?: number;
  failed?: number;
  skipped?: number;
  pending?: number;
  error?: string | null;
  recent_events?: BotEvent[];
};

export async function getBotAvailability() {
  const res = await apiFetch("/api/bot/availability");
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<BotAvailability>;
}

export async function getBotStatus(candidateId: number) {
  const res = await apiFetch(`/api/bot/status/${candidateId}`);
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<BotStatus>;
}

export async function startBot(
  candidateId: number,
  opts?: {
    platform?: string;
    continuous?: boolean;
    batchSize?: number;
    jobUrl?: string;
    jobId?: number;
    jobTitle?: string;
    jobCompany?: string;
    applyMode?: "top" | "all";
    maxJobs?: number;
    jobs?: Array<{ title: string; company: string; apply_url: string; job_id: number }>;
  },
) {
  const res = await apiFetch("/api/bot/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      candidate_id: candidateId,
      platform: opts?.platform ?? "linkedin",
      continuous: opts?.continuous ?? true,
      batch_size: opts?.batchSize ?? 5,
      job_url: opts?.jobUrl,
      job_id: opts?.jobId,
      job_title: opts?.jobTitle,
      job_company: opts?.jobCompany,
      apply_mode: opts?.applyMode,
      max_jobs: opts?.maxJobs,
      jobs: opts?.jobs,
    }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<BotStatus>;
}

export async function stopBot(candidateId: number) {
  const res = await apiFetch(`/api/bot/stop/${candidateId}`, { method: "POST" });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<BotStatus>;
}

export async function startBotLogin(candidateId: number, platform: string) {
  const res = await apiFetch("/api/bot/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ candidate_id: candidateId, platform }),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json() as Promise<BotStatus>;
}

export async function signalHumanReady() {
  const res = await apiFetch("/api/bot/human-ready", { method: "POST" });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function signalGoBack() {
  const res = await apiFetch("/api/bot/go-back", { method: "POST" });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export async function signalSkipJob() {
  const res = await apiFetch("/api/bot/skip-job", { method: "POST" });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}
