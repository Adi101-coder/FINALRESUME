"use client";

import { useState, useEffect, useRef } from "react";
import {
  getBotStatus,
  getApplications,
  listAppliedJobs,
  startBot,
  stopBot,
  signalHumanReady,
  signalGoBack,
  signalSkipJob,
  type BotStatus,
  type Application,
  type AppliedJobRow,
} from "@/lib/api";
import { MatchResult } from "@/lib/api";

type Props = {
  candidateId: number;
  loading: boolean;
  error: string;
  onBack: () => void;
  reviewQueue: MatchResult[];
  onRemoveFromQueue: (jobId: number) => void;
  onClearQueue: () => void;
};

type JobUiStatus = "queued" | "applying" | "applied" | "skipped" | "failed" | "review";

type Outcome = { status: JobUiStatus; message: string };

function normUrl(url: string) {
  return (url || "").split("?")[0].replace(/\/+$/, "").toLowerCase();
}

function sameJob(
  job: MatchResult,
  other: { job_id?: number | null; apply_url?: string; url?: string; title?: string; company?: string },
) {
  if (other.job_id && Number(other.job_id) === job.job_id) return true;
  const otherUrl = normUrl(String(other.apply_url || other.url || ""));
  if (otherUrl && otherUrl === normUrl(job.apply_url)) return true;
  const t = (other.title || "").trim().toLowerCase();
  const c = (other.company || "").trim().toLowerCase();
  return Boolean(t && c && t === job.title.trim().toLowerCase() && c === job.company.trim().toLowerCase());
}

function mapBotStatus(raw: string, message: string): JobUiStatus {
  const s = (raw || "").toLowerCase();
  const msg = (message || "").toLowerCase();
  if (s === "submitted" || s === "applied" || msg.includes("already applied")) return "applied";
  if (s === "review_required" || s === "pending") return "review";
  if (s === "failed") return "failed";
  if (s === "skipped" || s === "unsupported") return "skipped";
  return "queued";
}

function badge(status: JobUiStatus) {
  switch (status) {
    case "applied":
      return { label: "Applied", className: "bg-green-600 text-white" };
    case "applying":
      return { label: "Applying…", className: "bg-ink text-white" };
    case "failed":
      return { label: "Failed", className: "bg-red-100 text-red-800" };
    case "skipped":
      return { label: "Skipped", className: "bg-ink-100 text-ink-600" };
    case "review":
      return { label: "Needs review", className: "bg-amber-100 text-amber-900" };
    default:
      return { label: "Queued", className: "bg-ink-100 text-ink-500" };
  }
}

