# tg-forward-downloader-bot

## 1) 本地使用

### 前置
- Linux / macOS / WSL
- 已安装 `bash`
- 准备好 Telegram Bot Token

### 步骤
```bash
cd tg-forward-downloader-bot
cp .env.example .env

bash scripts/install.sh
bash scripts/start.sh
```

`install.sh` 会把 `tdl / yt-dlp / ffmpeg` 二进制下载到项目 `./bin`（可用 `INSTALL_BIN_DIR` 覆盖）。

### 访问
- 管理后台：`http://127.0.0.1:8090`
- 用 `.env` 中的 `WEB_ADMIN_USERNAME / WEB_ADMIN_PASSWORD` 登录

> 端口可通过 `.env` 的 `WEB_PORT` 修改。

---

## 2) Docker Compose 启动

### 步骤
```bash
cd tg-forward-downloader-bot
cp .env.example .env
mkdir -p bin

# 把二进制放到 ./bin，并确保有执行权限
# 需要这 3 个文件名：tdl / yt-dlp / ffmpeg
# chmod +x ./bin/tdl ./bin/yt-dlp ./bin/ffmpeg

docker compose up -d --build
```

### 查看日志
```bash
docker compose logs -f
```

### 停止
```bash
docker compose down
```

### 访问
- 管理后台：`http://127.0.0.1:8090`

### 数据持久化目录
- `./bin`：tdl / yt-dlp / ffmpeg 二进制（挂载到容器 `/app/bin`）
- `./downloads`：下载文件
- `./data`：数据库
- `./tdl-data`：tdl 会话

---

## 3) 直接用镜像运行（docker run）

```bash
docker pull ghcr.io/amberpepper/tg-forward-downloader-bot:sha-853e9a5

mkdir -p bin downloads data tdl-data
# bin 里需要放：tdl / yt-dlp / ffmpeg，并确保可执行
# chmod +x ./bin/tdl ./bin/yt-dlp ./bin/ffmpeg

docker run -d \
  --name tg-forward-downloader-bot \
  --restart unless-stopped \
  --env-file ./.env \
  -p 8090:8090 \
  -v "$(pwd)/bin:/app/bin" \
  -v "$(pwd)/downloads:/app/downloads" \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/tdl-data:/root/.tdl" \
  ghcr.io/amberpepper/tg-forward-downloader-bot:sha-853e9a5
```

---

## 4) tdl 登录

### 本地运行时
```bash
tdl login -T code
```

### Docker Compose 运行时
```bash
docker compose exec tg-forward-downloader-bot tdl login -T code
```

登录成功后，`tdl` 会话会持久化在 `./tdl-data`。
