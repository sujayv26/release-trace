'use server';

import { cookies } from 'next/headers';
import { redirect } from 'next/navigation';
import { accessCookie, historyPath, readApi, ReleaseApiError } from '../lib/releases';

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
