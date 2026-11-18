import Link from 'next/link';
import { notFound, redirect } from 'next/navigation';
import { Status } from '../../components';
import { type Case, type Page, readApi, ReleaseApiError, type RunDetail, type Variant, viewerToken } from '../../../lib/releases';

function Evidence({ label, variant }: { label: string; variant: Variant }) {
  return (
    <div className="evidence">
      <h3>{label} <Status value={variant.outcome} /></h3>
      {variant.evidence ? <><code>{variant.evidence.id}</code><blockquote>{variant.evidence.text}</blockquote></> : <p>No policy evidence recorded.</p>}
    </div>
  );
}

export default async function RunPage({ params, searchParams }: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ outcome?: string; after?: string }>;
}) {
  const { id } = await params;
  const { outcome, after } = await searchParams;
  if (!/^[A-Za-z0-9-]{1,128}$/.test(id)) notFound();
  const token = await viewerToken();
  if (!token) redirect('/');
  const path = `/runs/${id}`;
  const query = new URLSearchParams();
  const failuresOnly = outcome === 'fail';
  if (failuresOnly) query.set('outcome', 'fail');
  if (typeof after === 'string') query.set('after', after);

  let run: RunDetail;
  let cases: Page<Case> & { state: string };
  try {
    [run, cases] = await Promise.all([
      readApi<RunDetail>(`/v1${path}`, token),
      readApi<Page<Case> & { state: string }>(`/v1${path}/cases?${query}`, token),
    ]);
  } catch (error) {
    if (error instanceof ReleaseApiError && error.status === 404) notFound();
    if (error instanceof ReleaseApiError && error.status === 401) redirect('/');
    return <><Link href="/">← Release history</Link><h1>Release evaluation</h1><p role="alert">Could not load this run.</p><Link href={path}>Open run again</Link></>;
  }

  // The worker can finish between the two reads. Render a consistent pending
  // view until both results are available on the same request.
  const report = cases.state === 'completed' ? run.report : null;
  return (
    <>
      <Link href="/">← Release history</Link>
      <h1>Release evaluation</h1>
      <p className="commit"><code>{run.commit_sha}</code> <Status value={report ? run.state : 'pending'} /></p>
      <p className="intro">Synthetic evaluation</p>
      {report ? (
        <>
          <div className="comparison" aria-label="Gate decisions">
            {(['baseline', 'candidate'] as const).map((variant) => (
              <section className="panel result" key={variant}>
                <h2>{variant === 'baseline' ? 'Stable' : 'Candidate'}</h2>
                <Status value={report[variant].verdict} />
                <p>{Math.round(report[variant].score * 100)}% of cases passed</p>
                {report[variant].critical_failures.length > 0 && <p>Critical failures: {report[variant].critical_failures.join(', ')}</p>}
              </section>
            ))}
          </div>
          <details className="panel provenance">
            <summary>Evaluation provenance</summary>
            <dl><dt>Dataset digest</dt><dd><code>{report.dataset_digest}</code></dd><dt>Policy digest</dt><dd><code>{report.policy_digest}</code></dd></dl>
          </details>
          <section aria-labelledby="cases-heading">
            <div className="case-heading">
              <h2 id="cases-heading">Case results</h2>
              <nav aria-label="Case filters" className="filters">
                <Link href={path} aria-current={!failuresOnly ? 'page' : undefined}>All cases</Link>
                <Link href={`${path}?outcome=fail`} aria-current={failuresOnly ? 'page' : undefined}>Failed cases</Link>
              </nav>
            </div>
            {cases.items.length === 0 && <p className="panel">{failuresOnly ? 'No failed cases on this page.' : 'No cases on this page.'}</p>}
            {cases.items.map((item) => (
              <article className="panel case" key={item.case_id}>
                <div className="case-heading"><h3>{item.question}</h3>{item.critical && <span className="critical">Critical case</span>}</div>
                <p>Expected citation <code>{item.required_citation}</code></p>
                <div className="comparison"><Evidence label="Stable" variant={item.baseline} /><Evidence label="Candidate" variant={item.candidate} /></div>
              </article>
            ))}
            <nav aria-label="Case pages" className="page-links">
              {after && <Link href={failuresOnly ? `${path}?outcome=fail` : path}>First page</Link>}
              {cases.next_cursor && <Link href={{ pathname: path, query: { ...(failuresOnly && { outcome: 'fail' }), after: cases.next_cursor } }}>Next page</Link>}
            </nav>
          </section>
        </>
      ) : <p className="panel">Evaluation is pending. <a href={path}>Refresh run</a></p>}
    </>
  );
}
