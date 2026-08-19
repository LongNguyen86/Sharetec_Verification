FROM node:20-bookworm-slim

USER root

# Cài đặt Python 3, pip và Chromium trên hệ điều hành Debian chuẩn
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    chromium \
    && rm -rf /var/lib/apt/lists/*

# Cài đặt n8n chính chủ
RUN npm install -g n8n

COPY . /home/node/app

# Copy file requirements và cài đặt toàn bộ thư viện Python
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt --break-system-packages

# Cấu hình Playwright dùng Chromium hệ thống trong Docker
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
ENV PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH=/usr/bin/chromium

EXPOSE 5678

CMD ["sh", "-c", "n8n import:workflow --input=/home/node/app/workspace/n8n_workflows/BSDC_Workflow.json && n8n start"]