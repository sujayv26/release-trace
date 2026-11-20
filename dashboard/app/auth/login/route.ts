import { createHash, randomBytes } from 'node:crypto';
import { NextResponse } from 'next/server';
import { oauthEnabled, secure, siteOrigin } from '../../../lib/tracker-session';

export async function GET() {
  if (!oauthEnabled()) return NextResponse.redirect(new URL('/?notice=setup',siteOrigin()));
  const state = randomBytes(32).toString('base64url');
  const verifier = randomBytes(32).toString('base64url');
  const authorize = new URL('https://github.com/login/oauth/authorize');
  authorize.search = new URLSearchParams({ client_id: process.env.RT_GITHUB_CLIENT_ID!,
    redirect_uri: siteOrigin() + '/auth/callback', state,
    code_challenge: createHash('sha256').update(verifier).digest('base64url'), code_challenge_method: 'S256' }).toString();
  const res = NextResponse.redirect(authorize);
  // Lax permits GitHub's top-level return; the one-time code also requires this browser's PKCE verifier.
  res.cookies.set((secure() ? '__Host-' : '')+'rt-oauth',state+'.'+verifier,
    {secure:secure(),httpOnly:true,sameSite:'lax',path:'/',maxAge:600});
  res.headers.set('Cache-Control','no-store');
  return res;
}
