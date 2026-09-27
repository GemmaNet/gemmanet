# syntax=docker/dockerfile:1
# Images:
#   app - the coordinator (also runs the seed nodes)
#   web - Caddy serving the website and docs, proxying everything else to app
#   db  - PostgreSQL for the stack
#
# Supply chain: every base image is pinned by digest (Dependabot proposes
# updates), Python packages install only from hash-locked files
# (scripts/lock.sh), and CI fails on any fixable HIGH/CRITICAL finding
# (Trivy) in these images.
#
# Building behind a TLS-inspecting proxy? Pass its CA:
#   docker build --secret id=ca,src=/path/to/ca.pem ...

FROM python:3.11-slim@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app/src
WORKDIR /app
COPY requirements/app.txt ./requirements/app.txt
# The code runs from /app/src (PYTHONPATH), so no build tools are needed:
# setuptools and wheel, which the base image ships, are removed.
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install --require-hashes -r requirements/app.txt \
    && pip uninstall -y setuptools wheel
COPY src ./src
COPY seeds ./seeds
COPY scripts ./scripts
RUN useradd --system --uid 10001 gemmanet && mkdir -p /data && chown gemmanet /data
USER gemmanet
ENV FORUM_DB=/data/forum.db
EXPOSE 8800
# One process only (no --workers): node connections live in memory.
# Port 8800 is reachable only from the compose network (Caddy), so the
# forwarded client IP set by Caddy can be trusted.
# --ws-max-size matches GEMMANET_WS_MAX_MESSAGE_BYTES (4 MiB).
CMD ["uvicorn", "gemmanet.coordinator.server:app", "--host", "0.0.0.0", "--port", "8800", \
     "--proxy-headers", "--forwarded-allow-ips", "*", "--ws-max-size", "4194304"]

FROM python:3.11-slim@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e AS docs
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /build
COPY docs/requirements.txt ./docs/requirements.txt
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export PIP_CERT=/run/secrets/ca; fi; \
    pip install --require-hashes -r docs/requirements.txt
COPY mkdocs.yml ./
COPY docs ./docs
RUN mkdocs build --strict -d /site

# Caddy v2.11.4 rebuilt with a patched Go toolchain and libraries: the
# official v2.11.4 binary was built with Go 1.26.3 and vulnerable x/crypto,
# x/net, x/text and grpc versions, and has not been re-released. Every
# replacement below is at or above the version the build resolves by itself.
FROM golang:1.26.8-alpine@sha256:8ac98ca534ac3f51e1f420a1dd2c15e74c75cfa0f23f3ad27eb5d7236c349a0c AS caddy-build
ARG CADDY_VERSION=v2.11.4
ARG XCADDY_VERSION=v0.4.7
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export SSL_CERT_FILE=/run/secrets/ca; fi; \
    go install github.com/caddyserver/xcaddy/cmd/xcaddy@${XCADDY_VERSION}
RUN --mount=type=secret,id=ca,required=false \
    if [ -f /run/secrets/ca ]; then export SSL_CERT_FILE=/run/secrets/ca; fi; \
    CGO_ENABLED=0 xcaddy build ${CADDY_VERSION} --output /usr/local/bin/caddy \
      --replace golang.org/x/crypto=golang.org/x/crypto@v0.57.0 \
      --replace golang.org/x/net=golang.org/x/net@v0.59.0 \
      --replace golang.org/x/text=golang.org/x/text@v0.42.0 \
      --replace google.golang.org/grpc=google.golang.org/grpc@v1.84.0 \
    && /usr/local/bin/caddy version

FROM caddy:2.11.4@sha256:0c994536bddb66445885237f1a5dcc1916bccea922661c76b4e9fc24061f9b52 AS web
# Our binary replaces the official one. It carries no file capability (the
# official one has cap_net_bind_service, which makes the kernel refuse to run
# it once all capabilities are dropped). Binding 80/443 as a non-root user
# still works: Docker sets net.ipv4.ip_unprivileged_port_start=0 in the
# container (docker-compose.yml states it too).
COPY --from=caddy-build /usr/local/bin/caddy /usr/bin/caddy
RUN addgroup -S -g 10002 caddy && adduser -S -D -H -u 10002 -G caddy caddy
COPY deploy/Caddyfile.docker /etc/caddy/Caddyfile
COPY website /srv/website
COPY --from=docs /site /srv/docs
USER caddy

FROM postgres:16@sha256:1a6ab3f5345eb6dbe04a1349529caabdb0ab09293a09590fad07b2246bfa4b54 AS db
# docker-compose.yml starts PostgreSQL as the postgres user, so the entrypoint
# never switches users and gosu is unused. It is removed because it is built
# with an old Go (unfixed upstream HIGH findings); starting this image as
# root now fails instead of running with it.
RUN rm /usr/local/bin/gosu
