# Vendored copy of django-hub/mcp_server (source of truth: django-hub repo).
# Lives here so the sidecar image can build with Chatbot's existing AWS/ECR
# credentials, avoiding a cross-repo checkout token for a private repo.
# If django-hub/mcp_server/server.py or its function taxonomy changes,
# re-sync this copy by hand until a proper cross-repo sync mechanism exists.
FROM python:3.12-alpine3.22 AS builder

RUN apk update && apk upgrade --no-cache && rm -rf /var/cache/apk/*
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

RUN apk add --no-cache ca-certificates
COPY mcp_server/requirements.txt /tmp/requirements.txt

RUN python -m venv /opt/venv; \
    /opt/venv/bin/python -m pip install --upgrade pip setuptools wheel; \
    /opt/venv/bin/python -m pip install -r /tmp/requirements.txt; \
    /opt/venv/bin/python -m pip check; \
    /opt/venv/bin/python -c "from mcp.server.fastmcp import FastMCP"

FROM python:3.12-alpine3.22 AS runtime

RUN apk update && apk upgrade --no-cache && rm -rf /var/cache/apk/*
WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH="/app" \
    HOME="/home/app"

RUN apk add --no-cache ca-certificates; \
    rm -rf /usr/local/lib/python3.12/site-packages/pip* \
        /usr/local/lib/python3.12/site-packages/setuptools* \
        /usr/local/lib/python3.12/site-packages/wheel* \
        /usr/local/lib/python3.12/ensurepip \
        /root/.cache /var/cache/apk/* /tmp/* /var/tmp/*; \
    addgroup -S -g 10001 app; \
    adduser -S -D -H -u 10001 -G app app; \
    mkdir -p /home/app; \
    chown -R app:app /home/app

COPY --from=builder /opt/venv /opt/venv
USER root
RUN rm -rf /opt/venv/lib/python3.12/site-packages/pip* \
    /opt/venv/lib/python3.12/site-packages/setuptools* \
    /opt/venv/lib/python3.12/site-packages/wheel* \
    /opt/venv/bin/pip /opt/venv/bin/pip3 /opt/venv/bin/pip3.12 \
    /root/.cache /home/app/.cache /var/cache/apk/* /tmp/* /var/tmp/*

COPY --chown=app:app mcp_server /app/mcp_server
USER app

EXPOSE 8001
CMD ["uvicorn", "mcp_server.server:app", "--host", "0.0.0.0", "--port", "8001"]
