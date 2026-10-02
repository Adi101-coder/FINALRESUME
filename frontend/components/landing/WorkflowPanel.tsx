"use client";

import Link from "next/link";
import { useState } from "react";
import { ScoreRing } from "@/components/ScoreRing";
import { StatCard } from "@/components/StatCard";
import {
  logClick,
  type CandidateProfile,
  type DiscoveryResult,
  type MatchResult,
} from "@/lib/api";

const PIPELINE = [
  { id: "upload", label: "Parse resume" },
  { id: "login", label: "Log in to platforms" },
  { id: "discover", label: "Discover & score jobs" },
  { id: "apply", label: "Apply" },
];

const PLATFORMS = [
  { key: "linkedin", label: "LinkedIn", auth: "login required", needsLoginForSearch: true },
];

const JOB_TYPES = [
  { key: "remote", label: "Remote" },
  { key: "onsite", label: "On-site" },
  { key: "hybrid", label: "Hybrid" },
  { key: "", label: "Any" },
];

type PlatformSession = {
  platform: string;
  logged_in: boolean;
  updated_at: string | null;
};

type Props = {
  loading: boolean;
  loadingPhase: "" | "discover" | "match";
  candidateId: number | null;
  profile: CandidateProfile | null;
  discovery: DiscoveryResult | null;
  matches: MatchResult[];
  appliedIds: Set<number>;
  pipelineStep: number;
  message: string;
  error: string;
  selectedPlatforms: string[];
  remoteType: string;
  sessions: PlatformSession[] | null;
  loginPlatform: string | null;
  onUpload: (file: File) => void;
  onLogin: (platform: string) => void;
  onLoginDone: () => void;
  onDiscoverAndMatch: () => void;
  onPersonalize: (jobId: number) => void;
  onApply: (match: MatchResult) => void;
  onBotApply?: (match: MatchResult) => void;
  onAutoApplyAll?: () => void;
  autoApplyArmed?: boolean;
  onAutoApplyArmedChange?: (armed: boolean) => void;
  onPlatformToggle?: (platform: string) => void;
  onRemoteTypeChange?: (type: string) => void;
  botRunning?: boolean;
  onError?: (error: string) => void;
};

