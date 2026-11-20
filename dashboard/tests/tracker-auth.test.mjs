import assert from 'node:assert/strict';
import { randomBytes } from 'node:crypto';
import { registerHooks } from 'node:module';
import { test } from 'node:test';
import { TOTP } from 'otpauth';

// Replace only the network and Next request context. Exercise the production
// enrollment, encryption, session validation and conditional update code.
const records = new Map();
const jar = new Map();
const mocks = {
  'server-only': '',
  '@vercel/blob': `
    export class BlobPreconditionFailedError extends Error {}
    export async function get(path) {
      const r = globalThis.trackerRecords.get(path);
      return r && {statusCode:200,stream:r.body,blob:{etag:r.etag}};
    }
    export async function put(path, body, opts) {
      const r = globalThis.trackerRecords.get(path);
      if (opts.ifMatch !== r?.etag) throw new BlobPreconditionFailedError();
      globalThis.trackerRecords.set(path,{body,etag:String(Number(r?.etag || 0)+1)});
    }
    export async function del(path) { globalThis.trackerRecords.delete(path); }`,
  'next/headers': `
    export const cookies = async () => ({
      get: k => ({value:globalThis.trackerJar.get(k)}),
      set: (k,v) => globalThis.trackerJar.set(k,v),
      delete: k => globalThis.trackerJar.delete(k)
    });
    export const headers = async () => new Headers();`,
  './github-tracker': 'export function github() { throw new Error("Unexpected GitHub request"); }',
};
globalThis.trackerRecords = records;
globalThis.trackerJar = jar;
registerHooks({resolve(specifier, context, next) {
  return Object.hasOwn(mocks,specifier)
    ? {shortCircuit:true,url:'data:text/javascript,'+encodeURIComponent(mocks[specifier])}
    : next(specifier,context);
}});
process.env.RT_TRACKER_ENCRYPTION_KEY = randomBytes(32).toString('base64');
process.env.BLOB_READ_WRITE_TOKEN = 'local-test';
const auth = await import('../lib/tracker-session.ts');

test('workspace sign-in upgrades existing keys, binds setup, limits retries and consumes each code once', async t => {
  const now = Date.UTC(2026,0,1);
  t.mock.timers.enable({apis:['Date'],now});
  const key = await auth.createWorkspaceKey();
  const other = await auth.createWorkspaceKey();
  const setup = await auth.beginEnrollment(key);
  const totp = new TOTP({secret:setup.secret});
  const code = totp.generate({timestamp:now});
  assert.equal(await auth.signInKey(other,code,setup.ticket),false);
  assert.equal(await auth.signInKey(key,code,''),false);
  const expired = await auth.beginEnrollment(other);
  t.mock.timers.tick(610000);
  assert.equal(await auth.signInKey(other,new TOTP({secret:expired.secret}).generate(),expired.ticket),false);
  const current = await auth.beginEnrollment(key);
  const authenticator = new TOTP({secret:current.secret});
  const first = authenticator.generate();
  assert.equal(await auth.trackerSession(),null);
  const parallel = await Promise.all([1,2].map(()=>auth.signInKey(key,first,current.ticket)));
  assert.deepEqual(parallel.sort(),[false,true]);
  assert.equal(await auth.beginEnrollment(key),null);
  const session = await auth.trackerSession();
  await auth.changeRepository(session.userId,3,true);
  assert.equal(await auth.logoutTracker(),undefined);
  assert.equal(await auth.trackerSession(),null);
  assert.equal(await auth.signInKey(key,first,''),false);
  t.mock.timers.tick(30000);
  const accepted = new Set([-1,0,1].map(d=>authenticator.generate({timestamp:Date.now()+30000*d})));
  let bad = '000000';
  while (accepted.has(bad)) bad = String(Number(bad)+1).padStart(6,'0');
  for (let i=0;i<4;i++) assert.equal(await auth.signInKey(key,bad,''),false);
  assert.equal(await auth.signInKey(key,authenticator.generate(),''),false);
  t.mock.timers.tick(300001);
  assert.equal(await auth.signInKey(key,authenticator.generate(),''),true);
  assert.deepEqual(await auth.savedRepositories(session.userId),[3]);
  for (const {body} of records.values()) {
    assert.equal(body.includes(current.secret) || body.includes(key),false);
  }
  const storedSession = [...records.values()].find(r=>JSON.parse(r.body).verifiedAuthenticator);
  const legacy = JSON.parse(storedSession.body);
  delete legacy.verifiedAuthenticator;
  storedSession.body = JSON.stringify(legacy);
  assert.equal(await auth.trackerSession(),null);
});
