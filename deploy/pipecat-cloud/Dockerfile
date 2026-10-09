# The uTrace voice agent on Pipecat Cloud.
#
# Built in the cloud from the repository root's committed tree (`deploy.py`) on the
# Pipecat base image, whose server imports `/app/bot.py` and calls `bot()` for each
# session. Our `app` package is copied to `/app/app`, where Python finds it ahead of
# the base image's own `app.py`.
FROM dailyco/pipecat-base:0.2.5-py3.13

# The project keeps its repository layout under /srv, so its relative path to the
# contracts package resolves; `--inexact` installs it into the base image's
# environment beside the packages the base server brings.
COPY packages/utrace-contracts/python/pyproject.toml /srv/packages/utrace-contracts/python/
COPY packages/utrace-contracts/python/src /srv/packages/utrace-contracts/python/src
COPY packages/utrace-contracts/schemas /srv/packages/utrace-contracts/schemas
COPY apps/api/pyproject.toml apps/api/uv.lock /srv/apps/api/
RUN cd /srv/apps/api && UV_PROJECT_ENVIRONMENT=/app/.venv \
    uv sync --locked --inexact --no-dev --group pipecat-cloud --no-install-project
COPY apps/api/app /app/app
COPY deploy/pipecat-cloud/bot.py /app/bot.py
