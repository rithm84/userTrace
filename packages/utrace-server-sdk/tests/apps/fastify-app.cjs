'use strict';

/** A Fastify product service started after the uTrace preload. */

const Fastify = require('fastify');

const port = Number(process.env.APP_PORT);
const app = Fastify();

app.get('/healthz', async () => ({ status: 'ok' }));

app.get('/api/saved-views/:view_id', async (request) => ({
  view_id: request.params.view_id,
}));

app.get('/api/saved-views/:view_id/boom', async () => {
  const error = new Error('saved view 7 for maya@dudulemon.test exploded');
  error.name = 'SavedViewNotFoundError';
  throw error;
});

app.listen({ port, host: '127.0.0.1' }).then(() => {
  process.stdout.write('APP_LISTENING\n');
});

process.on('SIGTERM', () => {
  void app.close();
});
