'use strict';

/**
 * The Express product service the control-socket tests observe.
 *
 * It is started after the uTrace preload, which is the supported bootstrap
 * path, so every module it requires is patched before use. The saved-view route
 * is the manifest's one required product route, and it reads a real row so the
 * trace carries the database hop the propagation probe requires.
 */

const express = require('express');
const { Pool } = require('pg');

const port = Number(process.env.APP_PORT);
const pool = new Pool({ connectionString: process.env.APP_DATABASE_URL, max: 2 });

const app = express();

app.get('/healthz', (request, response) => {
  response.send('ok');
});

app.get('/api/saved-views/:view_id', (request, response) => {
  pool
    .query('SELECT view_id, label FROM saved_views WHERE view_id = $1', [
      Number(request.params.view_id),
    ])
    .then((result) => {
      response.json({ view_id: request.params.view_id, rows: result.rowCount });
    })
    .catch((error) => {
      response.status(500).json({ error: error.name });
    });
});

const server = app.listen(port, '127.0.0.1', () => {
  process.stdout.write('APP_LISTENING\n');
});

process.on('SIGTERM', () => {
  server.close();
  void pool.end();
});
