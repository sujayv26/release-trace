import 'server-only';
import { cookies } from 'next/headers';

export const accessCookie = 'releasetrace-viewer';

export type Run = {
  id: string;
  commit_sha: string;
  state: string;
  baseline_verdict: string | null;
  candidate_verdict: string | null;
};

type Evidence = { id: string; revision: number; text: string };
export type Variant = { outcome: string; citation: string | null; evidence: Evidence | null };
export type Case = {
  case_id: string;
  question: string;
  required_citation: string;
  critical: boolean;
  baseline: Variant;
  candidate: Variant;
};

type Gate = { verdict: string; score: number; critical_failures: string[] };
export type RunDetail = {
  id: string;
  commit_sha: string;
  state: string;
  report: { baseline: Gate; candidate: Gate; dataset_digest: string; policy_digest: string } | null;
};

export type Page<T> = { items: T[]; next_cursor: string | null };

export class ReleaseApiError extends Error {
  constructor(readonly status: number) {
    super(`ReleaseTrace API returned ${status}`);
  }
}

export async function viewerToken() {
  return (await cookies()).get(accessCookie)?.value;
}

export function historyPath() {
  const repository = process.env.RELEASETRACE_REPOSITORY_ID;
  if (!repository || !/^\d+$/.test(repository)) throw new Error('Repository ID is missing');
  return `/v1/projects/${repository}/runs`;
}

export async function readApi<T>(path: string, token: string): Promise<T> {
  const base = process.env.RELEASETRACE_API_URL ?? 'http://127.0.0.1:8000';
  const response = await fetch(new URL(path, base), {
    headers: { Authorization: `Bearer ${token}` },
    cache: 'no-store',
    redirect: 'error',
    signal: AbortSignal.timeout(5_000),
  });
  if (!response.ok) throw new ReleaseApiError(response.status);
  return response.json() as Promise<T>;
}
