import 'server-only';
type EvaluationMode = 'synthetic_fixture' | 'build_endpoints';

export type Project = {
  id: string;
  name: string;
  organization: string;
  role: 'viewer' | 'maintainer' | 'approver';
};

export type Account = { handle: string | null; projects: Project[] };

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
  suite_revision?: string;
  policy_revision?: string;
  verdict?: string;
  reason?: string | null;
  builds?: Record<'baseline' | 'candidate', Build>;
};
export type RunDetail = {
  id: string;
  project_id: string;
  commit_sha: string;
  state: string;
  created_at: string | null;
  completed_at: string | null;
  github_check_id: number | null;
  github_check_retry_at: string | null;
  evaluation_mode: EvaluationMode;
  can_investigate: boolean;
  can_record_decision: boolean;
  approval: { decision: 'approve' | 'reject'; image_digest: string; actor: string; created_at: string } | null;
  deployment: {
    namespace: string;
    rollout: string;
    outcome: 'promoted' | 'aborted';
    image_digest: string;
    stable_image_digest: string;
    recorded_at: string;
  } | null;
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

export function historyPath(project: Project) {
  return `/v1/projects/${encodeURIComponent(project.id)}/runs`;
}

export function readApi<T>(path: string, token: string, body?: object): Promise<T> {
  return releaseApi<T>(path, token, body ? 'POST' : 'GET', body);
}

export async function releaseApi<T>(
  path: string, token: string, method: 'GET' | 'POST' | 'DELETE', body?: object,
): Promise<T> {
  const host = process.env.RELEASETRACE_API_HOSTPORT;
  const base = process.env.RELEASETRACE_API_URL ?? (host ? `http://${host}` : 'http://127.0.0.1:8000');
  const response = await fetch(new URL(path, base), {
    method,
    body: body ? JSON.stringify(body) : undefined,
    headers: { Authorization: `Bearer ${token}`, ...(body && { 'Content-Type': 'application/json' }) },
    cache: 'no-store',
    redirect: 'error',
    signal: AbortSignal.timeout(5_000),
  });
  if (!response.ok) throw new ReleaseApiError(response.status);
  return response.status === 204 ? undefined as T : response.json() as Promise<T>;
}
