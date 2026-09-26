# syntax=docker/dockerfile:1
# Two images:
#   app - the coordinator (also runs the seed nodes)
#   web - Caddy serving the website and docs, proxying everything else to app
#
# Building behind a TLS-inspecting proxy? Pass its CA:
#   docker build --secret id=ca,src=/path/to/ca.pem ...

FROM python:3.11-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install ".[server]"
COPY seeds ./seeds
COPY scripts ./scripts
RUN useradd --system --uid 10001 gemmanet && mkdir -p /data && chown gemmanet /data
USER gemmanet
ENV FORUM_DB=/data/forum.db
EXPOSE 8800
# One process only (no --workers): node connections live in memory.
# Port 8800 is reachable only from the compose network (Caddy), so the
# forwarded client IP set by Caddy can be trusted.
CMD ["uvicorn", "gemmanet.coordinator.server:app", "--host", "0.0.0.0", "--port", "8800", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]

FROM python:3.11-slim AS docs
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY docs/requirements.txt ./docs/requirements.txt
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install -r docs/requirements.txt
COPY mkdocs.yml ./
COPY docs ./docs
RUN mkdocs build --strict -d /site

FROM caddy:2 AS web
COPY deploy/Caddyfile.docker /etc/caddy/Caddyfile
COPY website /srv/website
COPY --from=docs /site /srv/docs
