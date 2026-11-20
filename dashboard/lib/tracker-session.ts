import 'server-only';
import { BlobPreconditionFailedError, del, get, put } from '@vercel/blob';
import { createCipheriv, createDecipheriv, createHash, randomBytes } from 'node:crypto';
import { cookies, headers } from 'next/headers';
import { github } from './github-tracker';

export const siteOrigin = () => new URL(process.env.RT_TRACKER_ORIGIN || 'https://releasetrace-demo.vercel.app').origin;
export const secure = () => siteOrigin().startsWith('https://');
const cookieName = () => (secure() ? '__Host-' : '') + 'rt-tracker';
export const storageEnabled = () => Boolean(process.env.BLOB_READ_WRITE_TOKEN);
export const oauthEnabled = () => Boolean(storageEnabled() && process.env.RT_GITHUB_CLIENT_ID &&
  process.env.RT_GITHUB_CLIENT_SECRET && process.env.RT_TRACKER_ENCRYPTION_KEY);
const options = {access:'private' as const, addRandomSuffix:false, contentType:'application/json'};
const hash = (value: string) => createHash('sha256').update(value).digest('hex');
type Workspace = {repositories: number[]};
type Session = {userId:string; login:string; encryptedToken?:string; expires:number};
const workspacePath = (id:string) => 'tracker/accounts/'+id+'.json';
const sessionPath = (token:string) => 'tracker/sessions/'+hash(token)+'.json';

async function read<T>(path:string) {
  // Read the origin on every authenticated request so logout and removals are immediately visible.
  const blob = await get(path,{access:'private',useCache:false});
  if (!blob || blob.statusCode !== 200) return null;
  return {data:await new Response(blob.stream).json() as T,etag:blob.blob.etag};
}

const encryptionKey = () => Buffer.from(process.env.RT_TRACKER_ENCRYPTION_KEY || '', 'base64');
function encrypt(value: string) {
  const iv = randomBytes(12);
  const cipher = createCipheriv('aes-256-gcm', encryptionKey(), iv);
  return Buffer.concat([iv,cipher.update(value,'utf8'),cipher.final(),cipher.getAuthTag()]).toString('base64');
}
function decrypt(value:string) {
  const data = Buffer.from(value,'base64');
  const cipher = createDecipheriv('aes-256-gcm',encryptionKey(),data.subarray(0,12));
  cipher.setAuthTag(data.subarray(-16));
  return Buffer.concat([cipher.update(data.subarray(12,-16)),cipher.final()]).toString();
}
async function startSession(userId:string, login:string, token?:string) {
  const raw = randomBytes(32).toString('base64url');
  const session:Session = {userId,login,expires:Date.now()+3600_000,encryptedToken:token && encrypt(token)};
  await put(sessionPath(raw),JSON.stringify(session),{...options,ttlDays:1});
  (await cookies()).set(cookieName(),raw,{secure:secure(),httpOnly:true,sameSite:'strict',path:'/',maxAge:3600});
}
export async function issueSession(token:string) {
  const user = await github<{id:number;login:string}>('/user',token);
  const id='github-'+user.id;
  if (!await read<Workspace>(workspacePath(id))) {
    try { await put(workspacePath(id),JSON.stringify({repositories:[]}),options); }
    catch (e) { if (!await read<Workspace>(workspacePath(id))) throw e; }
  }
  await startSession(id,user.login,token);
}
export async function createWorkspaceKey() {
  const source = (await headers()).get('x-vercel-forwarded-for') || (await headers()).get('x-real-ip') || 'local';
  const day = new Date().toISOString().slice(0,10);
  const quota = 'tracker/signup/'+hash(day+'|'+source)+'.json';
  for (let attempt=0;attempt<4;attempt++) {
    const record=await read<number>(quota);
    if ((record?.data || 0)>=8) throw new Error('Workspace creation limit reached');
    try { await put(quota,JSON.stringify((record?.data || 0)+1),{...options,ifMatch:record?.etag,ttlDays:1}); break; }
    catch(e) { if (attempt===3 || !(e instanceof BlobPreconditionFailedError || !record)) throw e; }
  }
  const key='rtw_'+randomBytes(32).toString('base64url');
  await put(workspacePath('key-'+hash(key)),JSON.stringify({repositories:[]}),options);
  return key;
}
export async function signInKey(key:string) {
  if (!/^rtw_[\w-]{43}$/.test(key)) return false;
  const id='key-'+hash(key);
  if (!await read<Workspace>(workspacePath(id))) return false;
  await startSession(id,'Personal workspace');
  return true;
}
export async function trackerSession() {
  const raw=(await cookies()).get(cookieName())?.value;
  if (!raw || !/^[\w-]{43}$/.test(raw) || !storageEnabled()) return null;
  const entry=await read<Session>(sessionPath(raw));
  if (!entry || entry.data.expires<=Date.now()) return null;
  const {userId,login,encryptedToken}=entry.data;
  return {userId,login,token:encryptedToken ? decrypt(encryptedToken) : undefined};
}
export async function savedRepositories(id:string) {
  return (await read<Workspace>(workspacePath(id)))?.data.repositories || [];
}
export async function changeRepository(userId:string,repo:number,save:boolean) {
  const path=workspacePath(userId);
  for (let retry=0;retry<4;retry++) {
    const record=await read<Workspace>(path);
    if (!record) throw new Error('Workspace unavailable');
    const ids=new Set(record.data.repositories);
    if (ids.has(repo)===save) return true;
    if (save && ids.size>=12) return false;
    if (save) ids.add(repo); else ids.delete(repo);
    try {
      await put(path,JSON.stringify({repositories:[...ids]}),{...options,ifMatch:record.etag});
      return true;
    } catch(e) { if (!(e instanceof BlobPreconditionFailedError) || retry===3) throw e; }
  }
  return false;
}
export async function logoutTracker() {
  const jar=await cookies();
  const raw=jar.get(cookieName())?.value;
  if (raw && /^[\w-]{43}$/.test(raw)) await del(sessionPath(raw));
  jar.delete(cookieName());
}
