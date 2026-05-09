FROM node:20-slim AS frontend-build

WORKDIR /app

COPY frontend/package.json frontend/package-lock.json ./frontend/
RUN cd frontend && npm ci

COPY frontend ./frontend
COPY app ./app

RUN cd frontend && npm run build


FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive \
    INSTALL_PYTHON_DEPS=0 \
    INSTALL_TDL=1 \
    INSTALL_YTDLP=1 \
    INSTALL_FFMPEG=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    wget \
    tar \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts ./scripts
COPY .env.example ./
RUN chmod +x ./scripts/install.sh ./scripts/update.sh \
    && bash ./scripts/install.sh

COPY app ./app
COPY --from=frontend-build /app/app/static/frontend ./app/static/frontend
COPY README.md ./
COPY docs ./docs

RUN mkdir -p /app/downloads /app/data /root/.tdl

VOLUME ["/app/downloads", "/app/data", "/root/.tdl"]

CMD ["python", "app/main.py"]
