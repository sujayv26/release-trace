'use server';

import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { accessCookie, historyPath, readApi, ReleaseApiError, viewerToken } from '../lib/releases';

export async function signIn(form: FormData) {
  const token = form.get('token');
  if (typeof token !== 'string' || !/^[!-~]{1,512}$/.test(token)) redirect('/?access=invalid');
  try {
    await readApi(`${historyPath()}?limit=1`, token);
  } catch (error) {
    if (error instanceof ReleaseApiError && error.status === 401) redirect('/?access=invalid');
    redirect('/?access=unavailable');
  }
  (await cookies()).set(accessCookie, token, {
    httpOnly: true,
    sameSite: 'strict',
    secure: process.env.NODE_ENV === 'production',
    path: '/',
    maxAge: 8 * 60 * 60,
  });
  redirect('/');
}

export async function signOut() {
  (await cookies()).delete(accessCookie);
  redirect('/');
}

export async function recordDecision(id: string, imageDigest: string, form: FormData) {
  if (!/^[A-Za-z0-9-]{1,128}$/.test(id)) redirect('/');
  const token = await viewerToken();
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
  const token = await viewerToken();
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
