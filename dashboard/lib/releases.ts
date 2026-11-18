import 'server-only';
import { cookies } from 'next/headers';

export const accessCookie = 'releasetrace-viewer';
type EvaluationMode = 'synthetic_fixture' | 'build_endpoints';

export type Run = {
  id: string;
  commit_sha: string;
  state: string;
  baseline_verdict: string | null;
  candidate_verdict: string | null;
  verdict: string | null;
  evaluation_mode: EvaluationMode;
  created_at: string | null;
};

type Evidence = { id: string; revision: number; text: string };
export type Variant = { outcome: string; citation: string | null; evidence: Evidence | null; reason?: string | null };
export type Case = {
  case_id: string;
  question: string;
  required_citation: string;
  critical: boolean;
  baseline: Variant;
  candidate: Variant;
  investigation_id: string | null;
};

type Gate = { verdict: string; score: number; critical_failures: string[] };
type Build = { commit_sha: string; image_digest: string };
type Report = {
  baseline: Gate;
  candidate: Gate;
  dataset_digest: string;
  policy_digest: string;
  verdict?: string;
  reason?: string | null;
  builds?: Record<'baseline' | 'candidate', Build>;
};
export type RunDetail = {
  id: string;
  commit_sha: string;
  state: string;
  created_at: string | null;
  completed_at: string | null;
  github_check_id: number | null;
  github_check_retry_at: string | null;
  evaluation_mode: EvaluationMode;
  can_investigate: boolean;
  report: Report | null;
};

export type Investigation = {
  investigation_id: string;
  case_id: string;
  state: string;
  report: {
    outcome: 'supported' | 'inconclusive';
    reason: string;
    required_citation: string;
    baseline: Variant;
    candidate: Variant;
    experiment: Variant | null;
    changed_parameter: { name: string; before: string; after: string } | null;
    dataset_digest: string;
    policy_digest: string;
  } | null;
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

export async function readApi<T>(path: string, token: string, body?: object): Promise<T> {
  const base = process.env.RELEASETRACE_API_URL ?? 'http://127.0.0.1:8000';
  const response = await fetch(new URL(path, base), {
    method: body ? 'POST' : 'GET',
    body: body ? JSON.stringify(body) : undefined,
    headers: { Authorization: `Bearer ${token}`, ...(body && { 'Content-Type': 'application/json' }) },
    cache: 'no-store',
    redirect: 'error',
    signal: AbortSignal.timeout(5_000),
  });
  if (!response.ok) throw new ReleaseApiError(response.status);
  return response.json() as Promise<T>;
}
