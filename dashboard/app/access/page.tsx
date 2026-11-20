import Link from 'next/link';
import { notFound, redirect } from 'next/navigation';
import { readApi, ReleaseApiError } from '../../lib/releases';
import { sessionToken } from '../../lib/session';
import { requestViewerAccess } from '../actions';

export default async function ProjectAccess({searchParams}: {searchParams:Promise<{notice?:string}>}) {
  if (process.env.RELEASETRACE_LIVE_TRACKING === '1') notFound();
  const token = await sessionToken();
  if (!token) redirect('/');
  let rows: {repository_id:string; status:string}[];
  try {
    rows = await readApi('/v1/me/access-requests',token);
  } catch (error) {
    if (error instanceof ReleaseApiError && error.status===401) redirect('/');
    if (error instanceof ReleaseApiError && error.status===403) notFound();
    return <><h1>Project access</h1><p role="alert">Requests are temporarily unavailable.</p><Link href="/access">Try again</Link></>;
  }
  const {notice} = await searchParams;
  return <><Link href="/">Release history</Link><h1>Project access</h1>
    <section className="panel"><h2>Request viewer access</h2>
      <p>Enter the numeric GitHub repository ID from your project operator. An operator will review your request.</p>
      <form action={requestViewerAccess} className="repo-picker"><label htmlFor="repo-id">Repository ID</label>
        <input id="repo-id" name="repository_id" inputMode="numeric" pattern="[1-9][0-9]{0,18}" maxLength={19} required/>
        <button className="primary">Request access</button></form>
      {notice === 'pending' && <p role="status">Request submitted. Check this page for the decision.</p>}
      {notice === 'invalid' && <p role="alert">Enter a valid numeric repository ID.</p>}
      {notice === 'conflict' && <p role="alert">Check your current access and pending requests. Each reviewer can have ten pending requests.</p>}
      {notice === 'unavailable' && <p role="alert">Could not submit the request. Try again.</p>}
    </section>
    <section className="panel tracker-section"><h2>Your recent requests</h2>
      {rows.length ? <ul>{rows.map(row=><li key={row.repository_id}>Repository {row.repository_id} · {row.status}</li>)}</ul> : <p>No requests yet.</p>}
      <Link href="/access">Refresh requests</Link></section></>;
}
