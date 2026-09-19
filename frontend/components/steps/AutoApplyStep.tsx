"use client";

import { useState, useEffect } from "react";
import {
  getAutoApplyConfig,
  getBotStatus,
  startBot,
  stopBot,
  signalHumanReady,
  type AutoApplyConfig,
  type BotStatus,
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

export function AutoApplyStep({ candidateId, loading, error, onBack, reviewQueue, onRemoveFromQueue, onClearQueue }: Props) {
  const [config, setConfig] = useState<AutoApplyConfig | null>(null);
  const [botStatus, setBotStatus] = useState<BotStatus | null>(null);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [botRunning, setBotRunning] = useState(false);

  const loadConfig = async () => {
    try {
      const cfg = await getAutoApplyConfig(candidateId);
      setConfig(cfg);
    } catch {}
  };

  useEffect(() => {
    loadConfig();
  }, [candidateId]);

  useEffect(() => {
    if (!botRunning) return;
    const timer = setInterval(async () => {
      try {
        const status = await getBotStatus(candidateId);
        setBotStatus(status);
        if (status.state === "idle" || status.state === "error") {
          setBotRunning(false);
        }
      } catch {}
    }, 2000);
    return () => clearInterval(timer);
  }, [botRunning, candidateId]);

  const handleStartBot = async () => {
    setBotRunning(true);
    try {
      // Convert review queue matches to job format for the bot
      const jobs = reviewQueue.map((match) => ({
        title: match.title,
        company: match.company,
        apply_url: match.apply_url,
        job_id: match.job_id,
      }));
      await startBot(candidateId, { platform: config?.job_titles?.[0] || "linkedin", continuous: false, batchSize: 1, jobs });
    } catch (err) {
      setBotRunning(false);
    }
  };

  const handleStopBot = async () => {
    await stopBot(candidateId);
    setBotRunning(false);
  };

  const approveCurrent = () => {
    const match = reviewQueue[currentIndex];
    if (match) onRemoveFromQueue(match.job_id);
    setCurrentIndex((prev) => Math.min(prev, Math.max(0, reviewQueue.length - 2)));
  };

  const rejectCurrent = () => {
    const match = reviewQueue[currentIndex];
    if (match) onRemoveFromQueue(match.job_id);
    setCurrentIndex((prev) => Math.min(prev, Math.max(0, reviewQueue.length - 2)));
  };

  const approveAll = () => {
    // Send all to bot
    handleStartBot();
    onClearQueue();
  };

  return (
    <div className="max-w-4xl mx-auto">
      <div className="flex items-center justify-between mb-6 flex-wrap gap-3">
        <div>
          <h1 className="text-3xl font-bold text-ink">Review & Approve</h1>
          <p className="text-ink-500">Review personalized applications before the bot applies</p>
        </div>
        <button onClick={onBack} className="btn-outline text-sm">
          ← Back to Matches
        </button>
      </div>

      {error && <div className="alert-error mb-6">{error}</div>}

      {/* Bot Status */}
      <div className={`mb-6 rounded-xl border p-4 ${botRunning ? "border-ink bg-ink text-white" : "border-ink-200 bg-ink-50"}`}>
        <div className="flex items-center justify-between">
          <div>
            <p className="font-semibold">{botRunning ? "Bot Running" : "Bot Idle"}</p>
            <p className="text-sm opacity-75">
              {botStatus?.submitted ?? 0} applied · {botStatus?.skipped ?? 0} skipped · {botStatus?.failed ?? 0} failed
            </p>
          </div>
          <div className="flex gap-2">
            {!botRunning ? (
              <button onClick={handleStartBot} className="btn-dark" disabled={reviewQueue.length === 0}>
                Start Bot ({reviewQueue.length} jobs)
              </button>
            ) : (
              <button onClick={handleStopBot} className="btn-outline bg-red-100 text-red-700 border-red-300">
                Stop Bot
              </button>
            )}
            <button
              onClick={() => signalHumanReady().then(() => setBotStatus((s: any) => ({ ...s, state: "running" })))}
              className="btn-outline text-sm"
            >
              Login Done / CAPTCHA Solved
            </button>
          </div>
        </div>
      </div>

      {/* Review Queue */}
      {reviewQueue.length > 0 ? (
        <div className="space-y-4">
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-xl font-semibold text-ink">Review Queue ({reviewQueue.length} jobs)</h2>
            <div className="flex gap-2">
              <button onClick={rejectCurrent} className="btn-outline text-sm">
                Reject
              </button>
              <button onClick={approveCurrent} className="btn-dark text-sm">
                Approve
              </button>
            </div>
          </div>

          <div className="rounded-2xl border border-ink-200 bg-white p-6">
            <ReviewCard
              match={reviewQueue[currentIndex]}
              onApprove={approveCurrent}
              onReject={rejectCurrent}
            />
          </div>

          <div className="flex items-center justify-between mt-4">
            <button
              onClick={() => setCurrentIndex((i) => Math.max(0, i - 1))}
              disabled={currentIndex === 0}
              className="btn-outline"
            >
              ← Previous
            </button>
            <span className="text-sm text-ink-500">
              {currentIndex + 1} / {reviewQueue.length}
            </span>
            <button
              onClick={() => setCurrentIndex((i) => Math.min(reviewQueue.length - 1, i + 1))}
              disabled={currentIndex >= reviewQueue.length - 1}
              className="btn-outline"
            >
              Next →
            </button>
          </div>

          <div className="mt-6 flex gap-3">
            <button onClick={approveAll} className="btn-dark flex-1">
              Approve All & Start Bot
            </button>
            <button onClick={onClearQueue} className="btn-outline flex-1">
              Clear Queue
            </button>
          </div>
        </div>
      ) : (
        <div className="rounded-2xl border-2 border-dashed border-ink-200 bg-ink-50 p-12 text-center">
          <div className="text-4xl mb-4">📋</div>
          <h3 className="text-xl font-semibold text-ink mb-2">No jobs in review queue</h3>
          <p className="text-ink-500 mb-6">
            Go back to Matches, select jobs above your threshold, then click "Review & Approve"
          </p>
        </div>
      )}

      {/* Kanban Status Board */}
      <div className="mt-10">
        <h2 className="text-2xl font-bold text-ink mb-4">Application Status Board</h2>
        <KanbanBoard candidateId={candidateId} />
      </div>
    </div>
  );
}

function ReviewCard({ match, onApprove, onReject }: { match: MatchResult; onApprove: () => void; onReject: () => void }) {
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold text-ink">{match.title}</h3>
          <p className="text-sm text-ink-500">{match.company} · {match.location}</p>
        </div>
        <span className="px-3 py-1 rounded-full text-sm font-medium bg-blue-100 text-blue-800">
          {match.score}% Match
        </span>
      </div>

      <div className="rounded-xl border border-ink-200 bg-ink-50 p-4">
        <h4 className="font-medium text-ink mb-2">Personalized Resume Preview</h4>
        <p className="text-sm text-ink-600 font-mono bg-white p-3 rounded border border-ink-100">
          {match.title} at {match.company} — Skills highlighted: Python, React, AWS, Docker
        </p>
      </div>

      <div className="flex gap-3">
        <button onClick={onReject} className="btn-outline flex-1">
          Reject
        </button>
        <button onClick={onApprove} className="btn-dark flex-1">
          Approve & Queue for Apply
        </button>
      </div>
    </div>
  );
}

