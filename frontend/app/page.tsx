"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AutoApplyPanel } from "@/components/landing/AutoApplyPanel";
import { LandingFooter } from "@/components/landing/LandingFooter";
import { PhoneMockup } from "@/components/landing/PhoneMockup";
import { WorkflowPanel } from "@/components/landing/WorkflowPanel";
import {
  checkBackendHealth,
  discoverJobs,
  getApplications,
  getAutoApplyConfig,
  getJobSources,
  matchJobs,
  personalizeResume,
  createApplication,
  uploadResume,
  startBot,
  startBotLogin,
  signalHumanReady,
  getBotStatus,
  API_URL,
  type CandidateProfile,
  type DiscoveryResult,
  type JobSource,
  type MatchResult,
} from "@/lib/api";

const ALL_PLATFORMS = ["linkedin", "indeed", "foundit", "naukri"];

const FEATURES = [
  {
    title: "Smart Job Discovery",
    desc: "Search terms built from your roles, skills, and experience — not generic tech keywords.",
  },
  {
    title: "AI Match Scoring",
    desc: "Every role scored 0–100 on skills, experience, embedding fit, and location alignment.",
  },
  {
    title: "Application Tracking",
    desc: "Personalize your resume per job and track every application from one dashboard.",
  },
];

const DAILY_FEATURES = [
  "Live job discovery from LinkedIn, Indeed, Foundit + Naukri.com",
  "Profile-based queries that adapt to any industry",
  "ATS-aware resume personalization per role",
  "Persistent application history and analytics",
];

const FOCUS_BENEFITS = [
  "One resume upload powers the entire pipeline",
  "Parallel discovery across job boards and company career pages (Greenhouse)",
  "Threshold-based matching so you focus on strong fits",
  "Export-ready PDFs tailored to each posting",
];

const TRUSTED = ["LinkedIn", "Indeed", "Foundit", "Naukri.com"];

const COMMUNITY = ["AK", "JM", "SR", "PL", "DC", "EV", "TH", "NW"];

