FROM node:20-bookworm-slim

USER root

# Install Python 3, pip, Chromium, and build essential packages
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    chromium \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install n8n globally
RUN npm install -g n8n

# Set working directory inside container
WORKDIR /home/node/app

# Copy project files into container
COPY . /home/node/app

# Install Python dependencies natively using pyproject.toml
RUN pip3 install --no-cache-dir . --break-system-packages

# Configure Playwright to use system-installed Chromium
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
ENV PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium

EXPOSE 5678

# Import workflow and start n8n server on container launch
CMD ["sh", "-c", "n8n import:workflow --input=/home/node/app/workspace/n8n_workflows/BSDC_Workflow.json && n8n start"]