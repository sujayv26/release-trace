'use client';

import { useActionState } from 'react';
import { loginWorkspace } from './tracker-actions';

export function WorkspaceLogin() {
  const [state, submit, pending] = useActionState(loginWorkspace, {
    key:'', secret:'', ticket:'', error:'', created:false,
  });
  return <section className="panel tracker-section"><h2>Personal workspace</h2>
    {state.secret ? <>
      {state.created && <><p>Save your workspace key in your password manager.</p>
        <code className="workspace-key">{state.key}</code></>}
      <h3>Set up your authenticator</h3>
      <p>In your authenticator app, add a time-based account named ReleaseTrace and enter this setup key.
        Keep a separate offline copy to restore your authenticator if you lose your device.</p>
      <code className="authenticator-secret">{state.secret}</code>
      <form action={submit} className="repo-picker">
        <input type="hidden" name="key" value={state.key}/>
        <input type="hidden" name="ticket" value={state.ticket}/>
        <label htmlFor="setup-code">Authenticator code</label>
        <input id="setup-code" name="code" inputMode="numeric" pattern="[0-9]{6}" maxLength={6} autoComplete="one-time-code" required/>
        <button className="primary" disabled={pending}>Verify and open workspace</button>
      </form></> : <>
      <p>Sign in with your workspace key and the six-digit code from your authenticator.
        If this is your first sign-in with an existing key, leave the code blank to set it up.</p>
      <form action={submit} className="repo-picker">
        <label htmlFor="workspace-key">Workspace key</label>
        <input type="password" id="workspace-key" name="key" autoComplete="current-password" required maxLength={64}/>
        <label htmlFor="login-code">Authenticator code</label>
        <input id="login-code" name="code" inputMode="numeric" pattern="[0-9]{6}" maxLength={6} autoComplete="one-time-code"/>
        <button className="primary" disabled={pending}>Sign in</button>
      </form>
      <form action={submit}><button className="quiet" name="intent" value="create" disabled={pending}>Create free workspace</button></form>
    </>}
    {state.error && <p role="alert">{state.error}</p>}</section>;
}
