"use client";

import { useEffect, useState } from "react";
import {
  discoverJobs,
  matchJobs,
  startBotLogin,
  getAutoApplyConfig,
  getBotStatus,
  signalHumanReady,
  type DiscoveryResult,
  type MatchResult,
} from "@/lib/api";

const PLATFORMS = [
  { key: "linkedin", label: "LinkedIn", needsLoginForSearch: true },
  { key: "indeed", label: "Indeed", needsLoginForSearch: false },
  { key: "foundit", label: "Foundit", needsLoginForSearch: false },
  { key: "naukri", label: "Naukri.com", needsLoginForSearch: false },
  { key: "ziprecruiter", label: "ZipRecruiter", needsLoginForSearch: false },
  { key: "glassdoor", label: "Glassdoor", needsLoginForSearch: false },
  { key: "greenhouse", label: "Greenhouse", needsLoginForSearch: false },
  { key: "remotive", label: "Remotive", needsLoginForSearch: false },
  { key: "remoteok", label: "RemoteOK", needsLoginForSearch: false },
  { key: "arbeitnow", label: "Arbeitnow", needsLoginForSearch: false },
];

const JOB_TYPES = [
  { key: "", label: "Any" },
  { key: "remote", label: "Remote" },
  { key: "onsite", label: "On-site" },
  { key: "hybrid", label: "Hybrid" },
];

