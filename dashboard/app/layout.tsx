import type { Metadata } from 'next';
import Link from 'next/link';
import { signOut } from './actions';
import { viewerToken } from '../lib/releases';
import './styles.css';

export const metadata: Metadata = { title: 'ReleaseTrace', description: 'Review release evaluations and case evidence.' };

export default async function Layout({ children }: { children: React.ReactNode }) {
  const token = await viewerToken();
  return (
    <html lang="en">
      <body>
        <header className="topbar">
          <div className="shell topbar-inner">
            <Link className="brand" href="/">ReleaseTrace</Link>
            {token && <form action={signOut}><button className="quiet">Sign out</button></form>}
          </div>
        </header>
        <main className="shell content">{children}</main>
      </body>
    </html>
  );
}
