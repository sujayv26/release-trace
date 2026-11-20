// The production dashboard fetches GitHub on the server, outside browser request interception.
const originalFetch = globalThis.fetch;
globalThis.fetch = (input, options) => {
  const url = new URL(input instanceof Request ? input.url : input);
  if (url.hostname !== 'api.github.com') return originalFetch(input, options);
  const responses = {
    '/repos/example/release': { id: 82, full_name: 'example/release', default_branch: 'main', private: false },
    '/repositories/82/commits/main': { sha: 'a'.repeat(40) },
    '/repositories/82/releases': [{ tag_name: 'v1.0', draft: false, published_at: '2026-01-01T00:00:00Z' }],
  };
  if (url.pathname === '/repos/example/limited') return Promise.resolve(new Response(null, {status:429}));
  const value = responses[url.pathname];
  return Promise.resolve(value ? Response.json(value) : new Response(null, {status:404}));
};

// Match the Docker image layout so the browser also loads the built CSS and JavaScript.
const {cpSync} = require('node:fs');
const {resolve} = require('node:path');
cpSync('.next/static', '.next/standalone/.next/static', {recursive:true});
require(resolve('.next/standalone/server.js'));
