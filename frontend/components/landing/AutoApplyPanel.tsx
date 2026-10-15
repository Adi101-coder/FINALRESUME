"use client";

import { useCallback, useEffect, useState } from "react";
import {
  getAutoApplyConfig,
  updateAutoApplyConfig,
  uploadAutoApplyResume,
  getBotAvailability,
  getBotStatus,
  startBot,
  stopBot,
  signalHumanReady,
  signalGoBack,
  signalSkipJob,
  type AutoApplyConfig,
  type BotStatus,
} from "@/lib/api";

type Props = {
  candidateId: number | null;
  onApplicationsChanged?: () => void;
};

const PLATFORMS = ["linkedin"];

const JOB_TYPES = [
  { key: "", label: "Any" },
  { key: "remote", label: "Remote" },
  { key: "onsite", label: "On-site" },
  { key: "hybrid", label: "Hybrid" },
];

function parseJobTitles(input: string): string[] {
  return input
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean)
    .map((t) => t.replace(/^"|"$/g, "").replace(/^'|'$/g, ""));
}

function formatJobTitles(titles: string[]): string {
  return titles.map((t) => (t.includes(",") ? `"${t}"` : t)).join(", ");
}

export function AutoApplyPanel({ candidateId, onApplicationsChanged }: Props) {
  const [config, setConfig] = useState<AutoApplyConfig | null>(null);
  const [titlesInput, setTitlesInput] = useState("Software Developer, Frontend Developer");
  const [location, setLocation] = useState("Remote");
  const [platform, setPlatform] = useState("linkedin");
  const [remoteType, setRemoteType] = useState("");
  const [botAvail, setBotAvail] = useState(false);
  const [botStatus, setBotStatus] = useState<BotStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [availCheckInterval, setAvailCheckInterval] = useState<NodeJS.Timeout | null>(null);
  const [applyMode, setApplyMode] = useState<"top" | "all">("top"); // "top" = only jobs that passed threshold, "all" = all matched jobs
  const [maxJobs, setMaxJobs] = useState<number | null>(null); // null = no limit

  const load = useCallback(async (id: number) => {
    try {
      const data = await getAutoApplyConfig(id);
      setConfig(data);
      setTitlesInput(formatJobTitles(data.job_titles));
      setLocation(data.location || "Remote");
      setRemoteType(data.remote_type || "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load auto-apply settings");
    }
  }, []);

  const checkBotAvailability = useCallback(async () => {
    try {
      const avail = await getBotAvailability();
      setBotAvail(avail.available);
    } catch {
      setBotAvail(false);
    }
  }, []);

  const loadBotStatus = useCallback(async (id: number) => {
    try {
      const status = await getBotStatus(id);
      setBotStatus(status);
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => {
    if (candidateId) {
      void load(candidateId);
      void loadBotStatus(candidateId);
    }
  }, [candidateId, load, loadBotStatus]);

  const startAvailabilityPolling = useCallback(() => {
    if (availCheckInterval) return;
    const interval = setInterval(() => {
      checkBotAvailability();
    }, 30000);
    setAvailCheckInterval(interval);
  }, [availCheckInterval, checkBotAvailability]);

  const stopAvailabilityPolling = useCallback(() => {
    if (availCheckInterval) {
      clearInterval(availCheckInterval);
      setAvailCheckInterval(null);
    }
  }, [availCheckInterval]);

  useEffect(() => {
    void checkBotAvailability();
    startAvailabilityPolling();
    return () => stopAvailabilityPolling();
  }, [checkBotAvailability, startAvailabilityPolling, stopAvailabilityPolling]);

  useEffect(() => {
    if (!candidateId || botStatus?.state !== "running") return;
    const timer = setInterval(() => {
      void getBotStatus(candidateId).then((s) => {
        setBotStatus(s);
        if (s.state === "idle" || s.state === "error") {
          onApplicationsChanged?.();
        }
      });
    }, 2000);
    return () => clearInterval(timer);
  }, [candidateId, botStatus?.state, onApplicationsChanged]);

  async function handleSaveTitles() {
    if (!candidateId) return;
    setLoading(true);
    setError("");
    try {
      const titles = parseJobTitles(titlesInput);
      const updated = await updateAutoApplyConfig(candidateId, titles, location, remoteType || undefined);
      setConfig(updated);
      setMessage("Job search preferences saved.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleResumeUpload(file: File) {
    if (!candidateId) return;
    setLoading(true);
    setError("");
    try {
      await uploadAutoApplyResume(candidateId, file);
      await load(candidateId);
      setMessage(`Auto-apply resume uploaded: ${file.name}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleStartBot() {
    if (!candidateId) return;
    setLoading(true);
    setError("");
    try {
      const status = await startBot(candidateId, { 
        platform, 
        continuous: true, 
        batchSize: 5,
        applyMode,
        maxJobs: maxJobs || undefined,
      });
      setBotStatus(status);
      setMessage(`Bot started on ${platform} (${applyMode === "top" ? "top jobs only" : "all jobs"}${maxJobs ? `, max ${maxJobs} jobs` : ""}). Watch the browser window.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not start bot");
    } finally {
      setLoading(false);
    }
  }

  async function handleStopBot() {
    if (!candidateId) return;
    try {
      const status = await stopBot(candidateId);
      setBotStatus(status);
      setMessage("Stop requested — bot will finish the current job.");
      onApplicationsChanged?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Stop failed");
    }
  }

  if (!candidateId) {
    return (
      <div className="card-light mt-8">
        <p className="section-tag">Auto-apply bot</p>
        <p className="mt-2 text-sm text-ink-500">Upload your main resume first to enable the apply bot.</p>
      </div>
    );
  }

  const running = botStatus?.state === "running" || botStatus?.state === "stopping";

  return (
    <div className="card-light mt-8 space-y-6">
      <div>
        <p className="section-tag">Auto-apply bot</p>
        <h3 className="mt-2 text-2xl font-semibold text-ink">Run bot from here</h3>
        <p className="mt-2 text-sm text-ink-500">
          Requires local backend with Playwright. Applies sync to your dashboard automatically.
        </p>
      </div>

      {message && <div className="alert-success">{message}</div>}
      {error && <div className="alert-error">{error}</div>}

      {!botAvail && (
        <div className="rounded-2xl border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
          <p className="font-medium">Bot runner unavailable</p>
          <p className="mt-1">
            Make sure the backend is running locally with Playwright installed.
            Run <code className="rounded bg-amber-100 px-1 py-0.5 font-mono text-xs">playwright install chromium</code> if you haven&apos;t already.
          </p>
          <button
            className="mt-2 text-xs font-medium text-amber-700 underline"
            onClick={() => void checkBotAvailability()}
          >
            Retry connection check
          </button>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <div className="space-y-3">
          <label className="text-sm font-medium text-ink">Bot resume (PDF)</label>
          <label className="upload-zone block cursor-pointer">
            <span className="font-medium text-ink">
              {config?.resume_uploaded ? `✓ ${config.resume_filename || "resume.pdf"}` : "Upload bot resume PDF"}
            </span>
            <input
              type="file"
              accept="application/pdf"
              className="hidden"
              disabled={loading || running}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) void handleResumeUpload(f);
              }}
            />
          </label>
        </div>

        <div className="space-y-3">
          <label className="text-sm font-medium text-ink">Platform</label>
          <select
            className="input w-full"
            value={platform}
            disabled={running}
            onChange={(e) => setPlatform(e.target.value)}
          >
            {PLATFORMS.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
          <input
            className="input w-full"
            value={titlesInput}
            disabled={running}
            onChange={(e) => setTitlesInput(e.target.value)}
            placeholder='Software Developer, "Senior Engineer", Frontend Developer'
          />
          <input
            className="input w-full"
            value={location}
            disabled={running}
            onChange={(e) => setLocation(e.target.value)}
            placeholder="Remote"
          />
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-500">Job type</label>
            <div className="flex flex-wrap gap-2">
              {JOB_TYPES.map((t) => (
                <button
                  key={t.key}
                  type="button"
                  disabled={running}
                  onClick={() => setRemoteType(t.key)}
                  className={`rounded-xl border px-3 py-1.5 text-xs font-medium transition-colors ${
                    remoteType === t.key
                      ? "border-ink bg-ink text-white"
                      : "border-ink-200 bg-white text-ink-600 hover:border-ink-400"
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-500">Apply mode</label>
            <div className="flex flex-wrap gap-2">
              {(["top", "all"] as const).map((mode) => (
                <button
                  key={mode}
                  type="button"
                  disabled={running}
                  onClick={() => setApplyMode(mode)}
                  className={`rounded-xl border px-3 py-1.5 text-xs font-medium transition-colors ${
                    applyMode === mode
                      ? "border-ink bg-ink text-white"
                      : "border-ink-200 bg-white text-ink-600 hover:border-ink-400"
                  }`}
                >
                  {mode === "top" ? "Top jobs only (≥75%)" : "All matched jobs"}
                </button>
              ))}
            </div>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-ink-500">Max jobs (optional)</label>
            <input
              type="number"
              className="input w-full max-w-[120px]"
              value={maxJobs ?? ""}
              onChange={(e) => setMaxJobs(e.target.value ? parseInt(e.target.value) : null)}
              placeholder="No limit"
              min="1"
              max="100"
              disabled={running}
            />
          </div>
          <button className="btn-outline w-full" disabled={loading || running} onClick={() => void handleSaveTitles()}>
            Save search preferences
          </button>
        </div>
      </div>

      <div className="flex flex-wrap gap-3">
        {!running ? (
          <button className="btn-dark" disabled={loading || !botAvail} onClick={() => void handleStartBot()}>
            Start auto-apply
          </button>
        ) : (
          <>
            <button className="btn-dark" onClick={() => void handleStopBot()}>
              Stop bot
            </button>
            <button
              className="btn-outline"
              onClick={() => void signalGoBack().then(() => setMessage("Going back — 30s to review the form."))}
            >
              Go back (30s review)
            </button>
            <button
              className="btn-outline"
              onClick={() => void signalSkipJob().then(() => setMessage("Skipping this job — continuing the queue."))}
            >
              Skip this job
            </button>
            <button
              className="btn-outline"
              onClick={() => void signalHumanReady().then(() => setMessage("Signal sent — bot will resume."))}
            >
              Login done / CAPTCHA solved
            </button>
          </>
        )}
        {botStatus && (
          <span className="self-center text-sm text-ink-500">
            {botStatus.state} · {botStatus.submitted ?? 0} submitted · {botStatus.skipped ?? 0} skipped
          </span>
        )}
      </div>

      {config?.sessions && (
        <div>
          <p className="mb-2 text-sm font-medium text-ink">Login status (log in via Step 2 above)</p>
          <div className="flex flex-wrap gap-2">
            {config.sessions.map((s) => (
              <span
                key={s.platform}
                className={`source-pill ${s.logged_in ? "border-ink bg-ink text-white" : ""}`}
              >
                {s.platform} {s.logged_in ? "✓" : "— not logged in"}
              </span>
            ))}
          </div>
          {running && (
            <button
              className="btn-outline mt-3"
              onClick={() =>
                void signalHumanReady().then(() => setMessage("Signal sent — bot will resume."))
              }
            >
              Login done / CAPTCHA solved
            </button>
          )}
        </div>
      )}

      {botStatus?.recent_events && botStatus.recent_events.length > 0 && (
        <div className="max-h-48 overflow-y-auto rounded-2xl border border-ink-200 bg-ink-50 p-4 font-mono text-xs text-ink-600">
          {botStatus.recent_events.slice(-12).map((ev, i) => (
            <p key={`${ev.ts}-${i}`} className="py-0.5">
              [{ev.level}] {ev.message}
            </p>
          ))}
        </div>
      )}
    </div>
  );
}