export function WorkflowPanel({
  loading,
  loadingPhase,
  candidateId,
  profile,
  discovery,
  matches,
  appliedIds,
  pipelineStep,
  message,
  error,
  selectedPlatforms,
  remoteType,
  sessions,
  loginPlatform,
  onUpload,
  onLogin,
  onLoginDone,
  onDiscoverAndMatch,
  onPersonalize,
  onApply,
  onBotApply,
  onAutoApplyAll,
  autoApplyArmed = false,
  onAutoApplyArmedChange,
  onPlatformToggle,
  onRemoteTypeChange,
  botRunning = false,
  onError,
}: Props) {
  const [jobLoadingStates, setJobLoadingStates] = useState<Record<number, { personalize: boolean; apply: boolean; botApply: boolean }>>({});
  const passedCount = matches.filter((m) => m.passed_threshold).length;
  const initials = profile?.name
    ? profile.name
        .split(" ")
        .map((n) => n[0])
        .join("")
        .slice(0, 2)
        .toUpperCase()
    : "?";

  async function handlePersonalize(jobId: number) {
    if (!candidateId) return;
    setJobLoadingStates((prev) => ({ ...prev, [jobId]: { ...prev[jobId], personalize: true } }));
    try {
      await onPersonalize(jobId);
    } finally {
      setJobLoadingStates((prev) => ({ ...prev, [jobId]: { ...prev[jobId], personalize: false } }));
    }
  }

  async function handleApply(match: MatchResult) {
    if (!candidateId) return;
    setJobLoadingStates((prev) => ({ ...prev, [match.job_id]: { ...prev[match.job_id], apply: true } }));
    try {
      await onApply(match);
    } finally {
      setJobLoadingStates((prev) => ({ ...prev, [match.job_id]: { ...prev[match.job_id], apply: false } }));
    }
  }

  async function handleBotApply(match: MatchResult) {
    if (!candidateId) return;
    setJobLoadingStates((prev) => ({ ...prev, [match.job_id]: { ...prev[match.job_id], botApply: true } }));
    try {
      await onBotApply?.(match);
    } finally {
      setJobLoadingStates((prev) => ({ ...prev, [match.job_id]: { ...prev[match.job_id], botApply: false } }));
    }
  }

  return (
    <section id="workspace" className="scroll-mt-24 bg-ink-50 py-24">
      <div className="mx-auto max-w-7xl px-6 lg:px-10">
        <div className="mx-auto max-w-2xl text-center">
          <p className="section-tag">Start now</p>
          <h2 className="mt-4 display-heading">
            Upload once.{" "}
            <span className="font-serif italic font-normal">Match everywhere.</span>
          </h2>
          <p className="mt-4 text-lg text-ink-500">
            Drop your PDF, discover jobs from your profile, and review scored matches in minutes.
          </p>
        </div>

        <div className="mt-10 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {PIPELINE.map((step, i) => (
            <div
              key={step.id}
              className={`progress-step ${
                pipelineStep > i + 1
                  ? "progress-step-done"
                  : pipelineStep === i + 1
                    ? "progress-step-active"
                    : ""
              }`}
            >
              <span className="font-mono text-xs opacity-60">{String(i + 1).padStart(2, "0")}</span>
              <span>{step.label}</span>
            </div>
          ))}
        </div>

        {message && <div className="alert-success mt-6">{message}</div>}
        {error && <div className="alert-error mt-6">{error}</div>}

        <div className="mt-10 grid gap-8 lg:grid-cols-2">
          <div className="card-light space-y-5">
            <div>
              <p className="section-tag">Step 1</p>
              <h3 className="mt-2 text-2xl font-semibold text-ink">Upload your resume</h3>
              <p className="mt-2 text-sm text-ink-500">
                PDF only. Works across engineering, finance, design, and more.
              </p>
            </div>

            <label className="upload-zone">
              <span className="mb-3 flex h-12 w-12 items-center justify-center rounded-2xl border border-ink-200 bg-white text-xs font-bold uppercase tracking-widest text-ink">
                PDF
              </span>
              <span className="font-medium text-ink">
                {loading ? "Processing…" : "Drop PDF or click to browse"}
              </span>
              <span className="mt-1 text-xs text-ink-400">Recommended · up to 2 pages</span>
              <input
                type="file"
                accept="application/pdf"
                className="hidden"
                disabled={loading}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  if (file) onUpload(file);
                }}
              />
            </label>

            {profile && (
              <div className="rounded-3xl border border-ink-200 bg-ink-50 p-5">
                <div className="flex gap-4">
                  <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl bg-ink text-lg font-bold text-white">
                    {initials}
                  </div>
                  <div className="min-w-0">
                    <p className="truncate text-lg font-semibold text-ink">
                      {profile.name || "Candidate"}
                    </p>
                    <p className="truncate text-sm text-ink-500">
                      {profile.email || "No email detected"}
                    </p>
                  </div>
                </div>
                <div className="mt-4 flex flex-wrap gap-1.5">
                  {profile.skills.slice(0, 10).map((skill) => (
                    <span key={skill} className="badge-neutral">
                      {skill}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>

          <div className="card-light flex flex-col space-y-5">
            <div>
              <p className="section-tag">Step 2</p>
              <h3 className="mt-2 text-2xl font-semibold text-ink">Log in to job platforms</h3>
              <p className="mt-2 text-sm text-ink-500">
                Uses your own Chrome where you&apos;re already logged in — no fresh bot browser.
                Click a platform, confirm, and search unlocks right after.
              </p>
            </div>

            {!candidateId && (
              <p className="text-center text-xs text-ink-400">Upload a resume first</p>
            )}

            {candidateId && !sessions && (
              <p className="text-center text-xs text-ink-400">Loading login status…</p>
            )}

            {candidateId && sessions && (
              <div className="grid grid-cols-2 gap-2">
                {sessions.map((s) => (
                  <div
                    key={s.platform}
                    className={`rounded-2xl border px-3 py-2.5 text-sm ${
                      s.logged_in ? "border-ink bg-ink text-white" : "border-ink-200 bg-white"
                    }`}
                  >
                    <p className="font-semibold capitalize">
                      {s.platform} {s.logged_in && "✓"}
                    </p>
                    {!s.logged_in && (
                      <button
                        type="button"
                        className="mt-1 text-xs font-semibold text-ink underline underline-offset-2"
                        disabled={loading || (loginPlatform !== null && loginPlatform !== s.platform)}
                        onClick={() => onLogin(s.platform)}
                      >
                        {loginPlatform === s.platform ? "Logging in…" : "Log in"}
                      </button>
                    )}
                    {loginPlatform === s.platform && !s.logged_in && (
                      <div className="mt-2 p-2 rounded-lg bg-amber-50 border border-amber-200 text-[11px] text-amber-800">
                        <p className="font-medium">Complete login in the browser window:</p>
                        <ul className="mt-1 list-disc list-inside space-y-0.5">
                          <li>If you see &ldquo;Sign in with Google&rdquo;, click it and complete Google sign-in</li>
                          <li>Make sure you&rsquo;re using the correct Google account</li>
                          <li>Close ALL other browser windows to avoid profile conflicts</li>
                        </ul>
                        <button
                          className="mt-2 text-xs font-semibold text-amber-700 underline"
                          onClick={onLoginDone}
                        >
                          Login done — save session
                        </button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}

            {loginPlatform !== null && (
              <button className="btn-dark w-full" onClick={onLoginDone}>
                Login done — save session & show my jobs
              </button>
            )}
          </div>

          <div className="card-light flex flex-col space-y-5 lg:col-span-2">
            <div>
              <p className="section-tag">Step 3</p>
              <h3 className="mt-2 text-2xl font-semibold text-ink">Available jobs for your role</h3>
              <p className="mt-2 text-sm text-ink-500">
                Live search across LinkedIn — exact available counts for your role, no stored pool.
              </p>
            </div>

            {discovery && (
              <div className="rounded-3xl border border-ink-200 bg-ink-50 p-4 text-sm text-ink-600">
                <p className="font-semibold text-ink">
                  {discovery.total_available ?? discovery.candidate_jobs} live jobs for “
                  {discovery.role ?? discovery.search_queries[0] ?? "this role"}” · fresh search, no pool
                </p>
                {discovery.platform_counts && (
                  <div className="mt-3 grid grid-cols-2 gap-2">
                    {Object.entries(discovery.platform_counts).map(([platform, c]) => (
                      <div
                        key={platform}
                        className="rounded-2xl border border-ink-200 bg-white px-3 py-2"
                      >
                        <p className="text-xs font-semibold capitalize text-ink">{platform}</p>
                        <p className="font-mono text-lg font-bold text-ink">
                          {c.available}
                          <span className="ml-1 text-xs font-normal text-ink-400">available</span>
                        </p>
                        <p className="text-[11px] text-ink-400">
                          {c.fetched} fetched live{c.errors ? ` · ${c.errors} error` : ""}
                        </p>
                        {c.error && (
                          <p className="mt-1 text-[11px] leading-snug text-amber-700">{c.error}</p>
                        )}
                      </div>
                    ))}
                  </div>
                )}
                <p className="mt-2 text-xs text-ink-500">
                  {(discovery.search_queries ?? []).slice(0, 5).join(" · ")}
                </p>
                {loadingPhase === "match" && (
                  <p className="mt-2 text-xs font-medium text-ink">
                    Scoring matches against your profile…
                  </p>
                )}
              </div>
            )}

            {candidateId && !loading && !discovery && (
              <div className="space-y-4 rounded-3xl border border-ink-200 bg-ink-50 p-4">
                <div>
                  <p className="mb-2 text-sm font-medium text-ink">Select platforms to search</p>
                  <div className="flex flex-wrap gap-2">
                    {PLATFORMS.map((p) => {
                      const session = sessions?.find((s) => s.platform === p.key);
                      const isLoggedIn = session?.logged_in;
                      const needsLogin = p.needsLoginForSearch && !isLoggedIn;
                      const isSelected = selectedPlatforms.includes(p.key);
                      return (
                        <button
                          key={p.key}
                          type="button"
                          onClick={() => {
                            // Always allow toggle, but show login prompt if needed
                            if (needsLogin && !isSelected) {
                              onLogin(p.key);
                            } else {
                              onPlatformToggle?.(p.key);
                            }
                          }}
                          className={`rounded-xl border px-3 py-1.5 text-xs font-medium transition-colors ${
                            isSelected
                              ? "border-ink bg-ink text-white"
                              : needsLogin
                              ? "border-amber-300 bg-amber-50 text-amber-800 hover:border-amber-400"
                              : "border-ink-200 bg-white text-ink-600 hover:border-ink-400"
                          }`}
                          title={
                            needsLogin && !isSelected
                              ? "Click to log in first"
                              : p.auth === "login recommended"
                              ? isLoggedIn
                                ? "Logged in ✓ (recommended)"
                                : "Login recommended for full access"
                              : "Free access — click to select"
                          }
                        >
                          {p.label}
                          {isLoggedIn && " ✓"}
                          {needsLogin && !isSelected && " 🔒"}
                          {p.auth === "login recommended" && !isLoggedIn && !needsLogin && " ⚡"}
                        </button>
                      );
                    })}
                  </div>
                  {selectedPlatforms.some(
                    (sp) => PLATFORMS.find((p) => p.key === sp)?.needsLoginForSearch && !sessions?.find((s) => s.platform === sp)?.logged_in,
                  ) && (
                    <div className="mt-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
                      <p className="font-medium">Some selected platforms need login:</p>
                      <ul className="mt-1 list-inside list-disc">
                        {selectedPlatforms
                          .map((sp) => PLATFORMS.find((p) => p.key === sp))
                          .filter((p) => p?.needsLoginForSearch && !sessions?.find((s) => s.platform === p!.key)?.logged_in)
                          .map((p) => (
                            <li key={p!.key}>
                              <strong>{p!.label}</strong> — Click the platform button above to log in first.
                            </li>
                          ))}
                      </ul>
                    </div>
                  )}
                </div>
                <div>
                  <p className="mb-2 text-sm font-medium text-ink">Job type preference</p>
                  <div className="flex flex-wrap gap-2">
                    {JOB_TYPES.map((t) => (
                      <button
                        key={t.key}
                        type="button"
                        onClick={() => onRemoteTypeChange?.(t.key)}
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
              </div>
            )}

            <button
              className="btn-dark w-full"
              disabled={!candidateId || loading || selectedPlatforms.length === 0}
              onClick={() => {
                // Check if any selected platform needs login but user isn't logged in
                const needsLogin = selectedPlatforms.some((sp) => {
                  const platform = PLATFORMS.find((p) => p.key === sp);
                  const session = sessions?.find((s) => s.platform === sp);
                  return platform?.needsLoginForSearch && !session?.logged_in;
                });
                if (needsLogin) {
                  onError?.("Some selected platforms require login. Please log in first by clicking the platform button above.");
                  return;
                }
                logClick("click_discover_and_match");
                onAutoApplyArmedChange?.(false);
                onDiscoverAndMatch();
              }}
            >
              {loadingPhase === "discover"
                ? "Searching live jobs…"
                : loadingPhase === "match"
                  ? "Scoring matches…"
                  : "Show available jobs"}
            </button>

            {candidateId && sessions && !sessions.some((s) => s.logged_in) && !discovery && !loading && (
              <p className="text-center text-xs text-amber-700">
                Tip: log in via Step 2 first for complete results on every platform — or search
                now and log in before applying.
              </p>
            )}

            {discovery && !loading && matches.length === 0 && (
              <p className="text-center text-xs text-ink-500">
                Live search finished on every selected platform. Scored matches appear below once
                matching finishes — no stored pool is reused.
              </p>
            )}

            {!candidateId && (
              <p className="text-center text-xs text-ink-400">Upload a resume first</p>
            )}

            {matches.length > 0 && (
              <div className="mt-auto grid grid-cols-3 gap-3">
                <StatCard label="Matches" value={matches.length} />
                <StatCard label="≥ 75%" value={passedCount} />
                <StatCard label="Applied" value={appliedIds.size} />
              </div>
            )}
          </div>
        </div>

        {matches.length > 0 && (
          <div className="mt-16 space-y-6">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
              <div>
                <p className="section-tag">Results</p>
                <h3 className="mt-2 text-3xl font-bold text-ink">Top job matches</h3>
                <p className="mt-1 text-sm text-ink-500">
                  Exact live counts shown above per selected platform. Confirm, then auto-apply.
                </p>
              </div>
              <Link href="/dashboard" className="btn-ghost text-sm">
                Track all →
              </Link>
            </div>

            <div className="flex flex-col gap-3 rounded-3xl border border-ink-200 bg-white p-4 sm:flex-row sm:items-center">
              <label className="flex flex-1 cursor-pointer items-start gap-3 text-sm text-ink-600">
                <input
                  type="checkbox"
                  className="mt-1 h-4 w-4"
                  checked={autoApplyArmed}
                  onChange={(e) => onAutoApplyArmedChange?.(e.target.checked)}
                />
                <span>
                  I reviewed the exact available-job counts and confirm auto-apply to strong
                  matches (≥ 75%).
                </span>
              </label>
              {onAutoApplyAll && (
                <button
                  className="btn-dark shrink-0"
                  disabled={loading || botRunning || !autoApplyArmed}
                  onClick={() => onAutoApplyAll()}
                  title={!autoApplyArmed ? "Confirm counts first" : "Auto-apply strong matches"}
                >
                  {botRunning ? "Bot running…" : "Auto-apply all ≥ 75%"}
                </button>
              )}
            </div>

            <div className="space-y-4">
              {matches.map((match) => (
                <article
                  key={match.job_id}
                  className="flex flex-col gap-4 rounded-3xl border border-ink-200 bg-white p-5 shadow-card lg:flex-row lg:items-center"
                >
                  <ScoreRing score={match.score} passed={match.passed_threshold} />

                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <h4 className="font-semibold text-ink">{match.title}</h4>
                      {appliedIds.has(match.job_id) && (
                        <span className="badge-pass">Applied</span>
                      )}
                      {!match.passed_threshold && (
                        <span className="badge-fail">Below threshold</span>
                      )}
                    </div>
                    <p className="mt-1 text-sm text-ink-500">
                      {match.company}
                      {match.location && ` · ${match.location}`}
                    </p>
                    {match.apply_url && (
                      <a
                        href={match.apply_url}
                        target="_blank"
                        rel="noreferrer"
                        className="mt-2 inline-block text-xs font-medium text-ink underline-offset-2 hover:underline"
                      >
                        View posting ↗
                      </a>
                    )}
                  </div>

                  <div className="flex shrink-0 flex-wrap gap-2">
                    {onBotApply && match.apply_url && (
                      <button
                        className="btn-dark min-w-[120px]"
                        disabled={
                          loading ||
                          botRunning ||
                          appliedIds.has(match.job_id) ||
                          !autoApplyArmed ||
                          jobLoadingStates[match.job_id]?.botApply
                        }
                        title={!autoApplyArmed ? "Confirm counts above first" : "Auto apply"}
                        onClick={() => void handleBotApply(match)}
                      >
                        {jobLoadingStates[match.job_id]?.botApply ? "Applying…" : botRunning ? "Bot running…" : "Auto apply"}
                      </button>
                    )}
                    <button
                      className="btn-outline min-w-[120px]"
                      disabled={loading || jobLoadingStates[match.job_id]?.personalize}
                      onClick={() => void handlePersonalize(match.job_id)}
                    >
                      {jobLoadingStates[match.job_id]?.personalize ? "Personalizing…" : "Personalize"}
                    </button>
                    <button
                      className="btn-outline min-w-[120px]"
                      disabled={loading || appliedIds.has(match.job_id) || jobLoadingStates[match.job_id]?.apply}
                      onClick={() => void handleApply(match)}
                    >
                      {jobLoadingStates[match.job_id]?.apply ? "Applying…" : appliedIds.has(match.job_id) ? "Applied" : "Mark applied"}
                    </button>
                  </div>
                </article>
              ))}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
