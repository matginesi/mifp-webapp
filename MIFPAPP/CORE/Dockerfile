ARG PYTHON_IMAGE=python:3.12-slim-bookworm@sha256:d50fb7611f86d04a3b0471b46d7557818d88983fc3136726336b2a4c657aa30b
FROM ${PYTHON_IMAGE} AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /build
RUN python -m venv /opt/venv
COPY requirements.lock ./requirements.lock
RUN pip install --requirement requirements.lock \
    && pip check \
    && find /opt/venv -type d -name "__pycache__" -prune -exec rm -rf {} + \
    && find /opt/venv -type f -name "*.py[co]" -delete

FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/.cache \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates fonts-dejavu-core libffi8 libgdk-pixbuf-2.0-0 libharfbuzz-subset0 \
       libjpeg62-turbo libopenjp2-7 libpango-1.0-0 libpangoft2-1.0-0 shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv

# Runtime contract: Flask application code and public content only.
COPY --chown=10001:10001 app.py wsgi.py gunicorn_conf.py docker-entrypoint.sh ./
COPY --chown=10001:10001 config ./config
COPY --chown=10001:10001 mifp_app ./mifp_app
COPY --chown=10001:10001 mifp_archive ./mifp_archive
COPY --chown=10001:10001 \
    About.md CodeOfConduct.md HowToBecomeASponsor.md Manifesto.md Privacy.md cookie-policy.md ./
RUN mkdir -p /app/data \
    && chown -R 10001:10001 /app/data

RUN chmod 0755 /app/docker-entrypoint.sh

USER 10001:10001
EXPOSE 8000
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["gunicorn", "--config", "python:gunicorn_conf", "wsgi:app"]
