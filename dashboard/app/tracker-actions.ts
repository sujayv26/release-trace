'use server';

import { redirect } from 'next/navigation';
import { github, githubNotice, repositoryName, type Repo } from '../lib/github-tracker';
import { beginEnrollment, changeRepository, createWorkspaceKey, logoutTracker, signInKey, trackerSession } from '../lib/tracker-session';

type LoginForm = {key:string; secret:string; ticket:string; error:string; created:boolean};
export async function loginWorkspace(_previous:LoginForm, form:FormData): Promise<LoginForm> {
  const empty = {key:'',secret:'',ticket:'',created:false};
  const created = form.get('intent')==='create';
  try {
    const key = created ? await createWorkspaceKey() : String(form.get('key') || '');
    const code = String(form.get('code') || '').trim();
    if (!code) {
      const setup = await beginEnrollment(key);
      if (setup) return {key, ...setup, created, error:''};
    }
    if (!code || !await signInKey(key,code,String(form.get('ticket') || ''))) {
      return {...empty,error:'Sign-in failed. Check your workspace key and enter a fresh code. During setup, leave the code blank to restart. After five failed codes, wait five minutes.'};
    }
  } catch {
    return {...empty,error:'Workspace access is temporarily unavailable. Please try again later.'};
  }
  redirect('/');
}

export async function openRepository(form: FormData) {
  const name = repositoryName(String(form.get('repository') || ''));
  if (!name) redirect('/?notice=name');
  const session = await trackerSession();
  let repository: Repo;
  try { repository = await github<Repo>('/repos/'+name, session?.token); }
  catch (error) { redirect('/?notice='+githubNotice(error)); }
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