export function AutoApplyStep({ candidateId, error, onBack, reviewQueue, onClearQueue }: Props) {
  const [botStatus, setBotStatus] = useState<BotStatus | null>(null);
  const [outcomes, setOutcomes] = useState<Record<number, Outcome>>({});
  const [botRunning, setBotRunning] = useState(false);
  const [startError, setStartError] = useState("");
  const autoStarted = useRef(false);

  const mergeOutcomes = (apps: Application[], applied: AppliedJobRow[], status: BotStatus | null) => {
    setOutcomes((prev) => {
      const next = { ...prev };
      const write = (job: MatchResult, status: JobUiStatus, message: string) => {
        const existing = next[job.job_id];
        if (existing?.status === "applied") return;
        next[job.job_id] = { status, message };
      };
      for (const job of reviewQueue) {
        for (const row of applied) {
          if (sameJob(job, row)) write(job, "applied", row.notes || "Submitted");
        }
        for (const app of apps) {
          if (app.job_id !== job.job_id && !sameJob(job, { apply_url: app.apply_url, title: app.job_title, company: app.job_company })) {
            continue;
          }
          write(job, mapBotStatus(app.status, app.notes), app.notes || app.status);
        }
      }
      for (const event of status?.recent_events || []) {
        const result = event.result || {};
        const raw = String(result.status || "");
        if (!raw) continue;
        const message = String(result.message || event.message || "");
        const mapped = mapBotStatus(raw, message);
        for (const job of reviewQueue) {
          if (
            sameJob(job, {
              job_id: result.job_id as number | undefined,
              apply_url: String(result.apply_url || result.url || ""),
              title: String(result.title || ""),
              company: String(result.company || ""),
            })
          ) {
            write(job, mapped, message);
          }
        }
      }
      return next;
    });
  };

  const refreshProgress = async () => {
    try {
      const [status, apps, applied] = await Promise.all([
        getBotStatus(candidateId),
        getApplications(candidateId).catch(() => [] as Application[]),
        listAppliedJobs(candidateId).catch(() => [] as AppliedJobRow[]),
      ]);
      setBotStatus(status);
      if (status.state === "running" || status.state === "stopping") {
        setBotRunning(true);
      } else if (status.state === "idle" || status.state === "error") {
        setBotRunning(false);
      }
      mergeOutcomes(apps, applied, status);
    } catch {}
  };

  useEffect(() => {
    void refreshProgress();
    const timer = setInterval(() => void refreshProgress(), 2000);
    return () => clearInterval(timer);
  }, [candidateId, reviewQueue]);

  const handleStartBot = async (jobsOverride?: typeof reviewQueue) => {
    const source = jobsOverride ?? reviewQueue;
    setBotRunning(true);
    setStartError("");
    try {
      const jobs = [...source]
        .filter((match) => match.apply_url)
        .reverse()
        .map((match) => ({
          title: match.title,
          company: match.company,
          apply_url: match.apply_url,
          job_id: match.job_id,
        }));
      if (jobs.length > 0) {
        await startBot(candidateId, {
          platform: "linkedin",
          continuous: false,
          batchSize: Math.min(jobs.length, 20),
          jobs,
        });
      } else {
        await startBot(candidateId, {
          platform: "linkedin",
          continuous: true,
          batchSize: 5,
          applyMode: "all",
        });
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : "Could not start the bot";
      if (/already running/i.test(message)) {
        setBotRunning(true);
        return;
      }
      setStartError(message);
      setBotRunning(false);
    }
  };

  useEffect(() => {
    if (autoStarted.current) return;
    autoStarted.current = true;
    void handleStartBot(reviewQueue);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleStopBot = async () => {
    await stopBot(candidateId);
    setBotRunning(false);
  };

  const applyOrder = [...reviewQueue].reverse();
  let markedApplying = false;
  const rows = applyOrder.map((job) => {
    const outcome = outcomes[job.job_id];
    let status: JobUiStatus = outcome?.status || "queued";
    if (botRunning && !outcome && !markedApplying) {
      status = "applying";
      markedApplying = true;
    }
    return { job, status, message: outcome?.message || "" };
  });

  const appliedCount = rows.filter((r) => r.status === "applied").length;
  const skippedCount = rows.filter((r) => r.status === "skipped").length;
  const failedCount = rows.filter((r) => r.status === "failed").length;

  return (
    <div className="max-w-4xl mx-auto">
      <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
        <div>
          <h1 className="text-3xl font-bold text-ink">Auto-apply</h1>
          <p className="text-ink-500">Each listed job updates here as the bot submits, skips, or fails</p>
        </div>
        <button onClick={onBack} className="btn-outline text-sm">
          ← Back to Matches
        </button>
      </div>

      {(error || startError || botStatus?.error) && (
        <div className="alert-error mb-6">{startError || botStatus?.error || error}</div>
      )}

      <div
        className={`mb-6 rounded-2xl border p-5 ${
          botRunning ? "border-ink bg-ink text-white" : "border-ink-200 bg-ink-50"
        }`}
      >
        <div className="flex flex-col gap-4">
          <div className="min-w-0">
            <p className="font-semibold">{botRunning ? "Bot running" : "Bot idle"}</p>
            <p className="text-sm opacity-75">
              {appliedCount} applied · {skippedCount} skipped · {failedCount} failed · {rows.length} shown
            </p>
            {botRunning ? (
              <p className="mt-2 text-xs opacity-80">
                A separate Chromium window should open with LinkedIn already signed in. Check the Dock if it is behind this tab.
              </p>
            ) : null}
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {!botRunning ? (
              <button onClick={() => handleStartBot()} className="btn-dark whitespace-nowrap px-4 py-2 text-sm">
                Start bot{reviewQueue.length ? ` · ${reviewQueue.length} jobs` : ""}
              </button>
            ) : (
              <button
                onClick={handleStopBot}
                className="btn-outline whitespace-nowrap border-red-300 bg-red-100 px-4 py-2 text-sm text-red-700"
              >
                Stop bot
              </button>
            )}
            <button
              onClick={() => void signalGoBack()}
              className="btn-outline whitespace-nowrap px-4 py-2 text-sm"
              title="Go back one Easy Apply step and wait 30s"
            >
              Go back
            </button>
            <button
              onClick={() => void signalSkipJob()}
              className="btn-outline whitespace-nowrap px-4 py-2 text-sm"
              title="Skip this listing only"
            >
              Skip this job
            </button>
            <button
              onClick={() => signalHumanReady().then(() => setBotStatus((s) => (s ? { ...s, state: "running" } : s)))}
              className="btn-outline whitespace-nowrap px-4 py-2 text-sm"
            >
              I&apos;m done
            </button>
          </div>
        </div>
      </div>

      {rows.length > 0 ? (
        <div className="space-y-3">
          <h2 className="text-xl font-semibold text-ink">Shown jobs</h2>
          {rows.map(({ job, status, message }) => {
            const chip = badge(status);
            return (
              <div
                key={job.job_id}
                className={`rounded-2xl border bg-white p-4 ${
                  status === "applied"
                    ? "border-green-500"
                    : status === "applying"
                      ? "border-ink"
                      : "border-ink-200"
                }`}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="font-semibold text-ink">{job.title}</h3>
                    <p className="text-sm text-ink-500">
                      {job.company} · {job.location}
                    </p>
                    {message ? <p className="mt-2 text-xs text-ink-500 break-words">{message}</p> : null}
                  </div>
                  <span className={`shrink-0 rounded-full px-2.5 py-1 text-xs font-semibold ${chip.className}`}>
                    {chip.label}
                  </span>
                </div>
                {job.apply_url ? (
                  <a href={job.apply_url} target="_blank" rel="noreferrer" className="mt-2 inline-block text-xs text-ink-500 underline">
                    Open listing
                  </a>
                ) : null}
              </div>
            );
          })}
          <button onClick={onClearQueue} className="btn-outline text-sm">
            Clear list
          </button>
        </div>
      ) : (
        <div className="rounded-2xl border-2 border-dashed border-ink-200 bg-ink-50 p-12 text-center">
          <h3 className="text-xl font-semibold text-ink mb-2">No jobs in this batch</h3>
          <p className="text-ink-500">Go back to Discover and load a new set of jobs.</p>
        </div>
      )}
    </div>
  );
}
