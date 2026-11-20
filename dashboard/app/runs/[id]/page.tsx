import Link from 'next/link';
import { notFound, redirect } from 'next/navigation';
import { recordDecision, startInvestigation } from '../../actions';
import { Evidence, RunTime, Status } from '../../components';
import { type Case, type Page, readApi, ReleaseApiError, type RunDetail, viewerToken } from '../../../lib/releases';

export default async function RunPage({ params, searchParams }: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ outcome?: string; after?: string; investigation?: string; decision?: string }>;
}) {
  const { id } = await params;
  const { outcome, after, investigation, decision } = await searchParams;
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
  const verdict = report ? report.verdict ?? report.candidate.verdict : 'pending';
  const criticalCount = report?.candidate.critical_failures.length ?? 0;
  const decisionText = report?.reason === 'baseline_not_verified'
    ? 'The reference version could not be verified. Check the stable evidence before reviewing this candidate.'
    : verdict === 'block' && criticalCount > 0
      ? `${criticalCount} critical ${criticalCount === 1 ? 'case failed' : 'cases failed'}. Open failed cases to see which answers changed.`
      : verdict === 'block' ? 'The proposed version did not meet the release rules. Review the case results below.'
      : verdict === 'pass' ? 'The proposed version met the evaluation rules. Review the evidence before recording an approval.'
      : verdict === 'inconclusive' ? 'Some required evidence could not be verified. Review the case results to see what is missing.'
      : 'The worker is still gathering evidence. Refresh this run to see the result.';
  return (
    <>
      <Link href={{ pathname: '/', query: { project: run.project_id } }}>← Release history</Link>
      <section className="decision-hero" data-verdict={verdict}>
        <p className="eyebrow">Release evaluation</p>
        <div className="decision-heading"><h1>Release decision</h1><Status value={verdict} /></div>
        <p>{decisionText}</p>
        {report && <a className="decision-link" href="#cases-heading">Compare case evidence <span aria-hidden="true">↓</span></a>}
      </section>
      {investigation === 'access' && <p role="alert">A maintainer token is required to start an investigation.</p>}
      {investigation === 'unavailable' && <p role="alert">The investigation could not be started. Try again shortly.</p>}
      {decision === 'unavailable' && <p role="alert">The release decision could not be recorded. Review the current run and your access.</p>}
      <div className="run-detail-meta">
        <span>{run.evaluation_mode === 'build_endpoints' ? 'Application comparison' : 'Simulated evaluation'}</span>
        <span>Commit <code>{run.commit_sha.slice(0, 12)}</code></span>
        <span>Queued <RunTime value={run.created_at} />{report && <> · Completed <RunTime value={run.completed_at} /></>}</span>
      </div>
      {report ? (
        <>
          {run.approval && <p>
            {run.approval.decision === 'approve' ? 'Approved' : 'Rejected'} by {run.approval.actor} at <RunTime value={run.approval.created_at} />
          </p>}
          {run.can_record_decision && report.builds && (
            <form action={recordDecision.bind(null, id, report.builds.candidate.image_digest)}>
              {report.verdict === 'pass' && <button className="primary" name="decision" value="approve">Approve candidate</button>}
              {' '}<button className="quiet" name="decision" value="reject">Reject candidate</button>
            </form>
          )}
          {run.github_check_id && <p>Published GitHub Check <code>{run.github_check_id}</code></p>}
          {run.github_check_retry_at && <p>GitHub Check publication will retry after <RunTime value={run.github_check_retry_at} />.</p>}
          <div className="comparison" aria-label="Gate decisions">
            {(['baseline', 'candidate'] as const).map((variant) => (
              <section className="panel result" data-variant={variant} key={variant}>
                <div className="case-heading"><h2>{variant === 'baseline' ? 'Stable' : 'Candidate'}</h2><Status value={report[variant].verdict} /></div>
                <p>{variant === 'baseline' ? 'Reference version' : 'Proposed version'}</p>
                <div className="score"><strong>{Math.round(report[variant].score * 100)}%</strong> of cases passed</div>
                <progress max={1} value={report[variant].score} aria-label={`${variant === 'baseline' ? 'Stable' : 'Candidate'} share of cases passed`} />
                {report[variant].critical_failures.length > 0 && <p>Critical failures: {report[variant].critical_failures.join(', ')}</p>}
              </section>
            ))}
          </div>
          <details className="panel provenance">
            <summary>Evaluation provenance</summary>
            <p>Exact versions of the builds, questions, and release rules used for this run.</p>
            <dl>
              <dt>Evaluated commit</dt><dd><code>{run.commit_sha}</code></dd>
              {report.suite_revision && <><dt>Suite revision</dt><dd><code>{report.suite_revision}</code></dd></>}
              {report.policy_revision && <><dt>Policy revision</dt><dd><code>{report.policy_revision}</code></dd></>}
              <dt>Dataset digest</dt><dd><code>{report.dataset_digest}</code></dd>
              <dt>Policy digest</dt><dd><code>{report.policy_digest}</code></dd>
              {report.builds && <>
                <dt>Stable commit</dt><dd><code>{report.builds.baseline.commit_sha}</code></dd>
                <dt>Stable image digest</dt><dd><code>{report.builds.baseline.image_digest}</code></dd>
                <dt>Candidate image digest</dt><dd><code>{report.builds.candidate.image_digest}</code></dd>
              </>}
            </dl>
          </details>
          {run.approval?.decision === 'approve' && (
            <section className="panel provenance" aria-labelledby="rollout-heading">
              <h2 id="rollout-heading">Rollout</h2>
              {run.deployment ? <>
                <p><Status value={run.deployment.outcome} /> · Recorded <RunTime value={run.deployment.recorded_at} /></p>
                <dl>
                  <dt>Rollout</dt><dd><code>{run.deployment.namespace}/{run.deployment.rollout}</code></dd>
                  <dt>Candidate digest</dt><dd><code>{run.deployment.image_digest}</code></dd>
                  <dt>Stable revision digest</dt><dd><code>{run.deployment.stable_image_digest}</code></dd>
                </dl>
              </> : <p>Awaiting an observed rollout outcome.</p>}
            </section>
          )}
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
                <p className="expected-source">Expected source <code>{item.required_citation}</code></p>
                <div className="comparison"><Evidence label="Stable" variant={item.baseline} /><Evidence label="Candidate" variant={item.candidate} /></div>
                {item.investigation_id ? <p><Link href={`${path}/investigations/${item.investigation_id}`}>View investigation</Link></p> : (
                  run.can_investigate && item.candidate.outcome === 'fail' && (
                    <form action={startInvestigation.bind(null, id, item.case_id)}><button className="primary">Investigate</button></form>
                  )
                )}
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
