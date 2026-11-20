'use client';

import { useActionState } from 'react';
import { loginWorkspace, newWorkspace } from './tracker-actions';

export function WorkspaceLogin() {
  const [state,create,pending]=useActionState(newWorkspace,{key:'',error:''});
  return <section className="panel tracker-section"><h2>Personal workspace</h2>
    {state.key ? <><p><strong>Save your personal sign-in key.</strong> You’ll need it to reopen this workspace on another device.</p>
      <code className="workspace-key">{state.key}</code>
      <form action={loginWorkspace}><input type="hidden" name="key" value={state.key}/><button className="primary">Open my workspace</button></form></>
      : <><p>Save public repositories to your own list and return to their live measurements.</p>
        <form action={loginWorkspace} className="repo-picker"><label htmlFor="workspace-key">Workspace key</label>
          <input type="password" id="workspace-key" name="key" autoComplete="current-password" required maxLength={64}/>
          <button className="primary">Sign in</button></form>
        <form action={create}><button className="quiet" disabled={pending}>{pending ? 'Creating workspace…' : 'Create free workspace'}</button></form></>}
    {state.error && <p role="alert">{state.error}</p>}</section>;
}
