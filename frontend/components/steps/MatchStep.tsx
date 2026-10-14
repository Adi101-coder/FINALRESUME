"use client";

import { useEffect, useRef, useState } from "react";
import { discoverJobs, listDiscoveredJobs, type MatchResult } from "@/lib/api";

const JOB_TYPES = [
  { key: "", label: "Any" },
  { key: "remote", label: "Remote" },
  { key: "onsite", label: "On-site" },
  { key: "hybrid", label: "Hybrid" },
];

type Props = {
  candidateId: number;
  profile: any;
  onComplete: (matches: MatchResult[]) => void;
  onBack: () => void;
  loading: boolean;
  error: string;
  onError: (err: string) => void;
  onMessage: (msg: string) => void;
  selectedPlatforms: string[];
  remoteType: string;
  onRemoteTypeChange: (t: string) => void;
  searchLocation: string;
  onSearchLocationChange: (v: string) => void;
  allLocations: boolean;
  onAllLocationsChange: (v: boolean) => void;
  onAddToQueue: (match: MatchResult) => void;
  onRemoveFromQueue: (jobId: number) => void;
  queuedJobIds: number[];
};

export function MatchStep({
  candidateId,
  profile,
  onComplete,
  onBack,
  loading,
  error,
  onError,
  onMessage,
  selectedPlatforms,
  remoteType,
  onRemoteTypeChange,
  searchLocation,
  onSearchLocationChange,
  allLocations,
  onAllLocationsChange,
  onAddToQueue,
  onRemoveFromQueue,
  queuedJobIds,
}: Props) {
  const [jobs, setJobs] = useState<MatchResult[]>([]);
  const [discovering, setDiscovering] = useState(false);
  const [info, setInfo] = useState("");
  const autoStarted = useRef(false);

  const handleDiscover = async () => {
    const platforms = selectedPlatforms.length > 0 ? selectedPlatforms : ["linkedin"];
    setDiscovering(true);
    onError("");
    setInfo("");
    try {
      const roleLabel =
        profile?.preferred_roles?.[0] ||
        profile?.experience?.[0]?.role ||
        profile?.skills?.slice(0, 2)?.join(", ") ||
        "your profile";
      const locLabel = allLocations
        ? "all locations"
        : searchLocation.trim() || profile?.location || "your location";
      setInfo(`Searching ${platforms.join(", ")} for "${roleLabel}" in ${locLabel}…`);
      const disc = await discoverJobs(candidateId, platforms, remoteType || undefined, true, {
        location: searchLocation,
        allLocations,
      });
      const results = await listDiscoveredJobs(candidateId);
      setJobs(results);
      const total = disc.total_available ?? disc.candidate_jobs;
      onMessage(
        `${results.length} new jobs ready to apply (already-applied roles hidden). ${total} live on the boards.`,
      );
      setInfo("");
    } catch (err) {
      onError(err instanceof Error ? err.message : "Discovery failed");
    } finally {
      setDiscovering(false);
    }
  };

  useEffect(() => {
    if (autoStarted.current) return;
    autoStarted.current = true;
    void handleDiscover();
    // Fresh search every time this page opens (including Back to Matches).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="max-w-4xl mx-auto">
      <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
        <div>
          <h1 className="text-3xl font-bold text-ink">Discover Jobs</h1>
          <p className="text-ink-500">
            Searches automatically. Each click loads a new batch — jobs you already applied to stay hidden.
          </p>
        </div>
        <button onClick={handleDiscover} disabled={discovering || loading} className="btn-dark">
          {discovering ? "Searching…" : "Discover Jobs"}
        </button>
      </div>

      {error && <div className="alert-error mb-6">{error}</div>}
      {info && <div className="alert-success mb-6">{info}</div>}

      <div className="mb-6 rounded-3xl border border-ink-200 bg-ink-50 p-4">
        <div>
          <p className="mb-2 text-sm font-medium text-ink">Job type</p>
          <div className="flex flex-wrap gap-2">
            {JOB_TYPES.map((t) => (
              <button
                key={t.key}
                type="button"
                onClick={() => onRemoteTypeChange(t.key)}
                className={`rounded-xl border px-3 py-1.5 text-xs font-medium ${
                  remoteType === t.key ? "border-ink bg-ink text-white" : "border-ink-200 bg-white text-ink-600"
                }`}
              >
                {t.label}
              </button>
            ))}
          </div>
        </div>
        <div className="mt-4">
          <p className="mb-2 text-sm font-medium text-ink">Search location</p>
          <div className="flex flex-wrap items-center gap-2">
            <input
              type="text"
              value={searchLocation}
              onChange={(e) => onSearchLocationChange(e.target.value)}
              disabled={allLocations}
              placeholder="e.g. Bengaluru, India"
              className="w-64 max-w-full rounded-xl border border-ink-200 bg-white px-3 py-1.5 text-xs text-ink-600 placeholder:text-ink-400 focus:border-ink focus:outline-none disabled:opacity-50"
            />
            <button
              type="button"
              onClick={() => onAllLocationsChange(!allLocations)}
              className={`rounded-xl border px-3 py-1.5 text-xs font-medium ${
                allLocations ? "border-ink bg-ink text-white" : "border-ink-200 bg-white text-ink-600"
              }`}
            >
              🌍 All locations
            </button>
            <button
              type="button"
              onClick={() => {
                onAllLocationsChange(false);
                onSearchLocationChange("");
              }}
              className="btn-outline text-xs"
            >
              Resume location
            </button>
          </div>
          <p className="mt-1.5 text-xs text-ink-400">
            {allLocations
              ? "Worldwide search — location filter off."
              : searchLocation.trim()
                ? `Searching in ${searchLocation.trim()} (job search only).`
                : "Using the location from your resume."}
          </p>
        </div>
      </div>

      {jobs.length > 0 && (
        <p className="text-xs text-ink-400 mb-4">
          {jobs.length} jobs · {queuedJobIds.length} selected · Auto-apply runs bottom-to-top (last listed job first)
        </p>
      )}

      {jobs.length === 0 ? (
        <div className="text-center py-12 text-ink-500">
          Run discovery to load jobs for your profile.
        </div>
      ) : (
        <div className="space-y-4">
          {jobs.map((match) => (
            <JobCard
              key={match.job_id}
              match={match}
              onAddToQueue={onAddToQueue}
              onRemoveFromQueue={onRemoveFromQueue}
              queued={queuedJobIds.includes(match.job_id)}
            />
          ))}
        </div>
      )}

      <div className="flex gap-3 mt-6">
        <button onClick={onBack} className="btn-outline flex-1">
          ← Back to Platforms
        </button>
        {jobs.length > 0 && (
          <button onClick={() => onComplete(jobs)} className="btn-dark flex-[2]">
            Auto-apply to {jobs.length} jobs →
          </button>
        )}
      </div>
    </div>
  );
}

function JobCard({
  match,
  onAddToQueue,
  onRemoveFromQueue,
  queued,
}: {
  match: MatchResult;
  onAddToQueue: (match: MatchResult) => void;
  onRemoveFromQueue: (jobId: number) => void;
  queued: boolean;
}) {
  return (
    <div
      className={`rounded-2xl border p-5 shadow-sm transition-colors ${
        queued ? "border-green-500 bg-green-50/50 ring-1 ring-green-500/40" : "border-ink-200 bg-white"
      }`}
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 flex-wrap">
            <h3 className="font-semibold text-ink">{match.title}</h3>
            {queued && (
              <span className="px-2 py-0.5 rounded-full text-xs font-semibold bg-green-600 text-white">
                ✓ Selected
              </span>
            )}
          </div>
          <p className="mt-1 text-sm text-ink-500">{match.company} · {match.location}</p>
        </div>
        <div className="flex items-center gap-2">
          <a href={match.apply_url} target="_blank" rel="noreferrer" className="btn-outline text-sm">
            View ↗
          </a>
          {queued ? (
            <button
              onClick={() => onRemoveFromQueue(match.job_id)}
              className="btn-outline text-sm border-green-600 bg-green-600 text-white hover:bg-green-700"
              title="Click to remove from review queue"
            >
              ✓ Queued — remove
            </button>
          ) : (
            <button onClick={() => onAddToQueue(match)} className="btn-dark text-sm">
              + Queue
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
