"use client";

/**
 * Archived match-scoring UI (threshold slider + per-dimension score breakdown).
 * Kept for reference; the live flow uses listDiscoveredJobs without scoring.
 */

import { useState } from "react";
import type { MatchResult } from "@/lib/api";

export function ScoredMatchCard({
  match,
  onAddToQueue,
  onRemoveFromQueue,
  queued,
}: {
  match: MatchResult;
  profile: unknown;
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
            <span
              className={`px-2 py-0.5 rounded-full text-xs font-medium ${
                match.score >= 85
                  ? "bg-green-100 text-green-800"
                  : match.score >= 75
                    ? "bg-blue-100 text-blue-800"
                    : "bg-amber-100 text-amber-800"
              }`}
            >
              {match.score}%
            </span>
          </div>
          <p className="mt-1 text-sm text-ink-500">{match.company} · {match.location}</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setExpanded(!expanded)} className="btn-outline text-sm">
            {expanded ? "Hide" : "Score ↓"}
          </button>
          {queued ? (
            <button onClick={() => onRemoveFromQueue(match.job_id)} className="btn-outline text-sm">
              Remove
            </button>
          ) : (
            <button onClick={() => onAddToQueue(match)} className="btn-dark text-sm">
              + Queue
            </button>
          )}
        </div>
      </div>
      {expanded && (
        <div className="mt-4 pt-4 border-t border-ink-100 space-y-3">
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
      )}
    </div>
  );
}
