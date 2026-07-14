FROM node:22-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    EMPYRALIS_AGENT_COMPUTER_SYSTEM_SERVICE_MODE=1 \
    EMPYRALIS_AGENT_COMPUTER_SERVICE_TARGET=server_vps

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    ca-certificates \
    curl \
    build-essential \
    python3 \
    && rm -rf /var/lib/apt/lists/*

COPY . .

RUN cd empyralis-gateway \
    && npm install \
    && npm run build

VOLUME ["/app/.orion-stack/agent-computer", "/app/.orion-stack/logs"]

CMD ["bash", "scripts/agent_computer.sh", "service-run", "--system"]
