/**
 * An ESM product service that uses the typed annotation surface.
 *
 * It starts after the uTrace preload, which registers the ESM module hook, so
 * its `import` graph is patched before any of it runs.
 */

import { createServer } from 'node:http';

import {
  attachEntityClass,
  attachSafeValue,
  classifyExpectedOutcome,
  recordWorkflowMilestone,
  withProductOperation,
} from '../../dist/index.js';

const port = Number(process.env.APP_PORT);

const server = createServer((request, response) => {
  const path = (request.url ?? '/').split('?')[0];

  if (path === '/api/saved-views') {
    const body = withProductOperation('saved_view.create', () => {
      attachEntityClass('saved_view');
      attachSafeValue('saved_view_tier', 'priority');
      attachSafeValue('saved_view_filter_count', 6);
      recordWorkflowMilestone('saved_view.filters_applied');
      classifyExpectedOutcome('saved_view.created');
      return { view_id: 99 };
    });
    response.setHeader('content-type', 'application/json');
    response.end(JSON.stringify(body));
    return;
  }

  if (path === '/api/saved-views/unregistered') {
    const body = withProductOperation('billing.charge_customer', () => ({ ok: true }));
    response.setHeader('content-type', 'application/json');
    response.end(JSON.stringify(body));
    return;
  }

  if (path.startsWith('/api/saved-views/')) {
    response.setHeader('content-type', 'application/json');
    response.end(JSON.stringify({ view_id: path.split('/').pop() }));
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
});