type Props = {
  candidateId: number;
  profile: any;
  onComplete: () => void;
  onBack: () => void;
  loading: boolean;
  error: string;
  onError: (err: string) => void;
  onMessage: (msg: string) => void;
  selectedPlatforms: string[];
  onSelectedChange: (p: string[]) => void;
  remoteType: string;
  onRemoteTypeChange: (t: string) => void;
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
  onSelectedChange,
  remoteType,
  onRemoteTypeChange,
}: Props) {
  const [discovery, setDiscovery] = useState<DiscoveryResult | null>(null);
  const [matches, setMatches] = useState<MatchResult[]>([]);
  const [discovering, setDiscovering] = useState(false);
  const [threshold, setThreshold] = useState(60);
  const [sessions, setSessions] = useState<any[] | null>(null);
  const [loginPlatform, setLoginPlatform] = useState<string | null>(null);
  const [info, setInfo] = useState("");

  const loadSessions = async () => {
    try {
      const cfg = await getAutoApplyConfig(candidateId);
      setSessions(cfg.sessions || null);
    } catch {}
  };

  useEffect(() => {
    loadSessions();
  }, [candidateId]);

  const toggle = (p: string) => {
    const next = selectedPlatforms.includes(p)
      ? selectedPlatforms.filter((x) => x !== p)
      : [...selectedPlatforms, p];
    onSelectedChange(next);
  };

  const [loginBusy, setLoginBusy] = useState(false);
  const [isLocal, setIsLocal] = useState(false);

  useEffect(() => {
    fetch("http://localhost:8000/health", { mode: "no-cors" })
      .then(() => setIsLocal(true))
      .catch(() => setIsLocal(false));
  }, []);

  const handleLogin = async (platform: string) => {
    setLoginPlatform(platform);
    setLoginBusy(true);
    setInfo("");
    onError("");
    try {
      await startBotLogin(candidateId, platform);
      for (let i = 0; i < 10; i += 1) {
        await new Promise((r) => setTimeout(r, 1500));
        try {
          const s = await getBotStatus(candidateId);
          if (s.state === "error") {
            onError(s.error || `${platform} login failed on server. Free boards work without login.`);
            setLoginPlatform(null);
            setLoginBusy(false);
            return;
          }
          if ((s.recent_events || []).length > 0) break;
        } catch {
          break;
        }
      }
      if (!isLocal) {
        setInfo(
          `${platform} login started on the server. On the hosted version, browser login doesn't work (no display). Run locally for full login support, or use free boards (Remotive, RemoteOK, Arbeitnow, Greenhouse, ZipRecruiter) which work without login.`,
        );
      } else {
        setInfo(`Browser opened for ${platform}. Finish sign-in there, then press "Login done". You stay on this page.`);
      }
    } catch (err) {
      onError(err instanceof Error ? err.message : "Login failed");
      setLoginPlatform(null);
    } finally {
      setLoginBusy(false);
    }
  };

  const handleLoginDone = async () => {
    try {
      await signalHumanReady();
      setInfo("Session saving…");
      await new Promise((r) => setTimeout(r, 2000));
      await loadSessions();
      setInfo("Session saved ✓");
      onMessage("Logged in ✓");
    } catch (err) {
      onError(err instanceof Error ? err.message : "Signal failed");
    } finally {
      setLoginPlatform(null);
    }
  };

  const handleDiscover = async () => {
    if (selectedPlatforms.length === 0) {
      onError("Select at least one platform first.");
      return;
    }
    setDiscovering(true);
    onError("");
    setInfo("");
    try {
      const roleLabel = profile?.preferred_roles?.[0] || profile?.experience?.[0]?.role || profile?.skills?.slice(0, 2)?.join(", ") || "your profile";
      setInfo(`Searching ${selectedPlatforms.join(", ")} for "${roleLabel}"…`);
      const disc = await discoverJobs(candidateId, selectedPlatforms, remoteType || undefined, true);
      setDiscovery(disc);
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
          <p className="text-ink-500">Pick platforms, log in only where needed, then discover.</p>
        </div>
        <button onClick={handleDiscover} disabled={discovering || loading} className="btn-dark">
          {discovering ? "Searching…" : "Discover Jobs"}
        </button>
      </div>

      {error && <div className="alert-error mb-6">{error}</div>}
      {info && <div className="alert-success mb-6">{info}</div>}

      <div className="mb-6 rounded-3xl border border-ink-200 bg-ink-50 p-4">
        <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
          <p className="text-sm font-medium text-ink">Platforms ({selectedPlatforms.length} selected)</p>
          <div className="flex gap-2">
            <button onClick={() => onSelectedChange(PLATFORMS.map((p) => p.key))} className="btn-outline text-xs">Select all</button>
            <button onClick={() => onSelectedChange([])} className="btn-outline text-xs">Clear</button>
            <button onClick={loadSessions} className="btn-outline text-xs">Refresh login</button>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {PLATFORMS.map((p) => {
            const isLoggedIn = sessions?.find((s: any) => s.platform === p.key)?.logged_in;
            const isSelected = selectedPlatforms.includes(p.key);
            const needsLogin = p.needsLoginForSearch && !isLoggedIn;
            return (
              <div key={p.key} className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => toggle(p.key)}
                  className={`rounded-xl border px-3 py-1.5 text-xs font-medium ${
                    isSelected ? "border-ink bg-ink text-white" : "border-ink-200 bg-white text-ink-600"
                  }`}
                >
                  {p.label} {isLoggedIn && "✓"} {needsLogin && "🔒"}
                </button>
                {needsLogin && (
                  <button
                    type="button"
                    onClick={() => handleLogin(p.key)}
                    disabled={loginBusy}
                    className="rounded-xl border border-amber-300 bg-amber-50 px-2 py-1.5 text-xs font-semibold text-amber-800 disabled:opacity-50"
                  >
                    {loginPlatform === p.key && loginBusy ? "Opening…" : "Log in"}
                  </button>
                )}
              </div>
            );
          })}
        </div>
        <div className="mt-4">
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
        {loginPlatform && (
          <div className="mt-4 rounded-xl border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">
            <p className="font-medium">Logging in to {loginPlatform}… finish in the browser window. You stay here.</p>
            <button onClick={handleLoginDone} className="btn-dark mt-2 text-sm">Login Done — Save Session</button>
          </div>
        )}
      </div>

      {discovery && (
        <div className="mb-6 rounded-xl border border-ink-200 bg-ink-50 p-4">
          <p className="font-semibold text-ink">
            {discovery.total_available ?? discovery.candidate_jobs} live jobs for “{discovery.role ?? "your role"}”
          </p>
          <p className="text-sm text-ink-500 mt-1">
            {Object.entries(discovery.platform_counts ?? {})
              .map(([p, c]: any) => `${p}: ${c.available} available (${c.fetched} fetched${c.errors ? ` · ${c.errors} error` : ""})`)
              .join(" · ")}
          </p>
          {(discovery.search_queries ?? []).length > 0 && (
            <p className="mt-1 text-xs text-ink-400">Queries: {(discovery.search_queries ?? []).slice(0, 5).join(" · ")}</p>
          )}
        </div>
      )}

      <div className="mb-6">
        <label className="block text-sm font-medium text-ink mb-2">
          Match Threshold: <span className="font-mono">{threshold}%</span>
        </label>
        <input
          type="range"
          min="50"
          max="95"
          step="5"
          value={threshold}
          onChange={(e) => setThreshold(Number(e.target.value))}
          className="w-full h-2 bg-ink-100 rounded-lg appearance-none cursor-pointer accent-ink"
        />
        <p className="text-xs text-ink-400 mt-1">Showing {filteredMatches.length} of {matches.length} matches</p>
      </div>

      {filteredMatches.length === 0 && matches.length > 0 ? (
        <div className="text-center py-12 text-ink-500">
          No matches above {threshold}%. Lower the threshold to see more.
        </div>
      ) : (
        <div className="space-y-4">
          {filteredMatches.map((match) => (
            <MatchCard key={match.job_id} match={match} profile={profile} />
          ))}
        </div>
      )}

      <div className="flex gap-3 mt-6">
        <button onClick={onBack} className="btn-outline flex-1">
          ← Back to Platforms
        </button>
        {filteredMatches.length > 0 && (
          <button onClick={onComplete} className="btn-dark flex-[2]">
            Review & Approve Applications →
          </button>
        )}
      </div>
    </div>
  );
}

