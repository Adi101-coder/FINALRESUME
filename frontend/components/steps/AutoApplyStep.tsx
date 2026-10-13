"use client";

import { useState, useEffect, useRef } from "react";
import {
  getAutoApplyConfig,
  getBotStatus,
  startBot,
  stopBot,
  signalHumanReady,
  signalGoBack,
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
  const [startError, setStartError] = useState("");
  const autoStarted = useRef(false);

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

  const handleStartBot = async (jobsOverride?: typeof reviewQueue) => {
    const source = jobsOverride ?? reviewQueue;
    setBotRunning(true);
    setStartError("");
    try {
      const jobs = source
        .filter((match) => match.apply_url)
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
    // Start once when this step opens, using the jobs queued from Matches.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
          <h1 className="text-3xl font-bold text-ink">Auto-apply</h1>
          <p className="text-ink-500">The bot starts on its own and submits LinkedIn applications for these jobs</p>
        </div>
        <button onClick={onBack} className="btn-outline text-sm">
          ← Back to Matches
        </button>
      </div>

      {(error || startError) && <div className="alert-error mb-6">{startError || error}</div>}

      {/* Bot Status */}
      <div className={`mb-6 rounded-xl border p-4 ${botRunning ? "border-ink bg-ink text-white" : "border-ink-200 bg-ink-50"}`}>
        <div className="flex items-center justify-between">
          <div>
            <p className="font-semibold">{botRunning ? "Bot Running" : "Bot Idle"}</p>
            <p className="text-sm opacity-75">
              {botStatus?.submitted ?? 0} applied · {botStatus?.skipped ?? 0} skipped · {botStatus?.failed ?? 0} failed
            </p>
            {botStatus?.recent_events?.length ? (
              <ul className="mt-2 max-h-28 overflow-y-auto text-xs opacity-90 space-y-1">
                {botStatus.recent_events
                  .filter((e) => e.result?.status && e.result.status !== "submitted")
                  .slice(-4)
                  .map((e, i) => (
                    <li key={i}>
                      {String(e.result?.title ?? "Job")}: {String(e.result?.message ?? e.message ?? "")}
                    </li>
                  ))}
              </ul>
            ) : null}
          </div>
          <div className="flex gap-2">
            {!botRunning ? (
              <button onClick={() => handleStartBot()} className="btn-dark">
                Start Bot{reviewQueue.length ? ` (${reviewQueue.length} jobs)` : ""}
              </button>
            ) : (
              <button onClick={handleStopBot} className="btn-outline bg-red-100 text-red-700 border-red-300">
                Stop Bot
              </button>
            )}
            <button
              onClick={() => void signalGoBack()}
              className="btn-outline text-sm"
            >
              Go back (30s review)
            </button>
            <button
              onClick={() => signalHumanReady().then(() => setBotStatus((s: any) => ({ ...s, state: "running" })))}
              className="btn-outline text-sm"
            >
              Done (login / CAPTCHA / manual fields)
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
            Go back to Matches, select jobs above your threshold, then click &ldquo;Review &amp; Approve&rdquo;
          </p>
        </div>
      )}
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