function KanbanBoard({ candidateId }: { candidateId: number }) {
  const columns = [
    { id: "discovered", title: "Discovered", color: "bg-gray-100" },
    { id: "applied", title: "Applied", color: "bg-blue-100" },
    { id: "interviewing", title: "Interviewing", color: "bg-purple-100" },
    { id: "offer", title: "Offer", color: "bg-green-100" },
    { id: "rejected", title: "Rejected", color: "bg-red-100" },
  ];

  // Mock data - in real app, fetch from API
  const mockData = {
    discovered: [{ id: 1, title: "Software Engineer", company: "Stripe" }, { id: 2, title: "Frontend Dev", company: "Vercel" }],
    applied: [{ id: 3, title: "Backend Engineer", company: "Railway" }],
    interviewing: [{ id: 4, title: "Full Stack", company: "Linear" }],
    offer: [],
    rejected: [{ id: 5, title: "DevOps", company: "Fly.io" }],
  };

  return (
    <div className="grid grid-cols-1 md:grid-cols-5 gap-4">
      {columns.map((col) => (
        <div key={col.id} className={`rounded-xl ${col.color} p-4 min-h-[300px]`}>
          <h3 className="font-semibold text-ink mb-3 flex items-center justify-between">
            {col.title}
            <span className="px-2 py-0.5 rounded-full text-xs bg-white/50">
              {mockData[col.id as keyof typeof mockData]?.length ?? 0}
            </span>
          </h3>
          <div className="space-y-2">
            {mockData[col.id as keyof typeof mockData]?.map((job: any) => (
              <div key={job.id} className="bg-white rounded-lg p-3 shadow-sm border border-ink-100">
                <p className="font-medium text-sm text-ink">{job.title}</p>
                <p className="text-xs text-ink-500">{job.company}</p>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}