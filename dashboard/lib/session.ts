import 'server-only';
import { cookies } from 'next/headers';
import { releaseApi } from './releases';

const secure = process.env.NODE_ENV === 'production';
const sessionCookie = secure ? '__Host-releasetrace-session' : 'releasetrace-session';
const legacyAccessCookie = 'releasetrace-viewer';

export async function sessionToken() {
  return (await cookies()).get(sessionCookie)?.value;
}

export async function beginBrowserSession(accessKey: string) {
  const session = await releaseApi<{ session_token: string; expires_in: number }>(
    '/v1/sessions', accessKey, 'POST',
  );
  const jar = await cookies();
  jar.delete(legacyAccessCookie);
  jar.set(sessionCookie, session.session_token, {
    httpOnly: true,
    sameSite: 'strict',
    secure,
    path: '/',
    maxAge: session.expires_in,
  });
}

export async function endBrowserSession() {
  const jar = await cookies();
  const token = jar.get(sessionCookie)?.value;
  try {
    if (token) await releaseApi<void>('/v1/sessions/current', token, 'DELETE');
  } finally {
    jar.set(sessionCookie, '', { httpOnly: true, sameSite: 'strict', secure, path: '/', maxAge: 0 });
    jar.delete(legacyAccessCookie);
  }
}
