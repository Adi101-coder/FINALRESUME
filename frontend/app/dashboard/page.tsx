"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { StatCard } from "@/components/StatCard";
import {
  getAnalytics,
  getApplications,
  getBotStatus,
  logClick,
  updateApplicationStatus,
  type Application,
  type BotStatus,
} from "@/lib/api";

const STATUS_OPTIONS = ["applied", "interview", "offer", "rejected", "pending"];

const STATUS_STYLES: Record<string, string> = {
  applied: "border-ink-300 bg-ink-50 text-ink",
  interview: "border-ink bg-ink text-white",
  offer: "border-ink-400 bg-ink-100 text-ink",
  rejected: "border-ink-200 bg-white text-ink-500",
  pending: "border-ink-200 bg-ink-50 text-ink-600",
};

export default function DashboardPage() {
  const [candidateId, setCandidateId] = useState("");
  const [applications, setApplications] = useState<Application[]>([]);
  const [analytics, setAnalytics] = useState<{
    total_applications: number;
    interview_rate: number;
    status_breakdown: Record<string, number>;
    average_ats_score: number | null;
  } | null>(null);
  const [filter, setFilter] = useState("all");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [botStatus, setBotStatus] = useState<BotStatus | null>(null);
  const [botPolling, setBotPolling] = useState(false);

  const load = useCallback(async (id: number) => {
    setLoading(true);
    setError("");
    try {
      const [apps, stats] = await Promise.all([getApplications(id), getAnalytics(id)]);
      setApplications(apps);
      setAnalytics(stats);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load dashboard");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const stored = window.localStorage.getItem("candidateId");
    if (stored) {
      setCandidateId(stored);
      void load(Number(stored));
    }
  }, [load]);

  useEffect(() => {
    if (!candidateId || botPolling) return;
    const id = Number(candidateId);
    const timer = setInterval(async () => {
      try {
        const status = await getBotStatus(id);
        setBotStatus(status);
        if (status.state === "idle" || status.state === "error") {
          setBotPolling(false);
          await load(id);
        }
      } catch {
        setBotPolling(false);
      }
    }, 3000);
    setBotPolling(true);
    return () => clearInterval(timer);
  }, [candidateId, botPolling]);

  async function handleLoad() {
    const id = Number(candidateId);
    if (!id) return;
    window.localStorage.setItem("candidateId", String(id));
    logClick("load_dashboard", `candidate ${id}`);
    await load(id);
  }

  async function handleStatusChange(appId: number, status: string) {
    try {
      const updated = await updateApplicationStatus(appId, status);
      setApplications((prev) => prev.map((a) => (a.id === appId ? updated : a)));
      if (candidateId) await load(Number(candidateId));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Status update failed");
    }
  }

  const filtered =
    filter === "all" ? applications : applications.filter((a) => a.status === filter);

  return (
    <div className="mx-auto max-w-7xl animate-fade-up space-y-10 px-6 py-12 lg:px-10">
      <section className="card-light">
        <div className="flex flex-col gap-6 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p className="section-tag">Dashboard</p>
            <h1 className="mt-2 text-3xl font-bold tracking-tight text-ink">Your applications</h1>
            <p className="mt-2 max-w-lg text-ink-500">
              Every application is saved — status, match score, company, and apply link persist
              across sessions.
            </p>
          </div>
          <Link href="/" className="btn-outline shrink-0">
            ← Back to home
          </Link>
        </div>

        <div className="mt-6 flex flex-wrap gap-3">
          <input
            className="input max-w-[140px] font-mono"
            placeholder="ID"
            value={candidateId}
            onChange={(e) => setCandidateId(e.target.value)}
          />
          <button className="btn-dark" disabled={loading} onClick={() => void handleLoad()}>
            {loading ? "Loading…" : "Refresh"}
          </button>
        </div>
        {error && <div className="alert-error mt-4">{error}</div>}
      </section>

      {analytics && (
        <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard label="Total applied" value={analytics.total_applications} />
          <StatCard label="Interview rate" value={`${analytics.interview_rate}%`} />
          <StatCard label="Avg match score" value={analytics.average_ats_score ?? "—"} />
          <StatCard label="In interview" value={analytics.status_breakdown.interview ?? 0} />
        </section>
      )}

      {botStatus && botStatus.state !== "idle" && (
        <section className="card-light">
          <div className="flex items-center justify-between">
            <div>
              <p className="section-tag">Auto-apply bot</p>
              <h3 className="mt-2 text-xl font-semibold text-ink">Bot running on {botStatus.platform}</h3>
            </div>
            <span className={`badge-neutral ${botStatus.state === "running" ? "bg-ink text-white" : "bg-amber-100 text-amber-800"}`}>
              {botStatus.state}
            </span>
          </div>
          <div className="mt-4 grid gap-4 sm:grid-cols-4">
            <StatCard label="Submitted" value={botStatus.submitted ?? 0} />
            <StatCard label="Skipped" value={botStatus.skipped ?? 0} />
            <StatCard label="Failed" value={botStatus.failed ?? 0} />
            <StatCard label="Pending" value={botStatus.pending ?? 0} />
          </div>
          {botStatus.recent_events && botStatus.recent_events.length > 0 && (
            <div className="mt-4 max-h-48 overflow-y-auto rounded-2xl border border-ink-200 bg-ink-50 p-4 font-mono text-xs text-ink-600">
              {botStatus.recent_events.slice(-10).map((ev, i) => (
                <p key={`${ev.ts}-${i}`} className="py-0.5">
                  [{ev.level}] {ev.message}
                </p>
              ))}
            </div>
          )}
        </section>
      )}

      <section className="card-light">
        <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
          <h2 className="text-lg font-semibold text-ink">Application history</h2>
          <div className="flex flex-wrap gap-2">
            {["all", ...STATUS_OPTIONS].map((s) => (
              <button
                key={s}
                className={`filter-pill ${filter === s ? "filter-pill-active" : "filter-pill-inactive"}`}
                onClick={() => setFilter(s)}
              >
                {s}
              </button>
            ))}
          </div>
        </div>

        {filtered.length === 0 ? (
          <div className="flex flex-col items-center justify-center rounded-3xl border border-dashed border-ink-200 py-16 text-center">
            <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl border border-ink-200 bg-ink-50 text-sm font-semibold text-ink-500">
              0
            </div>
            <p className="text-ink-600">No applications yet</p>
            <p className="mt-2 max-w-sm text-sm text-ink-400">
              Match jobs on the home page and click &quot;Mark applied&quot; to track them here.
            </p>
            <Link href="/#workspace" className="btn-dark mt-6">
              Find jobs
            </Link>
          </div>
        ) : (
          <>
            <div className="space-y-3 lg:hidden">
              {filtered.map((app) => (
                <div
                  key={app.id}
                  className="rounded-2xl border border-ink-200 bg-ink-50 p-4"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div>
                      <p className="font-semibold text-ink">{app.job_title}</p>
                      <p className="text-sm text-ink-500">{app.job_company}</p>
                    </div>
                    {app.match_score != null && (
                      <span className="badge-neutral font-mono">{app.match_score}%</span>
                    )}
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    <select
                      className={`rounded-lg border px-2 py-1 text-xs capitalize ${STATUS_STYLES[app.status] ?? ""}`}
                      value={app.status}
                      onChange={(e) => void handleStatusChange(app.id, e.target.value)}
                    >
                      {STATUS_OPTIONS.map((s) => (
                        <option key={s} value={s}>
                          {s}
                        </option>
                      ))}
                    </select>
                    {app.apply_url && (
                      <a
                        href={app.apply_url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-xs font-medium text-ink underline-offset-2 hover:underline"
                      >
                        Open ↗
                      </a>
                    )}
                  </div>
                </div>
              ))}
            </div>

            <div className="hidden overflow-x-auto lg:block">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="border-b border-ink-200 text-xs uppercase tracking-wide text-ink-400">
                    <th className="pb-4 pr-4 font-medium">Role</th>
                    <th className="pb-4 pr-4 font-medium">Company</th>
                    <th className="pb-4 pr-4 font-medium">Location</th>
                    <th className="pb-4 pr-4 font-medium">Match</th>
                    <th className="pb-4 pr-4 font-medium">Status</th>
                    <th className="pb-4 pr-4 font-medium">Applied</th>
                    <th className="pb-4 font-medium">Link</th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((app) => (
                    <tr key={app.id} className="table-row">
                      <td className="py-4 pr-4 font-medium text-ink">{app.job_title}</td>
                      <td className="py-4 pr-4 text-ink-600">{app.job_company}</td>
                      <td className="py-4 pr-4 text-ink-500">{app.job_location || "—"}</td>
                      <td className="py-4 pr-4 font-mono text-ink">
                        {app.match_score != null ? `${app.match_score}%` : "—"}
                      </td>
                      <td className="py-4 pr-4">
                        <select
                          className={`rounded-lg border px-2.5 py-1.5 text-xs capitalize ${STATUS_STYLES[app.status] ?? ""}`}
                          value={app.status}
                          onChange={(e) => void handleStatusChange(app.id, e.target.value)}
                        >
                          {STATUS_OPTIONS.map((s) => (
                            <option key={s} value={s}>
                              {s}
                            </option>
                          ))}
                        </select>
                      </td>
                      <td className="py-4 pr-4 text-ink-500">
                        {app.applied_at ? new Date(app.applied_at).toLocaleDateString() : "—"}
                      </td>
                      <td className="py-4">
                        {app.apply_url ? (
                          <a
                            href={app.apply_url}
                            target="_blank"
                            rel="noreferrer"
                            className="font-medium text-ink underline-offset-2 hover:underline"
                          >
                            Open ↗
                          </a>
                        ) : (
                          "—"
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
