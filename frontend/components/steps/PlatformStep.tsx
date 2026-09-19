"use client";

import { useEffect, useState } from "react";
import {
  startBotLogin,
  getAutoApplyConfig,
  getBotStatus,
  signalHumanReady,
} from "@/lib/api";

const PLATFORMS = [
  { key: "linkedin", label: "LinkedIn", auth: "login required", needsLoginForSearch: true },
  { key: "indeed", label: "Indeed", auth: "login recommended", needsLoginForSearch: false },
  { key: "foundit", label: "Foundit", auth: "login recommended", needsLoginForSearch: false },
  { key: "naukri", label: "Naukri.com", auth: "login recommended", needsLoginForSearch: false },
  { key: "ziprecruiter", label: "ZipRecruiter", auth: "free", needsLoginForSearch: false },
  { key: "glassdoor", label: "Glassdoor", auth: "login recommended", needsLoginForSearch: false },
  { key: "greenhouse", label: "Greenhouse", auth: "free", needsLoginForSearch: false },
  { key: "remotive", label: "Remotive", auth: "free", needsLoginForSearch: false },
  { key: "remoteok", label: "RemoteOK", auth: "free", needsLoginForSearch: false },
  { key: "arbeitnow", label: "Arbeitnow", auth: "free", needsLoginForSearch: false },
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
  jobSources: any[];
  selectedPlatforms: string[];
  onSelectedChange: (p: string[]) => void;
  remoteType: string;
  onRemoteTypeChange: (t: string) => void;
  onComplete: () => void;
  onBack: () => void;
  loading: boolean;
  error: string;
  onError: (err: string) => void;
  onMessage: (msg: string) => void;
};

export function PlatformStep({
  candidateId,
  selectedPlatforms,
  onSelectedChange,
  remoteType,
  onRemoteTypeChange,
  onComplete,
  onBack,
  loading,
  error,
  onError,
  onMessage,
}: Props) {
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

  const toggle = (platform: string) => {
    const next = selectedPlatforms.includes(platform)
      ? selectedPlatforms.filter((p) => p !== platform)
      : [...selectedPlatforms, platform];
    onSelectedChange(next);
  };

  const selectAll = () => onSelectedChange(PLATFORMS.map((p) => p.key));
  const clearAll = () => onSelectedChange([]);

  const [loginBusy, setLoginBusy] = useState(false);

  const handleLogin = async (platform: string) => {
    setLoginPlatform(platform);
    setLoginBusy(true);
    setInfo("");
    onError("");
    try {
      await startBotLogin(candidateId, platform);
      // Poll bot status so backend failures (e.g. no display on hosted server) surface immediately
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
      setInfo(
        `Browser opened for ${platform}. Complete sign-in (Google OAuth if shown), then press "Login done". You stay on this page.`,
      );
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
      onMessage("Logged in ✓ — session saved.");
    } catch (err) {
      onError(err instanceof Error ? err.message : "Signal failed");
    } finally {
      setLoginPlatform(null);
    }
  };

  return (
    <div className="max-w-3xl mx-auto">
      <h1 className="text-3xl font-bold text-ink mb-2">Select Platforms</h1>
      <p className="text-ink-500 mb-8">
        Free boards search instantly. LinkedIn needs login. Others work without login, login only
        improves results.
      </p>

      {error && <div className="alert-error mb-6">{error}</div>}
      {info && <div className="alert-success mb-6">{info}</div>}

      <div className="mb-6 flex flex-wrap gap-2">
        <button onClick={selectAll} className="btn-outline text-sm">Select all</button>
        <button onClick={clearAll} className="btn-outline text-sm">Clear</button>
        <button onClick={loadSessions} className="btn-outline text-sm">Refresh login status</button>
      </div>

      <div className="mb-8">
        <h3 className="font-semibold text-ink mb-4">Platforms ({selectedPlatforms.length} selected)</h3>
        <div className="flex flex-wrap gap-2">
          {PLATFORMS.map((p) => {
            const session = sessions?.find((s: any) => s.platform === p.key);
            const isLoggedIn = session?.logged_in;
            const isSelected = selectedPlatforms.includes(p.key);
            const needsLogin = p.needsLoginForSearch && !isLoggedIn;
            return (
              <div key={p.key} className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => toggle(p.key)}
                  className={`rounded-xl border px-3 py-2 text-sm font-medium transition-colors ${
                    isSelected
                      ? "border-ink bg-ink text-white"
                      : "border-ink-200 bg-white text-ink-600 hover:border-ink-400"
                  }`}
                  title={isLoggedIn ? `${p.label} — logged in ✓` : `${p.label} — ${p.auth}`}
                >
                  {p.label}
                  {isLoggedIn && " ✓"}
                  {needsLogin && " 🔒"}
                </button>
                {needsLogin && (
                  <button
                    type="button"
                    onClick={() => handleLogin(p.key)}
                    disabled={loginBusy}
                    className="rounded-xl border border-amber-300 bg-amber-50 px-2 py-2 text-xs font-semibold text-amber-800 hover:border-amber-400 disabled:opacity-50"
                  >
                    {loginPlatform === p.key && loginBusy ? "Opening…" : loginPlatform === p.key ? "Log in" : "Log in"}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      </div>

      <div className="mb-8">
        <h3 className="font-semibold text-ink mb-3">Job type</h3>
        <div className="flex flex-wrap gap-2">
          {JOB_TYPES.map((t) => (
            <button
              key={t.key}
              type="button"
              onClick={() => onRemoteTypeChange(t.key)}
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

      <p className="mb-6 text-xs text-ink-400">
        Note: interactive browser login opens on the machine running the backend. On the hosted
        demo it may fail — free boards (Remotive, RemoteOK, Arbeitnow, Greenhouse, ZipRecruiter)
        search without any login.
      </p>

      {loginPlatform && (
        <div className="mb-6 rounded-xl border border-amber-200 bg-amber-50 p-4 text-amber-800">
          <p className="font-medium">Logging in to {loginPlatform}…</p>
          <ul className="mt-2 list-disc list-inside space-y-1 text-sm">
            <li>Complete sign-in in the opened browser window</li>
            <li>If Google OAuth appears, finish it there</li>
            <li>You stay on this page — no redirect</li>
          </ul>
          <button onClick={handleLoginDone} className="btn-dark mt-4">
            Login Done — Save Session
          </button>
        </div>
      )}

      <div className="flex gap-3">
        <button className="btn-outline flex-1" onClick={onBack}>
          ← Back
        </button>
        <button
          className="btn-dark flex-[2]"
          disabled={loading || selectedPlatforms.length === 0}
          onClick={onComplete}
        >
          Continue to Matches →
        </button>
      </div>
    </div>
  );
}
