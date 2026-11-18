import Link from 'next/link';
import { signIn } from './actions';
import { RunTime, Status } from './components';
import { historyPath, type Page, readApi, ReleaseApiError, type Run, viewerToken } from '../lib/releases';

function AccessForm({ error }: { error?: string }) {
  return (
    <section className="panel access-panel">
      <h1>Open release history</h1>
      <p>Enter your project viewer, maintainer, or approver token.</p>
      {error === 'invalid' && <p role="alert">The access token was rejected.</p>}
      {error === 'unavailable' && <p role="alert">The ReleaseTrace API is unavailable. Try again shortly.</p>}
      <form action={signIn} className="access-form">
        <label htmlFor="token">Project access token</label>
        <input id="token" name="token" type="password" required maxLength={512} autoComplete="off" />
        <button className="primary">Open dashboard</button>
      </form>
    </section>
  );
}

export default async function History({ searchParams }: { searchParams: Promise<{ after?: string; access?: string }> }) {
  const { after, access } = await searchParams;
  const token = await viewerToken();
  if (!token) return <AccessForm error={access} />;

  let page: Page<Run>;
  try {
    const query = typeof after === 'string' ? `?after=${encodeURIComponent(after)}` : '';
    page = await readApi<Page<Run>>(`${historyPath()}${query}`, token);
  } catch (error) {
    if (error instanceof ReleaseApiError && error.status === 401) return <AccessForm error="invalid" />;
    const invalidPage = error instanceof ReleaseApiError && [400, 422].includes(error.status);
    return <><h1>Release history</h1><p role="alert">{invalidPage ? 'This page link is invalid.' : 'Release history is unavailable.'}</p><Link href="/">Open first page</Link></>;
  }

  return (
    <>
      <h1>Release history</h1>
      {page.items.length === 0 ? <p className="panel">No evaluations on this page.</p> : (
        <div className="panel table-scroll" tabIndex={0} role="region" aria-label="Release evaluations">
          <table>
            <thead><tr><th scope="col">Commit</th><th scope="col">Queued at</th><th scope="col">Evaluation</th><th scope="col">Gate</th></tr></thead>
            <tbody>{page.items.map((run) => (
              <tr key={run.id}>
                <td><Link href={`/runs/${encodeURIComponent(run.id)}`} aria-label={`Review commit ${run.commit_sha}, delivery ${run.id}`}><code>{run.commit_sha.slice(0, 12)}</code></Link></td>
                <td><RunTime value={run.created_at} /></td>
                <td>{run.evaluation_mode === 'build_endpoints' ? 'Build endpoints' : 'Synthetic fixtures'}</td>
                <td><Status value={run.verdict} /></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      )}
      <nav aria-label="Run pages" className="page-links">
        {after && <Link href="/">First page</Link>}
        {page.next_cursor && <Link href={{ pathname: '/', query: { after: page.next_cursor } }}>Next page</Link>}
      </nav>
    </>
  );
}
