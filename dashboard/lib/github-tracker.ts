/** Read measurements from GitHub for the selected repository and default branch. */
export function repositoryName(input: string) {
  const value = input.trim().replace(/^https:\/\/github\.com\//i, '').replace(/\/$/, '').replace(/\.git$/, '');
  return /^[a-z\d][a-z\d-]{0,38}\/[a-z\d_.-]{1,100}$/i.test(value) && !['.', '..'].includes(value.split('/')[1])
    ? value : null;
}

export class GitHubError extends Error {
  constructor(readonly status: number, readonly rateLimited = false) { super('GitHub request unsuccessful'); }
}

export function githubNotice(error: unknown) {
  if (error instanceof GitHubError) {
    if (error.rateLimited) return 'rate';
    if (error.status === 401) return 'expired';
    if (error.status === 404 || error.status === 403) return 'repository';
  }
  return 'unavailable';
}

export async function github<T>(path: string, token?: string): Promise<T> {
  // Callers build paths from validated names, immutable IDs, or encoded refs.
  const res = await fetch('https://api.github.com' + path, {
    headers: { Accept: 'application/vnd.github+json', 'User-Agent': 'ReleaseTrace',
      ...(token && { Authorization: 'Bearer ' + token }) },
    ...(token ? { cache: 'no-store' as const } : { next: { revalidate: 60 } }),
    redirect: 'error', signal: AbortSignal.timeout(8000),
  });
  if (!res.ok) {
    const rateLimited = res.status === 429 || (res.status === 403 &&
      (res.headers.get('x-ratelimit-remaining') === '0' || res.headers.has('retry-after')));
    throw new GitHubError(res.status,rateLimited);
  }
  return res.json();
}

export type Repo = { id: number; full_name: string; default_branch: string; private: boolean };
type Workflow = { id: number; name: string; head_sha: string; status: string; conclusion: string | null;
  run_started_at: string | null; updated_at: string };
type Release = { tag_name: string; draft: boolean; prerelease: boolean; published_at: string | null };
type Check = { id: number; name: string; head_sha: string; status: string; conclusion: string | null;
  app: { id: number; slug: string } | null };

export async function measure(repo: Repo, token?: string) {
  const path = '/repositories/' + repo.id;
  const branch = encodeURIComponent(repo.default_branch);
  const [commit, releases, workflows] = await Promise.all([
    github<{ sha: string }>(path + '/commits/' + branch, token).catch(e => {
      if (e instanceof GitHubError && [404, 409].includes(e.status)) return null;
      throw e;
    }),
    github<Release[]>(path + '/releases?per_page=20', token),
    github<{workflow_runs: Workflow[]}>(path + '/actions/runs?branch=' + branch + '&per_page=25', token)
      .catch(e => { if (e instanceof GitHubError && [403, 404].includes(e.status)) return null; throw e; }),
  ]);
  const runs = workflows?.workflow_runs;
  const completed = runs?.filter(r => r.status === 'completed') ?? [];
  const elapsed = completed.flatMap(r => r.run_started_at
    ? [(Date.parse(r.updated_at) - Date.parse(r.run_started_at)) / 1000] : []).filter(s => s >= 0).sort((a,b) => a-b);
  const checks = commit ? await github<{check_runs: Check[]}>(
    path + '/commits/' + commit.sha + '/check-runs?check_name=' +
    encodeURIComponent('ReleaseTrace / Build evaluation') + '&per_page=100', token)
    .catch(e => { if (e instanceof GitHubError && [403, 404].includes(e.status)) return null; throw e; }) : null;
  return { commit, releases: releases.filter(r => !r.draft && r.published_at), runs,
    completed: completed.length, successes: completed.filter(r => r.conclusion === 'success').length,
    p95: elapsed.length ? elapsed[Math.ceil(elapsed.length * .95)-1] : null, timed: elapsed.length,
    checks: checks?.check_runs.filter(c => c.head_sha === commit?.sha && c.name === 'ReleaseTrace / Build evaluation'),
  };
}
