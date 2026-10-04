# uTrace control plane image: one image, two entry points.
#
# Build context is the repository root, because `utrace_contracts` resolves its
# JSON Schemas from `packages/utrace-contracts/schemas` relative to its own
# source file and refuses to import without them. The repository layout is
# therefore reproduced under /srv/utrace rather than flattened.
#
#   API     uvicorn --factory app.main:create_default_app   (the image CMD)
#   worker  utrace-worker                                    (the worker's staged CMD)
#
# Supabase Compute builds it: deploy/compute/deploy.py stages the files this
# copies into one build directory per compute and runs `supabase compute push`.
# The worker's staged copy appends its own CMD, since a compute has no
# per-compute environment to choose an entry point with.

FROM public.ecr.aws/docker/library/python:3.13-slim AS builder

# Pinned to the uv release that produced apps/api/uv.lock. A newer uv may
# rewrite the lock; --frozen then fails loudly instead of resolving silently.
COPY --from=ghcr.io/astral-sh/uv:0.11.2 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /srv/utrace/apps/api

# The contracts package is an editable path dependency of apps/api, so its
# source tree is part of the runtime image, not just of the build.
COPY packages/utrace-contracts/python/pyproject.toml /srv/utrace/packages/utrace-contracts/python/
COPY packages/utrace-contracts/python/src /srv/utrace/packages/utrace-contracts/python/src
COPY packages/utrace-contracts/schemas /srv/utrace/packages/utrace-contracts/schemas

# Dependencies resolve from the lock alone, so this layer is reused whenever
# only application code changed.
COPY apps/api/pyproject.toml apps/api/uv.lock /srv/utrace/apps/api/
RUN uv sync --frozen --no-dev --no-install-project

COPY apps/api/app /srv/utrace/apps/api/app
RUN uv sync --frozen --no-dev


FROM public.ecr.aws/docker/library/python:3.13-slim AS runtime

RUN groupadd --system --gid 10001 utrace \
    && useradd --system --uid 10001 --gid utrace --home-dir /srv/utrace --shell /usr/sbin/nologin utrace

COPY --from=builder --chown=utrace:utrace /srv/utrace /srv/utrace

ENV PATH=/srv/utrace/apps/api/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /srv/utrace/apps/api
USER utrace

# Compute's launcher wraps this command and serves whatever listens on $PORT.
# --timeout-keep-alive outlasts the proxy's idle timeout so the server never
# closes a connection the proxy still considers reusable, which surfaces as
# sporadic 502s.
CMD ["sh", "-c", "exec uvicorn --factory app.main:create_default_app --host 0.0.0.0 --port \"$PORT\" --timeout-keep-alive 3660"]
