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

# Install Playwright Chromium Browser and Dependencies
RUN python3 -m playwright install chromium --with-deps
RUN apt-get update && apt-get install -y xvfb
EXPOSE 5678 8000

# Import workflow and start n8n server on container launch

ENV DISPLAY=:99

CMD ["sh", "-c", "Xvfb :99 -ac -screen 0 1024x768x24 & sleep 1 && python3 -m uvicorn src.bsdc_engine.api.app:app --host 0.0.0.0 --port 8000 & if [ ! -f /home/node/.n8n/.imported ]; then n8n import:workflow --separate --input=/home/node/app/n8n_workflows/ && touch /home/node/.n8n/.imported; fi && n8n start"]