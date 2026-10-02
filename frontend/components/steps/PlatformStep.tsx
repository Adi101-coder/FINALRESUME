"use client";

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
  remoteType,
  onRemoteTypeChange,
  onComplete,
  onBack,
  loading,
  error,
}: Props) {
  return (
    <div className="max-w-3xl mx-auto">
      {error && <div className="alert-error mb-6">{error}</div>}

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

      <div className="flex gap-3">
        <button className="btn-outline flex-1" onClick={onBack}>
          ← Back
        </button>
        <button className="btn-dark flex-[2]" disabled={loading} onClick={onComplete}>
          Continue to Matches →
        </button>
      </div>
    </div>
  );
}
