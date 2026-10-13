"use client";

import { useState } from "react";
import { discoverJobs, matchJobs, type MatchResult } from "@/lib/api";

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
  const [matches, setMatches] = useState<MatchResult[]>([]);
  const [discovering, setDiscovering] = useState(false);
  const [threshold, setThreshold] = useState(40);
  const [info, setInfo] = useState("");

  const handleDiscover = async () => {
    const platforms = selectedPlatforms.length > 0 ? selectedPlatforms : ["linkedin"];
    setDiscovering(true);
    onError("");
    setInfo("");
    try {
      const roleLabel = profile?.preferred_roles?.[0] || profile?.experience?.[0]?.role || profile?.skills?.slice(0, 2)?.join(", ") || "your profile";
      const locLabel = allLocations ? "all locations" : searchLocation.trim() || profile?.location || "your location";
      setInfo(`Searching ${platforms.join(", ")} for "${roleLabel}" in ${locLabel}…`);
      const disc = await discoverJobs(candidateId, platforms, remoteType || undefined, true, {
        location: searchLocation,
        allLocations,
      });
      const results = await matchJobs(candidateId);
      setMatches(results);
      const total = disc.total_available ?? disc.candidate_jobs;
      onMessage(`${total} live jobs · ${results.length} scored matches.`);
      setInfo("");
    } catch (err) {
      onError(err instanceof Error ? err.message : "Discovery failed");
    } finally {
      setDiscovering(false);
    }
  };

  const filteredMatches = matches.filter((m) => m.score >= threshold);

  return (
    <div className="max-w-4xl mx-auto">
      <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
        <div>
          <h1 className="text-3xl font-bold text-ink">View Matches</h1>
          <p className="text-ink-500">Discover live jobs matched to your resume.</p>
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

      <div className="mb-6">
        <label className="block text-sm font-medium text-ink mb-2">
          Match Threshold: <span className="font-mono">{threshold}%</span>
        </label>
        <input
          type="range"
          min="20"
          max="95"
          step="5"
          value={threshold}
          onChange={(e) => setThreshold(Number(e.target.value))}
          className="w-full h-2 bg-ink-100 rounded-lg appearance-none cursor-pointer accent-ink"
        />
        <p className="text-xs text-ink-400 mt-1">
          Showing {filteredMatches.length} of {matches.length} matches · {queuedJobIds.length} selected for review
        </p>
      </div>

      {filteredMatches.length === 0 && matches.length > 0 ? (
        <div className="text-center py-12 text-ink-500">
          No matches above {threshold}{'%'}. Lower the threshold to see more.
        </div>
      ) : (
        <div className="space-y-4">
          {filteredMatches.map((match) => (
            <MatchCard
              key={match.job_id}
              match={match}
              profile={profile}
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
        {filteredMatches.length > 0 && (
          <button
            onClick={() => onComplete(filteredMatches)}
            className="btn-dark flex-[2]"
          >
            Auto-apply to {filteredMatches.length} jobs →
          </button>
        )}
      </div>
    </div>
  );
}

function MatchCard({
  match,
  profile,
  onAddToQueue,
  onRemoveFromQueue,
  queued,
}: {
  match: MatchResult;
  profile: any;
  onAddToQueue: (match: MatchResult) => void;
  onRemoveFromQueue: (jobId: number) => void;
  queued: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const scoreBreakdown = [
    { label: "Relevance", score: match.relevance_score ?? 0, color: "bg-rose-500" },
    { label: "Skills", score: match.skills_score, color: "bg-green-500" },
    { label: "Experience", score: match.experience_score, color: "bg-blue-500" },
    { label: "Embedding Fit", score: match.embedding_score, color: "bg-purple-500" },
    { label: "Location", score: match.location_score, color: "bg-orange-500" },
  ];
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
            <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
              match.score >= 85 ? "bg-green-100 text-green-800" :
              match.score >= 75 ? "bg-blue-100 text-blue-800" : "bg-amber-100 text-amber-800"
            }`}>{match.score}%</span>
            {queued && (
              <span className="px-2 py-0.5 rounded-full text-xs font-semibold bg-green-600 text-white">
                ✓ Selected
              </span>
            )}
          </div>
          <p className="mt-1 text-sm text-ink-500">{match.company} · {match.location}</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setExpanded(!expanded)} className="btn-outline text-sm">
            {expanded ? "Hide" : "Score ↓"}
          </button>
          <a href={match.apply_url} target="_blank" rel="noreferrer" className="btn-outline text-sm">View ↗</a>
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
      {expanded && (
        <div className="mt-4 pt-4 border-t border-ink-100">
          <div className="space-y-3">
            {scoreBreakdown.map((s) => {
              const pct = Math.min(100, Math.max(0, Math.round(s.score)));
              return (
                <div key={s.label} className="flex items-center gap-3">
                  <span className="w-24 text-sm text-ink-600">{s.label}</span>
                  <div className="flex-1 h-2 bg-ink-100 rounded-full overflow-hidden">
                    <div className={`h-full ${s.color} rounded-full`} style={{ width: `${pct}%` }} />
                  </div>
                  <span className="w-10 text-sm font-mono text-ink">{pct}%</span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
