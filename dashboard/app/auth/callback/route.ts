import { cookies } from 'next/headers';
import { NextRequest, NextResponse } from 'next/server';
import { issueSession, oauthEnabled, secure, siteOrigin } from '../../../lib/tracker-session';

export async function GET(req: NextRequest) {
  const jar = await cookies();
  const name = (secure() ? '__Host-' : '')+'rt-oauth';
  const [state, verifier] = (jar.get(name)?.value || '').split('.');
  jar.delete(name);
  const query = req.nextUrl.searchParams;
  let ok = false;
  if (oauthEnabled() && state && /^[\w-]{43}$/.test(verifier || '') && state === query.get('state')
    && /^[a-zA-Z0-9_-]{1,256}$/.test(query.get('code') || '')) {
    try {
      const response = await fetch('https://github.com/login/oauth/access_token',{ method:'POST',
        headers:{Accept:'application/json'}, body:new URLSearchParams({
          client_id:process.env.RT_GITHUB_CLIENT_ID!,client_secret:process.env.RT_GITHUB_CLIENT_SECRET!,
          code:query.get('code')!,code_verifier:verifier,redirect_uri:siteOrigin()+'/auth/callback',
        }),redirect:'error',cache:'no-store',signal:AbortSignal.timeout(8000)});
      const body = response.ok ? await response.json() : null;
      if (typeof body?.access_token === 'string') { await issueSession(body.access_token); ok = true; }
    } catch { /* The visitor can retry. Never log the OAuth response, code, or token. */ }
  }
  const res = NextResponse.redirect(new URL(ok ? '/' : '/?notice=login',siteOrigin()));
  res.headers.set('Cache-Control','no-store');
  res.headers.set('Referrer-Policy','no-referrer');
  return res;
}
