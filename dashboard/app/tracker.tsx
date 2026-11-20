import { github, GitHubError, measure, repositoryName, type Repo } from '../lib/github-tracker';
import { savedRepositories, oauthEnabled, storageEnabled, trackerSession } from '../lib/tracker-session';
import { WorkspaceLogin } from './workspace-login';
import { openRepository, removeRepository, signOutTracker } from './tracker-actions';

const notices: Record<string,string> = {
  key:'That workspace key was not recognized.',
  name:'Enter a GitHub repository URL or owner/repository.',
  repository:'GitHub could not open this repository. Check its name, your access, or try again after the GitHub rate limit resets.',
  login:'GitHub sign-in did not complete. Please try again.',
  setup:'Account sign-in is awaiting host configuration. You can inspect public repositories now.',
  limit:'Your workspace has 12 repositories. Remove one before adding another.',
};

export async function Tracker({ query }: { query: Record<string,string | string[] | undefined> }) {
  const session = await trackerSession();
  const saved = session ? await savedRepositories(session.userId) : [];
  // Refresh names and permissions using the visitor's token. Removed access must not reveal cached private metadata.
  const projects = await Promise.all(saved.map(async id => {
    try { return await github<Repo>('/repositories/'+id, session!.token); }
    catch { return { id,full_name:'Unavailable repository',default_branch:'',private:true }; }
  }));
  const name = typeof query.repo === 'string' && repositoryName(query.repo);
  let selected = projects.find(p => String(p.id) === query.selected && p.default_branch);
  let error = notices[String(query.notice)] || '';
  let metrics: Awaited<ReturnType<typeof measure>> | undefined;
  try {
    if (name && !selected) selected = await github<Repo>('/repos/'+name);
    if (selected) metrics = await measure(selected, name && !query.selected ? undefined : session?.token);
  } catch (e) {
    error = e instanceof GitHubError && e.status === 401 ? 'GitHub access expired. Sign out and sign in again.'
      : 'GitHub metrics are unavailable right now. Check access and try again after the GitHub rate limit resets.';
  }
  const base = selected && 'https://github.com/'+selected.full_name;
  const install = process.env.RT_GITHUB_APP_SLUG;
  return <>
    <section className="hero overview-hero"><div><p className="eyebrow">GitHub release tracking</p>
      <h1>{session ? 'Your release workspace' : 'Track a repository'}</h1>
      <p>Inspect published releases, recent workflow runs, and release checks on the latest default-branch commit.</p></div>
      <div>{session ? <><p>Signed in as <strong>{session.login}</strong></p><form action={signOutTracker}><button className="quiet">Sign out</button></form></>
        : oauthEnabled() ? <a className="hero-run" href="/auth/login">Sign in with GitHub</a>
        : <a className="hero-run" href="#workspace">Open personal workspace</a>}</div></section>
    {!session && storageEnabled() && <div id="workspace"><WorkspaceLogin/></div>}
    <section className="panel"><form action={openRepository} className="repo-picker">
      <label htmlFor="repository">GitHub repository</label><input id="repository" name="repository" required maxLength={180}
        placeholder="owner/repository" defaultValue={selected?.full_name} autoComplete="off"/>
      <button className="primary">{session ? 'Track repository' : 'Inspect repository'}</button></form>
      {error && <p role="alert">{error}</p>}
      {session?.token && install && <p><a href={'https://github.com/apps/'+install+'/installations/new'}>Choose repositories on GitHub</a></p>}
      {!session && <p><a href="/?repo=sujayv26%2Frelease-trace">Inspect ReleaseTrace’s live repository</a></p>}
    </section>
    {session && <section className="panel tracker-section"><h2>Tracked repositories</h2>
      {projects.length === 0 && <p>Enter your first repository above to save it to your workspace.</p>}
      {projects.map(p=><div className="tracked-row" key={p.id}><a href={'/?selected='+p.id}>{p.full_name}</a>
        <form action={removeRepository}><input type="hidden" name="id" value={p.id}/><button className="quiet">Remove</button></form></div>)}</section>}
    {selected && metrics && <>
      <div className="history-heading tracker-section"><div><p className="eyebrow">{selected.default_branch} · GitHub activity</p>
        <h2><a href={base!}>{selected.full_name}</a></h2></div>
        <a className="quiet" href={query.selected ? '/?selected='+selected.id : '/?repo='+encodeURIComponent(selected.full_name)}>Refresh measurements</a></div>
      <div className="tracker-metrics">
        <article className="panel"><h3>Latest published release</h3><p className="metric">{metrics.releases[0]?.tag_name || 'None'}</p>
          <p>{metrics.releases[0]?.published_at?.slice(0,10) || 'No published releases in the sample'}</p></article>
        <article className="panel"><h3>Workflow success</h3><p className="metric">{metrics.completed ? Math.round(100*metrics.successes/metrics.completed)+'%' : '—'}</p>
          <p>{metrics.successes} of {metrics.completed} completed runs in the latest {metrics.runs?.length ?? 0}</p></article>
        <article className="panel"><h3>Workflow elapsed p95</h3><p className="metric">{metrics.p95 === null ? '—' : Math.round(metrics.p95)+' s'}</p>
          <p>Start to last update across {metrics.timed} completed runs</p></article></div>
      <section className="panel tracker-section"><h2>ReleaseTrace gate</h2>
        {metrics.commit && <p>Default branch head <a href={base+'/commit/'+metrics.commit.sha}><code>{metrics.commit.sha.slice(0,12)}</code></a></p>}
        {metrics.checks?.length ? metrics.checks.map(c => <p key={c.id}><a href={base+'/runs/'+c.id}>{c.name}</a> · {c.conclusion || c.status} · published by {c.app?.slug}</p>)
          : <p>{!metrics.commit ? 'No default-branch commit to evaluate.' : metrics.checks ? 'No ReleaseTrace evaluation has been published on this commit.' : 'Check data unavailable for this commit.'}</p>}</section>
      <section className="panel tracker-section"><h2>Recent workflow runs</h2>
        {metrics.runs?.length ? <ul className="run-list">{metrics.runs.map(r=><li key={r.id}><a className="run-row" href={base+'/actions/runs/'+r.id}>
          <strong>{r.name}</strong><span>{r.conclusion || r.status}</span><code>{r.head_sha.slice(0,7)}</code></a></li>)}</ul>
          : <p>{metrics.runs ? 'No workflow runs on this branch.' : 'Workflow data unavailable.'}</p>}</section>
      <section className="panel tracker-section"><h2>Published releases</h2>{metrics.releases.length ? <ul>{metrics.releases.map(r=>
        <li key={r.tag_name}><a href={base+'/releases/tag/'+encodeURIComponent(r.tag_name)}>{r.tag_name}</a> · {r.published_at?.slice(0,10)} {r.prerelease && '· prerelease'}</li>)}</ul> : <p>No published releases.</p>}</section>
    </>}
    <p className="page-links"><a href="https://releasetrace-checkout-demo.excellentplover16.chatgpt.site/">Explore the synthetic checkout walkthrough</a></p>
  </>;
}
