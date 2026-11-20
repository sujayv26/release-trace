'use server';

import { redirect } from 'next/navigation';
import { github, repositoryName, type Repo } from '../lib/github-tracker';
import { changeRepository, createWorkspaceKey, logoutTracker, signInKey, trackerSession } from '../lib/tracker-session';

export async function newWorkspace() {
  try { return {key:await createWorkspaceKey(),error:''}; }
  catch { return {key:'',error:'Workspace creation is temporarily unavailable. Try later or sign in with your existing key.'}; }
}
export async function loginWorkspace(form:FormData) {
  const ok=await signInKey(String(form.get('key') || ''));
  redirect(ok ? '/' : '/?notice=key');
}

export async function openRepository(form: FormData) {
  const name = repositoryName(String(form.get('repository') || ''));
  if (!name) redirect('/?notice=name');
  const session = await trackerSession();
  let repository: Repo;
  try { repository = await github<Repo>('/repos/'+name, session?.token); }
  catch { redirect('/?notice=repository'); }
  if (!session) redirect('/?repo='+encodeURIComponent(repository.full_name));
  const saved = await changeRepository(session.userId,repository.id,true);
  redirect(saved ? '/?selected='+repository.id : '/?notice=limit');
}

export async function removeRepository(form: FormData) {
  const id = String(form.get('id') || '');
  const session = await trackerSession();
  if (session && /^\d{1,18}$/.test(id)) {
    await changeRepository(session.userId,Number(id),false);
  }
  redirect('/');
}

export async function signOutTracker() {
  await logoutTracker();
  redirect('/');
}