export default function HomePage() {
  const [loading, setLoading] = useState(false);
  const [candidateId, setCandidateId] = useState<number | null>(null);
  const [profile, setProfile] = useState<CandidateProfile | null>(null);
  const [discovery, setDiscovery] = useState<DiscoveryResult | null>(null);
  const [matches, setMatches] = useState<MatchResult[]>([]);
  const [appliedIds, setAppliedIds] = useState<Set<number>>(new Set());
  const [jobSources, setJobSources] = useState<JobSource[]>([]);
  const [pipelineStep, setPipelineStep] = useState(0);
  const [loadingPhase, setLoadingPhase] = useState<"" | "discover" | "match">("");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [botRunning, setBotRunning] = useState(false);
  const [selectedPlatforms, setSelectedPlatforms] = useState<string[]>([
    "linkedin",
    "indeed",
    "foundit",
    "naukri",
  ]);
  const [remoteType, setRemoteType] = useState<string>("");
  const [autoApplyArmed, setAutoApplyArmed] = useState(false);
  const [sessions, setSessions] = useState<
    { platform: string; logged_in: boolean; updated_at: string | null }[] | null
  >(null);
  const [loginPlatform, setLoginPlatform] = useState<string | null>(null);

  function handlePlatformToggle(platform: string) {
    setSelectedPlatforms((prev) =>
      prev.includes(platform) ? prev.filter((p) => p !== platform) : [...prev, platform],
    );
  }

  async function refreshApplications(id: number) {
    try {
      const apps = await getApplications(id);
      setAppliedIds(new Set(apps.map((a) => a.job_id)));
    } catch {
      /* ignore */
    }
  }

  async function refreshSessions(id: number) {
    try {
      const cfg = await getAutoApplyConfig(id);
      setSessions(cfg.sessions ?? null);
    } catch {
      /* ignore */
    }
  }

  useEffect(() => {
    void checkBackendHealth()
      .then((ok) => {
        if (!ok) setError(`Backend health check failed (${API_URL})`);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Backend unreachable");
      });
    void getJobSources()
      .then((r) => setJobSources(r.sources))
      .catch((err) => {
        const msg = err instanceof Error ? err.message : "Failed to load job sources";
        setError((prev) => prev || msg);
      });
  }, []);

  useEffect(() => {
    const stored = window.localStorage.getItem("candidateId");
    if (stored) {
      const id = Number(stored);
      setCandidateId(id);
      void getApplications(id)
        .then((apps) => setAppliedIds(new Set(apps.map((a) => a.job_id))))
        .catch(() => {});
      void refreshSessions(id);
      void getBotStatus(id)
        .then((s) => setBotRunning(s.state === "running" || s.state === "stopping"))
        .catch(() => {});
    }
  }, []);

  async function handleUpload(file: File) {
    setLoading(true);
    setError("");
    setMessage("");
    setMatches([]);
    setDiscovery(null);
    try {
      const result = await uploadResume(file);
      setCandidateId(result.candidate_id);
      window.localStorage.setItem("candidateId", String(result.candidate_id));
      setProfile(result.profile);
      setSessions(null);
      void refreshSessions(result.candidate_id);
      setPipelineStep(1);
      setMessage(`Resume parsed for ${result.profile.name || "candidate"}. Now log in to platforms in Step 2.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleLogin(platform: string) {
    if (!candidateId) return;
    setLoginPlatform(platform);
    setError("");
    try {
      await startBotLogin(candidateId, platform);
      setMessage(
        `Opening your Chrome on ${platform} — already-logged-in sessions are picked up automatically. Otherwise log in, then press “Login done”.`,
      );
      // Wait for the login run to finish (human logs in + presses Login done).
      for (let i = 0; i < 100; i += 1) {
        await new Promise((r) => setTimeout(r, 3000));
        try {
          const s = await getBotStatus(candidateId);
          if (s.state === "idle" || s.state === "error") break;
        } catch {
          break;
        }
      }
      await refreshSessions(candidateId);
      setMessage(`Logged in — searching live jobs for your role…`);
      await handleDiscoverAndMatch();
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Login failed";
      setError(
        msg.includes("already running")
          ? "Another login/apply is already running — wait for it to finish, then log in to the next platform."
          : msg,
      );
    } finally {
      setLoginPlatform(null);
    }
  }

  async function handleLoginDone() {
    try {
      await signalHumanReady();
      setMessage("Session saving — your jobs will appear in a moment…");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Signal failed");
    }
  }

  async function handleDiscoverAndMatch() {
    if (!candidateId) return;
    setLoading(true);
    setLoadingPhase("discover");
    setError("");
    setMatches([]);
    setAutoApplyArmed(false);
    setPipelineStep(2);
    try {
      // Fresh live search on every selected platform — no stored/static pool.
      const disc = await discoverJobs(candidateId, selectedPlatforms, remoteType || undefined, true);
      setDiscovery(disc);
      setPipelineStep(3);
      setLoadingPhase("match");
      const results = await matchJobs(candidateId);
      setMatches(results);
      setPipelineStep(4);
      const counts = disc.platform_counts ?? {};
      const perPlatform = selectedPlatforms
        .map((p) => {
          const c = counts[p];
          return `${p}: ${c ? c.available : 0} available`;
        })
        .join(" · ");
      const total = disc.total_available ?? disc.candidate_jobs;
      const role = disc.role ?? disc.search_queries[0] ?? "this role";
      setMessage(
        `${total} live jobs for “${role}” — ${perPlatform} · ${results.length} scored matches below. Arm auto-apply to proceed.`,
      );
    } catch (err) {
      setError(err instanceof Error ? err.message : "Discovery failed");
    } finally {
      setLoading(false);
      setLoadingPhase("");
    }
  }

  async function handlePersonalize(jobId: number) {
    if (!candidateId) return;
    setLoading(true);
    setError("");
    try {
      const result = await personalizeResume(candidateId, jobId);
      setMessage(`Optimized resume ready · ATS ${result.ats_report.overall_score}%`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Personalization failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleApply(match: MatchResult) {
    if (!candidateId) return;
    setLoading(true);
    setError("");
    try {
      await createApplication(candidateId, match.job_id, match.score);
      setAppliedIds((prev) => new Set(prev).add(match.job_id));
      setMessage(`Tracked application to ${match.title} at ${match.company}`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Apply failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleBotApply(match: MatchResult) {
    if (!candidateId || !match.apply_url) return;
    if (!autoApplyArmed) {
      setError("Review the live counts above, then arm auto-apply before the bot runs.");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const u = match.apply_url.toLowerCase();
      const platform = u.includes("linkedin")
        ? "linkedin"
        : u.includes("indeed")
          ? "indeed"
          : u.includes("naukri")
            ? "naukri"
            : u.includes("foundit") || u.includes("monsterindia")
              ? "foundit"
              : u.includes("ziprecruiter")
                ? "ziprecruiter"
                : u.includes("glassdoor")
                  ? "glassdoor"
                  : "linkedin";
      await startBot(candidateId, {
        platform,
        continuous: false,
        batchSize: 1,
        jobUrl: match.apply_url,
        jobId: match.job_id,
        jobTitle: match.title,
        jobCompany: match.company,
      });
      setBotRunning(true);
      setMessage(`Bot applying to ${match.title} at ${match.company}…`);
      setTimeout(() => {
        if (candidateId) void refreshApplications(candidateId);
      }, 8000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Bot apply failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleAutoApplyAll() {
    if (!candidateId || matches.length === 0) return;
    if (!autoApplyArmed) {
      setError("Arm auto-apply first — confirm the exact live counts before bulk apply.");
      return;
    }
    const targets = matches.filter((m) => m.passed_threshold && !appliedIds.has(m.job_id));
    if (targets.length === 0) {
      setMessage("No strong unapplied matches left for auto-apply.");
      return;
    }
    setLoading(true);
    setError("");
    try {
      let done = 0;
      for (const match of targets.slice(0, 10)) {
        setMessage(`Auto-apply ${done + 1}/${Math.min(targets.length, 10)}: ${match.title}…`);
        await handleBotApplyTracked(match);
        done += 1;
        await new Promise((r) => setTimeout(r, 4000));
      }
      setMessage(`Auto-apply batch finished: ${done} jobs processed. Check dashboard for status.`);
      await refreshApplications(candidateId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Bulk auto-apply failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleBotApplyTracked(match: MatchResult) {
    if (!candidateId || !match.apply_url) return;
    const u = match.apply_url.toLowerCase();
    const platform = u.includes("linkedin")
      ? "linkedin"
      : u.includes("indeed")
        ? "indeed"
        : u.includes("naukri")
          ? "naukri"
          : u.includes("foundit") || u.includes("monsterindia")
            ? "foundit"
            : "linkedin";
    await startBot(candidateId, {
      platform,
      continuous: false,
      batchSize: 1,
      jobUrl: match.apply_url,
      jobId: match.job_id,
      jobTitle: match.title,
      jobCompany: match.company,
    });
    setBotRunning(true);
    // Wait for this single-job run to finish (poll up to ~90s) before next.
    for (let i = 0; i < 30; i += 1) {
      await new Promise((r) => setTimeout(r, 3000));
      try {
        const s = await getBotStatus(candidateId);
        setBotRunning(s.state === "running" || s.state === "stopping");
        if (s.state === "idle" || s.state === "error") break;
      } catch {
        break;
      }
    }
  }

  return (
    <>
      {/* Hero */}
      <section className="relative overflow-hidden bg-white pb-20 pt-16 lg:pb-28 lg:pt-24">
        <div className="mx-auto max-w-7xl px-6 lg:px-10">
          <div className="mx-auto max-w-3xl text-center">
            <p className="section-tag">Your search, in perfect rhythm</p>
            <h1 className="mt-5 display-heading">
              Apply smarter,{" "}
              <span className="font-serif italic font-normal">not harder.</span>
            </h1>
            <p className="mx-auto mt-6 max-w-xl text-lg leading-relaxed text-ink-500">
              ResumeBuild parses your resume, discovers jobs across every major board, scores each
              role against your profile, and tracks every application — all in one flow.
            </p>
            <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
              <Link href="#workspace" className="btn-dark">
                Try it for free
                <span aria-hidden>→</span>
              </Link>
              {candidateId && (
                <Link href="/dashboard" className="btn-outline">
                  View dashboard
                </Link>
              )}
            </div>
          </div>

          <div className="mt-16 lg:mt-20">
            <PhoneMockup />
          </div>
        </div>
      </section>

      {/* Split headline */}
      <section id="how-it-works" className="scroll-mt-24 border-t border-ink-100 bg-white py-24">
        <div className="mx-auto grid max-w-7xl gap-12 px-6 lg:grid-cols-2 lg:items-end lg:px-10">
          <h2 className="display-heading lg:text-[3.25rem]">
            Designed to help you land more{" "}
            <span className="font-serif italic font-normal">with less effort.</span>
          </h2>
          <p className="text-lg leading-relaxed text-ink-500 lg:pb-2">
            Stop copy-pasting the same resume into dozens of portals. Upload once, let the pipeline
            find relevant roles, score them against your background, and personalize before you
            apply.
          </p>
        </div>
      </section>

      {/* Feature grid */}
      <section id="features" className="scroll-mt-24 bg-ink-50 py-24">
        <div className="mx-auto max-w-7xl px-6 lg:px-10">
          <div className="grid gap-8 md:grid-cols-3">
            {FEATURES.map((feature, i) => (
              <div key={feature.title} className="card-light">
                <div className="feature-icon">{String(i + 1).padStart(2, "0")}</div>
                <h3 className="mt-6 text-xl font-semibold text-ink">{feature.title}</h3>
                <p className="mt-3 text-sm leading-relaxed text-ink-500">{feature.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Work smarter every day */}
      <section className="bg-white py-24">
        <div className="mx-auto max-w-7xl px-6 lg:px-10">
          <div className="grid gap-12 lg:grid-cols-[1fr_1.1fr] lg:items-center">
            <div className="relative aspect-[4/5] overflow-hidden rounded-[2rem] bg-ink-100">
              <div
                className="absolute inset-0 bg-cover bg-center grayscale"
                style={{
                  backgroundImage:
                    "url(https://images.unsplash.com/photo-1573496359142-b8d87734a5a2?w=900&q=80&auto=format&fit=crop)",
                }}
              />
            </div>
            <div>
              <p className="section-tag">Every day</p>
              <h2 className="mt-4 text-4xl font-bold tracking-tight text-ink md:text-5xl">
                Work smarter every day
              </h2>
              <div className="mt-10 grid gap-6 sm:grid-cols-2">
                {DAILY_FEATURES.map((item) => (
                  <div key={item} className="flex gap-3">
                    <span className="mt-1 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-ink text-[10px] font-bold text-white">
                      ✓
                    </span>
                    <p className="text-sm leading-relaxed text-ink-600">{item}</p>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Full-width CTA band */}
      <section className="relative min-h-[420px] overflow-hidden bg-ink-900">
        <div
          className="absolute inset-0 bg-cover bg-center opacity-40 grayscale"
          style={{
            backgroundImage:
              "url(https://images.unsplash.com/photo-1487412720507-e7ab37603c6f?w=1400&q=80&auto=format&fit=crop)",
          }}
        />
        <div className="absolute inset-0 bg-gradient-to-r from-ink via-ink/80 to-transparent" />
        <div className="relative mx-auto flex min-h-[420px] max-w-7xl items-center px-6 py-20 lg:px-10">
          <div className="max-w-lg">
            <h2 className="text-4xl font-bold leading-tight text-white md:text-5xl">
              Ready to reclaim your job search?
            </h2>
            <p className="mt-4 text-lg text-white/60">
              Upload your resume and get scored matches in under a minute.
            </p>
            <Link href="#workspace" className="btn-dark mt-8 bg-white text-ink hover:bg-ink-100">
              Get started free
              <span aria-hidden>→</span>
            </Link>
          </div>
        </div>
      </section>

      {/* Trusted by */}
      <section className="border-b border-ink-100 bg-white py-16">
        <div className="mx-auto max-w-7xl px-6 text-center lg:px-10">
          <p className="section-tag">Trusted by teams at</p>
          <div className="mt-8 flex flex-wrap items-center justify-center gap-x-12 gap-y-6">
            {TRUSTED.map((name) => (
              <span
                key={name}
                className="text-lg font-semibold tracking-wide text-ink-300 md:text-xl"
              >
                {name}
              </span>
            ))}
          </div>
        </div>
      </section>

      {/* Community */}
      <section className="bg-white py-24">
        <div className="mx-auto max-w-7xl px-6 text-center lg:px-10">
          <p className="section-tag">Community</p>
          <h2 className="mx-auto mt-4 max-w-2xl text-4xl font-bold tracking-tight text-ink md:text-5xl">
            Join a community of modern professionals
          </h2>
          <div className="mt-12 flex flex-wrap items-center justify-center gap-4">
            {COMMUNITY.map((initials) => (
              <div
                key={initials}
                className="flex h-16 w-16 items-center justify-center rounded-2xl border border-ink-200 bg-ink-50 text-sm font-bold text-ink"
              >
                {initials}
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Built for focus */}
      <section className="bg-ink-50 py-24">
        <div className="mx-auto max-w-7xl px-6 lg:px-10">
          <div className="grid gap-12 lg:grid-cols-2 lg:items-center">
            <div>
              <p className="section-tag">Built for focus</p>
              <h2 className="mt-4 text-4xl font-bold tracking-tight text-ink md:text-5xl">
                Built for focus, made for real careers
              </h2>
              <ul className="mt-10 space-y-5">
                {FOCUS_BENEFITS.map((item) => (
                  <li key={item} className="flex gap-4 border-b border-ink-200 pb-5 last:border-0">
                    <span className="feature-icon shrink-0">→</span>
                    <p className="text-sm leading-relaxed text-ink-600">{item}</p>
                  </li>
                ))}
              </ul>
            </div>
            <div className="relative aspect-[4/5] overflow-hidden rounded-[2rem] bg-ink-200">
              <div
                className="absolute inset-0 bg-cover bg-center grayscale"
                style={{
                  backgroundImage:
                    "url(https://images.unsplash.com/photo-1522202176988-66273c2fd55f?w=900&q=80&auto=format&fit=crop)",
                }}
              />
            </div>
          </div>
        </div>
      </section>

      {/* Job sources */}
      {jobSources.length > 0 && (
        <section id="sources" className="scroll-mt-24 border-t border-ink-100 bg-white py-20">
          <div className="mx-auto max-w-7xl px-6 lg:px-10">
            <div className="mx-auto max-w-2xl text-center">
              <p className="section-tag">Integrations</p>
              <h2 className="mt-4 text-3xl font-bold text-ink">Job sources we pull from</h2>
            </div>
            <div className="mt-10 flex flex-wrap justify-center gap-3">
              {jobSources.map((s) => (
                <span key={s.key} className="source-pill">
                  {s.name}
                  {s.status === "needs_credentials" && " · needs key"}
                </span>
              ))}
            </div>
          </div>
        </section>
      )}

      {/* Interactive workflow */}
      <WorkflowPanel
        loading={loading}
        loadingPhase={loadingPhase}
        candidateId={candidateId}
        profile={profile}
        discovery={discovery}
        matches={matches}
        appliedIds={appliedIds}
        pipelineStep={pipelineStep}
        message={message}
        error={error}
        selectedPlatforms={selectedPlatforms}
        remoteType={remoteType}
        sessions={sessions}
        loginPlatform={loginPlatform}
        onUpload={(file) => void handleUpload(file)}
        onLogin={(p) => void handleLogin(p)}
        onLoginDone={() => void handleLoginDone()}
        onDiscoverAndMatch={() => void handleDiscoverAndMatch()}
        onPersonalize={(jobId) => void handlePersonalize(jobId)}
        onApply={(match) => void handleApply(match)}
        onBotApply={(match) => void handleBotApply(match)}
        onAutoApplyAll={() => void handleAutoApplyAll()}
        autoApplyArmed={autoApplyArmed}
        onAutoApplyArmedChange={setAutoApplyArmed}
        onPlatformToggle={handlePlatformToggle}
        onRemoteTypeChange={setRemoteType}
        botRunning={botRunning}
        onError={setError}
      />

      <AutoApplyPanel
        candidateId={candidateId}
        onApplicationsChanged={() => {
          if (!candidateId) return;
          void refreshApplications(candidateId);
          void getBotStatus(candidateId).then((s) =>
            setBotRunning(s.state === "running" || s.state === "stopping"),
          );
        }}
      />

      <LandingFooter />
    </>
  );
}
