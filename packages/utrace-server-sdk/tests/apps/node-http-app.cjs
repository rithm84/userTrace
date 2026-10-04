'use strict';

/**
 * A plain Node HTTP product service.
 *
 * It is a CommonJS application started after the uTrace preload, which is the
 * supported bootstrap path: every module it requires is patched before use.
 */

const http = require('http');

const port = Number(process.env.APP_PORT);
const databaseUrl = process.env.APP_DATABASE_URL;
const fetchEcho = process.env.APP_FETCH_ECHO === '1';

let pool = null;
if (typeof databaseUrl === 'string' && databaseUrl !== '') {
  const { Pool } = require('pg');
  pool = new Pool({ connectionString: databaseUrl, max: 2 });
}

function callInternalEcho() {
  return new Promise((resolve, reject) => {
    const request = http.get(
      { host: '127.0.0.1', port, path: '/api/internal/echo' },
      (response) => {
        response.resume();
        response.on('end', resolve);
      },
    );
    request.on('error', reject);
  });
}

const server = http.createServer((request, response) => {
  const path = (request.url ?? '/').split('?')[0];

  if (path === '/healthz') {
    response.end('ok');
    return;
  }
  if (path === '/admin/secret') {
    response.end('unregistered');
    return;
  }
  if (path === '/api/internal/echo') {
    response.end('echo');
    return;
  }
  if (path.startsWith('/api/saved-views/')) {
    callInternalEcho()
      .then(async () => {
        if (fetchEcho) {
          const echoed = await fetch(`http://127.0.0.1:${port}/api/internal/echo`);
          await echoed.text();
        }
        if (pool !== null) {
          await pool.query('SELECT view_id FROM saved_views WHERE view_id = $1', [1]);
        }
        response.setHeader('content-type', 'application/json');
        response.end(JSON.stringify({ view_id: path.split('/').pop() }));
      })
      .catch((error) => {
        response.statusCode = 500;
        response.end(String(error.message));
      });
    return;
  }
  response.statusCode = 404;
  response.end();
});

server.listen(port, '127.0.0.1', () => {
  process.stdout.write('APP_LISTENING\n');
});

process.on('SIGTERM', () => {
  server.close();
  if (pool !== null) {
    void pool.end();
  }
});
