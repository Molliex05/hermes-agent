FROM debian:13.4

# Disable Python stdout buffering to ensure logs are printed immediately
ENV PYTHONUNBUFFERED=1
ENV PATH="/opt/venv/bin:$PATH"

# Install system dependencies in one layer, clear APT cache
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        build-essential nodejs npm python3 python3-pip ripgrep ffmpeg gcc python3-dev libffi-dev && \
    rm -rf /var/lib/apt/lists/*

COPY . /opt/hermes
WORKDIR /opt/hermes

RUN pip install --no-cache-dir uv --break-system-packages
RUN uv venv /opt/venv
RUN uv pip install --no-cache -e ".[all]"
RUN npm install --prefer-offline --no-audit
RUN /opt/venv/bin/playwright install --with-deps chromium
RUN cd /opt/hermes/scripts/whatsapp-bridge && \
    npm install --prefer-offline --no-audit && \
    npm cache clean --force

WORKDIR /opt/hermes
RUN chmod +x /opt/hermes/docker/entrypoint.sh

ENV HERMES_HOME=/opt/data
VOLUME [ "/opt/data" ]
ENTRYPOINT [ "/opt/hermes/docker/entrypoint.sh" ]
CMD ["gateway", "run"]
