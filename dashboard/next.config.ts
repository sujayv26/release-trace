import type { NextConfig } from 'next';

const config: NextConfig = {
  output: process.env.VERCEL ? undefined : 'standalone',
  poweredByHeader: false,
  turbopack: { root: process.cwd() },
  async headers() {
    const privatePageHeaders = [{ key: 'Cache-Control', value: 'private, no-store' }];
    return [{
      source: '/:path*',
      headers: [
        { key: 'Content-Security-Policy', value: "frame-ancestors 'none'; base-uri 'self'; object-src 'none'" },
        { key: 'X-Content-Type-Options', value: 'nosniff' },
        { key: 'Referrer-Policy', value: 'no-referrer' },
      ],
    }, {
      source: '/',
      headers: privatePageHeaders,
    }, {
      source: '/auth/:path*',
      headers: privatePageHeaders,
    }, {
      source: '/runs/:path*',
      headers: privatePageHeaders,
    }];
  },
};

export default config;
