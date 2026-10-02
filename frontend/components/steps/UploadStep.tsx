"use client";

import { useState } from "react";
import { uploadResume, type CandidateProfile } from "@/lib/api";

type Props = {
  onComplete: (id: number, profile: CandidateProfile) => void;
  loading: boolean;
  error: string;
  onError: (err: string) => void;
};

export function UploadStep({ onComplete, loading, error, onError }: Props) {
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);

  const handleUpload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!file) return;
    setUploading(true);
    onError("");
    try {
      const result = await uploadResume(file);
      onComplete(result.candidate_id, result.profile);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  return (
    <div className="max-w-2xl mx-auto text-center">
      <h1 className="text-3xl font-bold text-ink mb-2">Upload Your Resume</h1>
      <p className="text-ink-500 mb-8">PDF only. We&rsquo;ll parse it and extract your skills, experience, and preferences.</p>

      {error && <div className="alert-error mb-6">{error}</div>}

      <form onSubmit={handleUpload}>
        <label className="upload-zone block cursor-pointer mb-6">
          <div className="flex h-20 w-20 items-center justify-center rounded-2xl border border-ink-200 bg-white mx-auto mb-3 text-xs font-bold uppercase tracking-widest text-ink">
            PDF
          </div>
          <span className="font-medium text-ink block mb-1">
            {file ? file.name : "Drop PDF or click to browse"}
          </span>
          <span className="text-xs text-ink-400">Recommended · up to 2 pages</span>
          <input
            type="file"
            accept="application/pdf"
            className="hidden"
            disabled={uploading || loading}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) setFile(f);
            }}
          />
        </label>

        <button
          type="submit"
          className="btn-dark w-full"
          disabled={!file || uploading || loading}
        >
          {uploading ? "Parsing…" : "Upload & Parse"}
        </button>
      </form>

      <p className="mt-6 text-xs text-ink-400">
        Your resume is parsed locally. We extract skills, experience, education, and preferences.
      </p>
    </div>
  );
}