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
- `./downloads`：下载文件
- `./data`：数据库
- `./tdl-data`：tdl 会话

---

## 3) tdl 登录

### 本地运行时
```bash
tdl login -T code
```

### Docker Compose 运行时
```bash
docker compose exec tg-forward-downloader-bot tdl login -T code
```

登录成功后，`tdl` 会话会持久化在 `./tdl-data`。
