'use server';

import { redirect } from 'next/navigation';
import { readApi, ReleaseApiError } from '../lib/releases';
import { beginBrowserSession, endBrowserSession, sessionToken } from '../lib/session';

export async function requestViewerAccess(form:FormData) {
  if (process.env.RELEASETRACE_LIVE_TRACKING === '1') redirect('/');
  const token = await sessionToken();
  if (!token) redirect('/');
  const id = String(form.get('repository_id') || '');
  if (!/^[1-9][0-9]{0,18}$/.test(id)) redirect('/access?notice=invalid');
  try {
    await readApi('/v1/me/access-requests',token,{repository_id:id});
  } catch (error) {
    if (error instanceof ReleaseApiError) {
      if (error.status===401) redirect('/');
      if (error.status===422) redirect('/access?notice=invalid');
      if (error.status===409) redirect('/access?notice=conflict');
    }
    redirect('/access?notice=unavailable');
  }
  redirect('/access?notice=pending');
}

export async function signIn(form: FormData) {
  const token = form.get('token');
  if (typeof token !== 'string' || !/^[!-~]{1,512}$/.test(token)) redirect('/?access=invalid');
  try {
    await beginBrowserSession(token);
  } catch (error) {
    if (error instanceof ReleaseApiError && error.status === 401) redirect('/?access=invalid');
    redirect('/?access=unavailable');
  }
  redirect('/');
}

export async function signOut() {
  try {
    await endBrowserSession();
  } catch {
    // The browser cookie was cleared even if the API was temporarily unavailable.
  }
  redirect('/');
}

export async function recordDecision(id: string, imageDigest: string, form: FormData) {
  if (!/^[A-Za-z0-9-]{1,128}$/.test(id)) redirect('/');
  const token = await sessionToken();
  if (!token) redirect('/');
  const path = `/runs/${id}`;
  const decision = form.get('decision');
  if (decision !== 'approve' && decision !== 'reject') redirect(path);
  try {
    await readApi(`/v1${path}/approval`, token, { decision, image_digest: imageDigest });
  } catch (error) {
    if (error instanceof ReleaseApiError && error.status === 401) redirect('/');
    redirect(`${path}?decision=unavailable`);
  }
  redirect(path);
}

export async function startInvestigation(id: string, caseId: string) {
  if (!/^[A-Za-z0-9-]{1,128}$/.test(id)) redirect('/');
  const token = await sessionToken();
  if (!token) redirect('/');
  const path = `/runs/${id}`;
  let investigationId: string;
  try {
    const result = await readApi<{ investigation_id: string }>(
      `/v1${path}/investigations`, token, { case_id: caseId },
    );
    investigationId = result.investigation_id;
  } catch (error) {
    if (error instanceof ReleaseApiError && error.status === 401) redirect('/');
    const reason = error instanceof ReleaseApiError && error.status === 403 ? 'access' : 'unavailable';
    redirect(`${path}?investigation=${reason}`);
  }
  redirect(`${path}/investigations/${encodeURIComponent(investigationId)}`);
}
