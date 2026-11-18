import Link from 'next/link';
import { notFound, redirect } from 'next/navigation';
import { Evidence, Status } from '../../../../components';
import { type Investigation, readApi, ReleaseApiError, viewerToken } from '../../../../../lib/releases';

const findings: Record<string, string> = {
  restored_expected_citation: 'The controlled rerun brought back the expected citation.',
  retrieval_settings_match: 'Both versions already use the same retrieval setting.',
  rerun_did_not_establish_cause: 'The rerun did not establish a cause for the failure.',
  settings_match: 'The stable and candidate settings match.',
  experiment_budget_exhausted: 'The investigation reached its experiment limit.',
  planner_timeout: 'The investigation planner timed out.',
  planner_abstained: 'The planner found no suitable experiment.',
  unsupported_intervention: 'The proposed experiment is unavailable for this investigation.',
};

const settingNames: Record<string, string> = { retrieval: 'Retrieval', prompt: 'Prompt', tool_response: 'Recorded tool response' };

export default async function InvestigationPage({ params }: {
  params: Promise<{ id: string; investigationId: string }>;
}) {
  const { id, investigationId } = await params;
  if (!/^[A-Za-z0-9-]{1,128}$/.test(id) || !/^[a-f0-9]{64}$/.test(investigationId)) notFound();
  const token = await viewerToken();
  if (!token) redirect('/');
  const runPath = `/runs/${id}`;
  const path = `${runPath}/investigations/${investigationId}`;
  let investigation: Investigation;
  try {
    investigation = await readApi<Investigation>(`/v1${path}`, token);
  } catch (error) {
    if (error instanceof ReleaseApiError && error.status === 404) notFound();
    if (error instanceof ReleaseApiError && error.status === 401) redirect('/');
    return <><Link href={runPath}>← Release evaluation</Link><h1>Investigation</h1><p role="alert">Could not load the investigation.</p><a href={path}>Try again</a></>;
  }
  const { report } = investigation;
  return (
    <>
      <Link href={runPath}>← Release evaluation</Link>
      <h1>Investigation</h1>
      <p><code>{investigation.case_id}</code> <Status value={report?.outcome ?? investigation.state} /></p>
      {report ? <>
        <p>{findings[report.reason] ?? 'Review the saved evidence below.'}</p>
        <p>Expected citation <code>{report.required_citation}</code></p>
        {report.changed_parameter && <p>{settingNames[report.changed_parameter.name] ?? report.changed_parameter.name} changed from <code>{report.changed_parameter.before}</code> to <code>{report.changed_parameter.after}</code>.</p>}
        <section className="panel investigation-comparison" aria-label="Investigation evidence">
          <Evidence label="Stable" variant={report.baseline} />
          <Evidence label="Candidate" variant={report.candidate} />
          {report.experiment && <Evidence label="Controlled rerun" variant={report.experiment} />}
        </section>
        <details className="panel provenance">
          <summary>Evaluation provenance</summary>
          <dl><dt>Dataset digest</dt><dd><code>{report.dataset_digest}</code></dd><dt>Policy digest</dt><dd><code>{report.policy_digest}</code></dd></dl>
        </details>
      </> : <p className="panel">The worker hasn’t finished this investigation. <a href={path}>Refresh report</a></p>}
    </>
  );
}
