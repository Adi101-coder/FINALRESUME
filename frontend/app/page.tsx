"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { UploadStep } from "@/components/steps/UploadStep";
import { PlatformStep } from "@/components/steps/PlatformStep";
import { MatchStep } from "@/components/steps/MatchStep";
import { AutoApplyStep } from "@/components/steps/AutoApplyStep";
import { LandingFooter } from "@/components/landing/LandingFooter";
import { PhoneMockup } from "@/components/landing/PhoneMockup";
import {
  checkBackendHealth,
  getJobSources,
  type CandidateProfile,
  type JobSource,
  type MatchResult,
} from "@/lib/api";

const STEPS = [
  { id: "upload", label: "Upload Resume" },
  { id: "platforms", label: "Select Platforms" },
  { id: "matches", label: "View Matches" },
  { id: "apply", label: "Auto-Apply" },
];

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
  "Live job discovery from LinkedIn",
  "Profile-based queries that adapt to any industry",
  "ATS-aware resume personalization per role",
  "Persistent application history and analytics",
];

const FOCUS_BENEFITS = [
  "One resume upload powers the entire pipeline",
  "Live LinkedIn discovery for your exact role — no static job pool",
  "Threshold-based matching so you focus on strong fits",
  "Export-ready PDFs tailored to each posting",
];

const TRUSTED = ["LinkedIn"];
const COMMUNITY = ["AK", "JM", "SR", "PL", "DC", "EV", "TH", "NW"];