function MatchCard({ match, profile }: { match: MatchResult; profile: any }) {
  const [expanded, setExpanded] = useState(false);
  const scoreBreakdown = [
    { label: "Skills", score: match.skills_score, color: "bg-green-500" },
    { label: "Experience", score: match.experience_score, color: "bg-blue-500" },
    { label: "Embedding Fit", score: match.embedding_score, color: "bg-purple-500" },
    { label: "Location", score: match.location_score, color: "bg-orange-500" },
  ];
  return (
    <div className="rounded-2xl border border-ink-200 bg-white p-5 shadow-sm">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 flex-wrap">
            <h3 className="font-semibold text-ink">{match.title}</h3>
            <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${
              match.score >= 85 ? "bg-green-100 text-green-800" :
              match.score >= 75 ? "bg-blue-100 text-blue-800" : "bg-amber-100 text-amber-800"
            }`}>{match.score}%</span>
          </div>
          <p className="mt-1 text-sm text-ink-500">{match.company} · {match.location}</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setExpanded(!expanded)} className="btn-outline text-sm">
            {expanded ? "Hide" : "Score ↓"}
          </button>
          <a href={match.apply_url} target="_blank" rel="noreferrer" className="btn-outline text-sm">View ↗</a>
        </div>
      </div>
      {expanded && (
        <div className="mt-4 pt-4 border-t border-ink-100">
          <div className="space-y-3">
            {scoreBreakdown.map((s) => (
              <div key={s.label} className="flex items-center gap-3">
                <span className="w-24 text-sm text-ink-600">{s.label}</span>
                <div className="flex-1 h-2 bg-ink-100 rounded-full overflow-hidden">
                  <div className={`h-full ${s.color} rounded-full`} style={{ width: `${s.score}%` }} />
                </div>
                <span className="w-10 text-sm font-mono text-ink">{s.score}%</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
