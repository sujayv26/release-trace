import Link from 'next/link';
import { signIn } from './actions';
import { Status } from './components';
import { RunHistory } from './run-history';
import { type Account, historyPath, type Page, readApi, ReleaseApiError, type Run, viewerToken } from '../lib/releases';

function AccessForm({ error }: { error?: string }) {
  return (
    <div className="access-layout">
    <section className="hero sign-in-hero">
      <p className="eyebrow">Release review</p>
      <h1>Know what changed before you ship.</h1>
      <p>ReleaseTrace asks the current and proposed versions of an AI app the same questions. Review the differences and see what could block a release.</p>
      <div className="intro-steps"><span>Compare versions</span><span>Review evidence</span><span>Decide what ships</span></div>
    </section>
    <section className="panel access-panel">
      <h2>Open release history</h2>
      <p>Enter your individual access key or a local demo token.</p>
      {error === 'invalid' && <p role="alert">The access key was rejected.</p>}
      {error === 'unavailable' && <p role="alert">The ReleaseTrace API is unavailable. Try again shortly.</p>}
      <form action={signIn} className="access-form">
        <label htmlFor="token">Access key</label>
        <input id="token" name="token" type="password" required maxLength={512} autoComplete="off" />
        <button className="primary">Open dashboard</button>
      </form>
    </section>
    </div>
  );
}

export default async function History({ searchParams }: {
  searchParams: Promise<{ after?: string; access?: string; project?: string | string[] }>;
}) {
  const { after, access, project } = await searchParams;
  const token = await viewerToken();
  if (!token) return <AccessForm error={access} />;

  let page: Page<Run>;
  let account: Account;
  let selected;
  try {
    account = await readApi<Account>('/v1/me', token);
    if (account.projects.length === 0) {
      return <><h1>Release history</h1><p className="panel">Your account has no project access.</p></>;
    }
    selected = project === undefined ? account.projects[0] : account.projects.find(item => item.id === project);
    if (!selected) {
      return <><h1>Release history</h1><p role="alert">This project is unavailable.</p><Link href="/">Open an available project</Link></>;
    }
    const query = typeof after === 'string' ? `?after=${encodeURIComponent(after)}` : '';
    page = await readApi<Page<Run>>(`${historyPath(selected)}${query}`, token);
  } catch (error) {
    if (error instanceof ReleaseApiError && error.status === 401) return <AccessForm error="invalid" />;
    const invalidPage = error instanceof ReleaseApiError && [400, 422].includes(error.status);
    return <><h1>Release history</h1><p role="alert">{invalidPage ? 'This page link is invalid.' : 'Release history is unavailable.'}</p><Link href="/">Open first page</Link></>;
  }
  const projectQuery = { project: selected.id };

  return (
    <>
      <section className="hero overview-hero">
        <div>
          <p className="eyebrow">Release history</p>
          <h1>{selected.name}</h1>
          <p>See whether the proposed version is ready and review the evidence behind each decision.</p>
          {account.handle && <p className="identity">{account.handle} · {selected.role}</p>}
        </div>
        {!after && page.items[0] && (
          <Link className="hero-run" href={`/runs/${encodeURIComponent(page.items[0].id)}`}>
            <span>Latest evaluation</span>
            <Status value={page.items[0].verdict} />
            <strong>Review latest run <span aria-hidden="true">→</span></strong>
          </Link>
        )}
      </section>
      {account.projects.length > 1 && (
        <nav className="filters project-links" aria-label="Projects">
          {account.projects.map(item => (
            <Link key={item.id} href={{ pathname: '/', query: { project: item.id } }}
              aria-current={item.id === selected.id ? 'page' : undefined}>
              {item.organization} / {item.name}
            </Link>
          ))}
        </nav>
      )}
      <div className="workspace-grid">
        <RunHistory runs={page.items} />
        <details className="panel review-guide" open>
          <summary>How to review a release</summary>
          <ol>
            <li><strong>Read the decision</strong><p>Blocked means a release rule failed. Passed means the proposed version met the evaluation rules.</p></li>
            <li><strong>Compare the answers</strong><p>Stable is the reference version. Candidate is the version being proposed. A citation is the source used to answer a test question.</p></li>
            <li><strong>Take the next step</strong><p>Review failed cases on a blocked run. On a passing run, users with approval access can record a decision.</p></li>
          </ol>
        </details>
      </div>
      <nav aria-label="Run pages" className="page-links">
        {after && <Link href={{ pathname: '/', query: projectQuery }}>First page</Link>}
        {page.next_cursor && <Link href={{ pathname: '/', query: { ...projectQuery, after: page.next_cursor } }}>Next page</Link>}
      </nav>
    </>
  );
}