export default function HomePage() {
  const [step, setStep] = useState<"upload" | "platforms" | "matches" | "apply">("upload");
  const [candidateId, setCandidateId] = useState<number | null>(null);
  const [profile, setProfile] = useState<CandidateProfile | null>(null);
  const [jobSources, setJobSources] = useState<JobSource[]>([]);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [selectedPlatforms, setSelectedPlatforms] = useState<string[]>(["linkedin"]);
  const [remoteType, setRemoteType] = useState<string>("");
  const [searchLocation, setSearchLocation] = useState<string>("");
  const [allLocations, setAllLocations] = useState<boolean>(false);
  const [reviewQueue, setReviewQueue] = useState<MatchResult[]>([]);

  const [storedId, setStoredId] = useState<number | null>(null);

  const handleAddToQueue = (match: MatchResult) => {
    setReviewQueue((prev) => {
      if (prev.some((m) => m.job_id === match.job_id)) return prev;
      return [...prev, match];
    });
  };

  const handleRemoveFromQueue = (jobId: number) => {
    setReviewQueue((prev) => prev.filter((m) => m.job_id !== jobId));
  };

  const handleClearQueue = () => {
    setReviewQueue([]);
  };

  useEffect(() => {
    if (typeof window === "undefined") return;
    void checkBackendHealth()
      .then((ok) => {
        if (!ok) setError(`Backend health check failed`);
      })
      .catch(() => setError("Backend unreachable"));
    void getJobSources().then((r) => setJobSources(r.sources)).catch(() => {});
    // Never auto-skip the upload step — just remember the previous profile
    // so the user can continue or start fresh.
    const stored = window.localStorage.getItem("candidateId");
    if (stored) {
      const id = Number(stored);
      if (!Number.isNaN(id) && id > 0) {
        setStoredId(id);
      }
    }
  }, []);

  const handleUploadComplete = (id: number, candidateProfile: CandidateProfile) => {
    setCandidateId(id);
    setProfile(candidateProfile);
    window.localStorage.setItem("candidateId", String(id));
    setStoredId(id);
    setMessage(`Resume parsed for ${candidateProfile.name || "candidate"}. Now select platforms.`);
    setStep("platforms");
    document.getElementById("workspace")?.scrollIntoView({ behavior: "smooth" });
  };

  const handleContinueStored = () => {
    if (storedId) {
      setCandidateId(storedId);
      setMessage(`Continuing with saved profile #${storedId}.`);
      setStep("platforms");
      document.getElementById("workspace")?.scrollIntoView({ behavior: "smooth" });
    }
  };

  const handleStartFresh = () => {
    window.localStorage.removeItem("candidateId");
    setStoredId(null);
    setCandidateId(null);
    setProfile(null);
    setMessage("");
    setError("");
    setStep("upload");
    document.getElementById("workspace")?.scrollIntoView({ behavior: "smooth" });
  };

  return (
    <>
      {/* Hero */}
      <section className="relative overflow-hidden bg-white pb-20 pt-16 lg:pb-28 lg:pt-24">
        <div className="mx-auto max-w-7xl px-6 lg:px-10">
          <div className="mx-auto max-w-3xl text-center">
            <p className="section-tag">Your search, in perfect rhythm</p>
            <h1 className="mt-5 display-heading">
              Apply smarter, <span className="font-serif italic font-normal">not harder.</span>
            </h1>
            <p className="mx-auto mt-6 max-w-xl text-lg leading-relaxed text-ink-500">
              ResumeBuild parses your resume, discovers matching jobs on LinkedIn, scores each
              role against your profile, and tracks every application — all in one flow.
            </p>
            <div className="mt-8 flex flex-wrap items-center justify-center gap-3">
              <Link href="#workspace" className="btn-dark">
                Try it for free <span aria-hidden>→</span>
              </Link>
              {candidateId ? (
                <Link href="/dashboard" className="btn-outline">
                  View dashboard
                </Link>
              ) : null}
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
              Get started free <span aria-hidden>→</span>
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
              <span key={name} className="text-lg font-semibold tracking-wide text-ink-300 md:text-xl">
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

      {/* Workspace — job search flow */}
      <section id="workspace" className="scroll-mt-24 bg-ink-50 py-24">
        <div className="mx-auto max-w-5xl px-6 lg:px-10">
          <div className="mx-auto max-w-2xl text-center">
            <p className="section-tag">Start now</p>
            <h2 className="mt-4 display-heading">
              Upload once. <span className="font-serif italic font-normal">Match everywhere.</span>
            </h2>
            <p className="mt-4 text-lg text-ink-500">
              Drop your PDF, pick platforms, log in only where needed, and review scored matches.
            </p>
          </div>

          <div className="mt-10 grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {STEPS.map((s, i) => {
              const order = ["upload", "platforms", "matches", "apply"];
              const active = order.indexOf(step) === i;
              const done = order.indexOf(step) > i;
              const locked = s.id !== "upload" && !candidateId;
              return (
                <button
                  key={s.id}
                  type="button"
                  disabled={locked}
                  title={locked ? "Upload a resume first" : `Go to ${s.label}`}
                  onClick={() => {
                    if (s.id !== "upload" && !candidateId) {
                      setError("Upload a resume first, then switch steps.");
                      return;
                    }
                    setError("");
                    setStep(s.id as typeof step);
                    document.getElementById("workspace")?.scrollIntoView({ behavior: "smooth" });
                  }}
                  className={`progress-step text-left transition-colors ${
                    done
                      ? "progress-step-done cursor-pointer hover:opacity-80"
                      : active
                        ? "progress-step-active cursor-default"
                        : locked
                          ? "opacity-50 cursor-not-allowed"
                          : "cursor-pointer hover:opacity-80"
                  }`}
                >
                  <span className="font-mono text-xs opacity-60">{String(i + 1).padStart(2, "0")}</span>
                  <span>{s.label}</span>
                </button>
              );
            })}
          </div>

          {message && <div className="alert-success mt-6">{message}</div>}
          {error && <div className="alert-error mt-6">{error}</div>}

          {!candidateId && storedId && step === "upload" && (
            <div className="mt-6 rounded-2xl border border-ink-200 bg-white p-4 flex flex-wrap items-center justify-between gap-3">
              <p className="text-sm text-ink-600">
                Found a previous upload (profile #{storedId}). Continue where you left off, or start fresh.
              </p>
              <div className="flex gap-2">
                <button onClick={handleContinueStored} className="btn-dark text-sm">
                  Continue #{storedId} →
                </button>
                <button onClick={handleStartFresh} className="btn-outline text-sm">
                  Upload new resume
                </button>
              </div>
            </div>
          )}

          {candidateId && (
            <div className="mt-6 flex justify-end">
              <button onClick={handleStartFresh} className="btn-outline text-xs">
                ↺ Upload new resume (start over)
              </button>
            </div>
          )}

          <div className="mt-6 card-light p-6 md:p-8">
            {step === "upload" && (
              <UploadStep
                onComplete={handleUploadComplete}
                loading={loading}
                error={error}
                onError={setError}
              />
            )}

            {step === "platforms" && candidateId && (
              <PlatformStep
                candidateId={candidateId}
                profile={profile}
                jobSources={jobSources}
                selectedPlatforms={selectedPlatforms}
                onSelectedChange={setSelectedPlatforms}
                remoteType={remoteType}
                onRemoteTypeChange={setRemoteType}
                onComplete={() => setStep("matches")}
                onBack={() => setStep("upload")}
                loading={loading}
                error={error}
                onError={setError}
                onMessage={setMessage}
              />
            )}

            {step === "matches" && candidateId && (
              <MatchStep
                candidateId={candidateId}
                profile={profile}
                onComplete={() => setStep("apply")}
                onBack={() => setStep("platforms")}
                loading={loading}
                error={error}
                onError={setError}
                onMessage={setMessage}
                selectedPlatforms={selectedPlatforms}
                onSelectedChange={setSelectedPlatforms}
                remoteType={remoteType}
                onRemoteTypeChange={setRemoteType}
                searchLocation={searchLocation}
                onSearchLocationChange={setSearchLocation}
                allLocations={allLocations}
                onAllLocationsChange={setAllLocations}
                onAddToQueue={handleAddToQueue}
                onRemoveFromQueue={handleRemoveFromQueue}
                queuedJobIds={reviewQueue.map((m) => m.job_id)}
              />
            )}

            {step === "apply" && candidateId && (
              <AutoApplyStep
                candidateId={candidateId}
                loading={loading}
                error={error}
                onBack={() => setStep("matches")}
                reviewQueue={reviewQueue}
                onRemoveFromQueue={handleRemoveFromQueue}
                onClearQueue={handleClearQueue}
              />
            )}
          </div>
        </div>
      </section>

      <LandingFooter />
    </>
  );
}
