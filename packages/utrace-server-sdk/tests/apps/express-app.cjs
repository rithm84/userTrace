'use strict';

/** An Express product service started after the uTrace preload. */

const express = require('express');

const port = Number(process.env.APP_PORT);
const app = express();

app.use((request, response, next) => {
  response.setHeader('x-app-middleware', 'ran');
  next();
});

app.get('/healthz', (request, response) => {
  response.send('ok');
});

app.get('/api/saved-views/:view_id', (request, response) => {
  response.json({ view_id: request.params.view_id });
});

app.get('/api/saved-views/:view_id/boom', () => {
  throw new Error('saved view 7 for maya@dudulemon.test exploded');
});

const server = app.listen(port, '127.0.0.1', () => {
  process.stdout.write('APP_LISTENING\n');
});

process.on('SIGTERM', () => {
  server.close();
});
