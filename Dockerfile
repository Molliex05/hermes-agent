FROM ghcr.io/astral-sh/uv:python3.13-trixie AS uv_source
FROM tianon/gosu:trixie AS gosu_source
FROM debian:13.4

# Disable Python stdout buffering to ensure logs are printed immediately
ENV PYTHONUNBUFFERED=1

# Store Playwright browsers outside the volume mount so the build-time
# install survives the /opt/data volume overlay at runtime.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/hermes/.playwright

# Install system dependencies in one layer, clear APT cache
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        git build-essential nodejs npm python3 ripgrep ffmpeg gcc python3-dev libffi-dev procps && \
    rm -rf /var/lib/apt/lists/*

# Non-root user for runtime; UID can be overridden via HERMES_UID at runtime
RUN useradd -u 10000 -m -d /opt/data hermes

COPY --chmod=0755 --from=gosu_source /gosu /usr/local/bin/
COPY --chmod=0755 --from=uv_source /usr/local/bin/uv /usr/local/bin/uvx /usr/local/bin/

COPY . /opt/hermes
WORKDIR /opt/hermes

# Install Node dependencies (Playwright omitted — install manually if browser tools needed)
RUN npm install --prefer-offline --no-audit && \
    cd /opt/hermes/scripts/whatsapp-bridge && \
    npm install --prefer-offline --no-audit && \
    npm cache clean --force

# Hand ownership to hermes user, then install Python deps in a virtualenv
RUN chown -R hermes:hermes /opt/hermes
USER hermes

RUN uv venv && \
    uv pip install --no-cache-dir -e ".[all]"

USER root
RUN chmod +x /opt/hermes/docker/entrypoint.sh

ENV HERMES_HOME=/opt/data
VOLUME [ "/opt/data" ]
ENTRYPOINT [ "/opt/hermes/docker/entrypoint.sh" ]
CMD ["gateway", "run"]
