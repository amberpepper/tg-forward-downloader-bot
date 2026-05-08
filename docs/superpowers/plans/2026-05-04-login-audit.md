# Login Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add independent Web admin login audit storage, query APIs, and a dedicated WebUI tab that shows successful and failed login attempts with IP and User-Agent metadata.

**Architecture:** Persist login attempts in a new SQLite `login_logs` table managed by `SQLiteStore`, record attempts directly inside the `/login` handler, expose a paginated `/api/login-logs` endpoint protected by the existing session cookie, and render the results in a dedicated “登录日志” tab inside the existing admin page. Use Python `unittest` plus FastAPI `TestClient` to cover failed login recording, successful login recording, API filtering, and the admin-page shell.

**Tech Stack:** Python 3, FastAPI, SQLite (`sqlite3`), Starlette `TestClient`, built-in `unittest`, vanilla HTML/CSS/JS.

---

### Task 1: Add Login Audit Persistence And Failed-Login Recording

**Files:**
- Create: `tests/test_login_audit.py`
- Modify: `app/store.py`
- Modify: `app/main.py`

- [ ] **Step 1: Write the failing test**

```python
import asyncio
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import DownloaderBot, create_web_app
from app.store import SQLiteStore


class LoginAuditStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.TemporaryDirectory()
        root = Path(self.tmpdir.name)
        self.settings = Settings(
            bot_token="test-token",
            download_root=root / "downloads",
            db_path=root / "app.db",
            default_webhook_url=None,
            default_hook_script=None,
            tdl_bin="tdl",
            yt_dlp_bin="yt-dlp",
            ffmpeg_bin="ffmpeg",
            tdl_cmd="tdl download {url} -d {output_dir}",
            url_downloader_cmd="yt-dlp -o {output_template} {url}",
            telegram_reply_on_finish=True,
            telegram_upload_back_on_finish=False,
            telegram_upload_back_max_mb=50,
            max_concurrent_jobs=1,
            require_allowlist=False,
            admin_user_ids=set(),
            admin_usernames=set(),
            initial_allowed_user_ids=set(),
            initial_allowed_usernames=set(),
            web_enabled=True,
            web_host="127.0.0.1",
            web_port=8090,
            web_admin_username="admin",
            web_admin_password="secret",
            web_secret_key="test-secret",
            web_session_hours=24,
        )
        self.store = SQLiteStore(self.settings.db_path, self.settings)
        asyncio.run(self.store.init())
        self.service = DownloaderBot(self.settings, self.store)
        self.client = TestClient(create_web_app(self.service))

    def tearDown(self) -> None:
        self.client.close()
        asyncio.run(self.store.close())
        self.tmpdir.cleanup()

    def test_failed_login_records_audit_metadata(self) -> None:
        response = self.client.post(
            "/login",
            data={"username": "admin", "password": "wrong-password"},
            headers={
                "x-forwarded-for": "203.0.113.7, 10.0.0.2",
                "user-agent": "AuditTest/1.0",
            },
        )

        self.assertEqual(response.status_code, 401)

        logs = asyncio.run(self.store.search_login_logs(page=1, page_size=20, q="", success="false"))
        self.assertEqual(logs["total"], 1)
        self.assertEqual(logs["items"][0]["username"], "admin")
        self.assertFalse(logs["items"][0]["success"])
        self.assertEqual(logs["items"][0]["failure_reason"], "invalid_credentials")
        self.assertEqual(logs["items"][0]["ip"], "203.0.113.7")
        self.assertEqual(logs["items"][0]["user_agent"], "AuditTest/1.0")
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
python -m unittest tests.test_login_audit.LoginAuditStoreTests.test_failed_login_records_audit_metadata -v
```

Expected: `AttributeError` or `sqlite3.OperationalError` because `search_login_logs` and the `login_logs` table do not exist yet.

- [ ] **Step 3: Write the minimal implementation**

In `app/store.py`, add schema creation plus read/write helpers:

```python
                CREATE TABLE IF NOT EXISTS login_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    username TEXT,
                    success INTEGER NOT NULL,
                    ip TEXT,
                    user_agent TEXT,
                    failure_reason TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_login_logs_created_at ON login_logs(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_login_logs_success_created_at ON login_logs(success, created_at DESC);
```

```python
    async def add_login_log(
        self,
        *,
        username: str | None,
        success: bool,
        ip: str | None,
        user_agent: str | None,
        failure_reason: str | None = None,
    ) -> None:
        async with self.lock:
            self.conn.execute(
                """
                INSERT INTO login_logs(created_at, username, success, ip, user_agent, failure_reason)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    now_utc(),
                    (username or "").strip() or None,
                    1 if success else 0,
                    (ip or "").strip() or None,
                    (user_agent or "").strip() or None,
                    (failure_reason or "").strip() or None,
                ),
            )
            self.conn.commit()
```

```python
    async def search_login_logs(
        self,
        page: int = 1,
        page_size: int = 20,
        q: str = "",
        success: str = "",
    ) -> dict[str, Any]:
        page = max(1, page)
        page_size = min(max(1, page_size), 100)
        clauses: list[str] = []
        params: list[Any] = []

        if success == "true":
            clauses.append("success = 1")
        elif success == "false":
            clauses.append("success = 0")

        q = q.strip()
        if q:
            like = f"%{q}%"
            clauses.append("(IFNULL(username, '') LIKE ? OR IFNULL(ip, '') LIKE ? OR IFNULL(user_agent, '') LIKE ?)")
            params.extend([like, like, like])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        async with self.lock:
            total = int(
                self.conn.execute(
                    f"SELECT COUNT(*) AS c FROM login_logs {where_sql}",
                    tuple(params),
                ).fetchone()["c"]
            )
            rows = self.conn.execute(
                f"SELECT * FROM login_logs {where_sql} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (*params, page_size, (page - 1) * page_size),
            ).fetchall()

        items = [
            {
                "id": row["id"],
                "created_at": row["created_at"],
                "username": row["username"],
                "success": bool(row["success"]),
                "ip": row["ip"],
                "user_agent": row["user_agent"],
                "failure_reason": row["failure_reason"],
            }
            for row in rows
        ]
        pages = max(1, (total + page_size - 1) // page_size)
        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": pages,
            "q": q,
            "success": success,
        }
```

In `app/main.py`, add request metadata extraction and failure logging:

```python
    def extract_client_ip(request: Request) -> str | None:
        forwarded_for = request.headers.get("x-forwarded-for", "")
        if forwarded_for:
            for raw_part in forwarded_for.split(","):
                ip = raw_part.strip()
                if ip:
                    return ip
        if request.client and request.client.host:
            return request.client.host
        return None

    async def safe_record_login_attempt(
        request: Request,
        *,
        username: str | None,
        success: bool,
        failure_reason: str | None = None,
    ) -> None:
        try:
            await service.store.add_login_log(
                username=username,
                success=success,
                ip=extract_client_ip(request),
                user_agent=request.headers.get("user-agent"),
                failure_reason=failure_reason,
            )
        except Exception:
            logger.exception("failed to write login audit log")
```

Update the login handler signature and failure branch:

```python
    @app.post("/login")
    async def login_submit(request: Request, username: str = Form(...), password: str = Form(...)) -> Response:
        if username != service.settings.web_admin_username or password != service.settings.web_admin_password:
            await safe_record_login_attempt(
                request,
                username=username,
                success=False,
                failure_reason="invalid_credentials",
            )
            return HTMLResponse(render_login_page("用户名或密码错误"), status_code=401)
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
python -m unittest tests.test_login_audit.LoginAuditStoreTests.test_failed_login_records_audit_metadata -v
```

Expected: `OK`

- [ ] **Step 5: Commit**

If `.git` is available in the workspace:

```powershell
git add app/store.py app/main.py tests/test_login_audit.py
git commit -m "feat: record failed web login audit logs"
```

### Task 2: Add Successful Login Recording And Login-Log API

**Files:**
- Modify: `tests/test_login_audit.py`
- Modify: `app/main.py`
- Modify: `app/store.py`

- [ ] **Step 1: Write the failing tests**

Append these tests to `tests/test_login_audit.py`:

```python
    def test_successful_login_records_audit_and_sets_cookie(self) -> None:
        response = self.client.post(
            "/login",
            data={"username": "admin", "password": "secret"},
            headers={"user-agent": "AuditSuccess/1.0"},
            follow_redirects=False,
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], "/admin")
        self.assertIn("tgfd_session=", response.headers.get("set-cookie", ""))

        logs = asyncio.run(self.store.search_login_logs(page=1, page_size=20, q="AuditSuccess", success="true"))
        self.assertEqual(logs["total"], 1)
        self.assertTrue(logs["items"][0]["success"])
        self.assertEqual(logs["items"][0]["failure_reason"], None)

    def test_login_logs_api_supports_search_and_success_filter(self) -> None:
        asyncio.run(
            self.store.add_login_log(
                username="admin",
                success=True,
                ip="198.51.100.20",
                user_agent="Browser/5.0",
                failure_reason=None,
            )
        )
        asyncio.run(
            self.store.add_login_log(
                username="intruder",
                success=False,
                ip="198.51.100.99",
                user_agent="Scanner/1.0",
                failure_reason="invalid_credentials",
            )
        )

        login_response = self.client.post(
            "/login",
            data={"username": "admin", "password": "secret"},
            follow_redirects=False,
        )
        cookie = login_response.cookies.get("tgfd_session")

        response = self.client.get(
            "/api/login-logs?page=1&page_size=20&q=198.51.100&success=false",
            cookies={"tgfd_session": cookie},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["username"], "intruder")
        self.assertFalse(payload["items"][0]["success"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m unittest tests.test_login_audit.LoginAuditStoreTests.test_successful_login_records_audit_and_sets_cookie tests.test_login_audit.LoginAuditStoreTests.test_login_logs_api_supports_search_and_success_filter -v
```

Expected: failures because successful logins are not recorded yet and `/api/login-logs` does not exist.

- [ ] **Step 3: Write the minimal implementation**

In `app/main.py`, add the success write before returning the redirect:

```python
        await safe_record_login_attempt(
            request,
            username=username,
            success=True,
            failure_reason=None,
        )
        redirect = RedirectResponse(url="/admin", status_code=302)
```

Add the protected API route:

```python
    @app.get("/api/login-logs")
    async def api_login_logs(
        request: Request,
        page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100),
        q: str = Query(default=""),
        success: str = Query(default=""),
    ) -> dict[str, Any]:
        require_api_login(request)
        if success not in {"", "true", "false"}:
            raise HTTPException(status_code=400, detail="success must be '', 'true', or 'false'")
        return await service.store.search_login_logs(page=page, page_size=page_size, q=q, success=success)
```

If needed, keep `search_login_logs` returning `success` unchanged so the UI can round-trip the active filter value.

- [ ] **Step 4: Run tests to verify they pass**

Run:

```powershell
python -m unittest tests.test_login_audit.LoginAuditStoreTests.test_successful_login_records_audit_and_sets_cookie tests.test_login_audit.LoginAuditStoreTests.test_login_logs_api_supports_search_and_success_filter -v
```

Expected: `OK`

- [ ] **Step 5: Commit**

If `.git` is available in the workspace:

```powershell
git add app/main.py app/store.py tests/test_login_audit.py
git commit -m "feat: expose login audit api"
```

### Task 3: Add The Admin Login-Logs Tab And Shell Test

**Files:**
- Modify: `tests/test_login_audit.py`
- Modify: `app/templates/index.html`

- [ ] **Step 1: Write the failing test**

Append this test to `tests/test_login_audit.py`:

```python
    def test_admin_page_contains_login_logs_tab_and_fetch_hook(self) -> None:
        login_response = self.client.post(
            "/login",
            data={"username": "admin", "password": "secret"},
            follow_redirects=False,
        )
        cookie = login_response.cookies.get("tgfd_session")

        response = self.client.get("/admin", cookies={"tgfd_session": cookie})

        self.assertEqual(response.status_code, 200)
        html = response.text
        self.assertIn("登录日志", html)
        self.assertIn("id=\"login_log_search\"", html)
        self.assertIn("data-tab-target=\"login-logs\"", html)
        self.assertIn("/api/login-logs", html)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```powershell
python -m unittest tests.test_login_audit.LoginAuditStoreTests.test_admin_page_contains_login_logs_tab_and_fetch_hook -v
```

Expected: assertion failure because the admin template does not yet contain the new tab and fetch path.

- [ ] **Step 3: Write the minimal implementation**

In `app/templates/index.html`, add a dedicated nav item and panel near the existing “事件日志” and “访问控制” sections:

```html
<div class="nav-item" data-tab-target="login-logs">
  <svg class="svg-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
    <path d="M10 17l5-5-5-5"></path>
    <path d="M15 12H3"></path>
    <path d="M21 19V5"></path>
  </svg>
  <span class="nav-label">登录日志</span>
</div>
```

```html
<section class="tab-panel" data-tab="login-logs">
  <div class="section-head">
    <div>
      <h2>登录日志</h2>
      <span class="badge">审计</span>
    </div>
  </div>
  <div class="filters">
    <input id="login_log_search" placeholder="搜索用户名 / IP / User-Agent" />
    <select id="login_log_success">
      <option value="">全部</option>
      <option value="true">成功</option>
      <option value="false">失败</option>
    </select>
    <select id="login_log_page_size">
      <option value="20">20 / 页</option>
      <option value="50">50 / 页</option>
      <option value="100">100 / 页</option>
    </select>
    <button class="btn-primary" onclick="reloadLoginLogs(1)">搜索日志</button>
  </div>
  <div class="empty-state" id="loginLogsEmpty">当前没有匹配的登录日志</div>
  <div class="table-wrap"><table id="loginLogsTable" class="events-table"></table></div>
  <div class="pager">
    <button onclick="prevLoginLogPage()">上一页</button>
    <button onclick="nextLoginLogPage()">下一页</button>
    <span id="loginLogsMeta" class="badge"></span>
  </div>
</section>
```

Add state, renderer, and fetch logic in the existing script:

```javascript
let loginLogPage = 1;
let loginLogPages = 1;
let loginLogsReloadInFlight = false;

function renderLoginLogs(data) {
  loginLogPage = data.page || 1;
  loginLogPages = data.pages || 1;
  const items = data.items || [];
  const rows = ['<tr><th>时间</th><th>结果</th><th>用户名</th><th>IP</th><th>User-Agent</th><th>失败原因</th></tr>'];
  items.forEach((item) => {
    const resultClass = item.success ? 'status-success' : 'status-error';
    const resultText = item.success ? '成功' : '失败';
    rows.push(
      `<tr><td>${escapeHtml(formatDateTime(item.created_at) || '')}</td><td><span class="badge ${resultClass}">${resultText}</span></td><td>${escapeHtml(item.username || '')}</td><td>${escapeHtml(item.ip || '')}</td><td><div class="ellipsis" title="${escapeHtml(item.user_agent || '')}">${escapeHtml(item.user_agent || '')}</div></td><td>${escapeHtml(item.failure_reason || '')}</td></tr>`
    );
  });
  document.getElementById('loginLogsTable').innerHTML = rows.join('');
  setEmptyState('loginLogsEmpty', items.length === 0);
  setText('loginLogsMeta', `第 ${loginLogPage} / ${loginLogPages} 页，共 ${data.total || 0} 条`);
}

async function reloadLoginLogs(page, options = {}) {
  if (loginLogsReloadInFlight) return;
  loginLogsReloadInFlight = true;
  try {
    if (page) loginLogPage = page;
    const q = encodeURIComponent(document.getElementById('login_log_search').value || '');
    const success = encodeURIComponent(document.getElementById('login_log_success').value || '');
    const pageSize = encodeURIComponent(document.getElementById('login_log_page_size').value || '20');
    const res = await fetch(`/api/login-logs?page=${loginLogPage}&page_size=${pageSize}&q=${q}&success=${success}`, { credentials: 'same-origin' });
    if (res.status === 401) { window.location.href = '/login'; return; }
    if (!res.ok) throw new Error('load login logs failed');
    renderLoginLogs(await res.json());
  } finally {
    loginLogsReloadInFlight = false;
  }
}

function prevLoginLogPage() {
  if (loginLogPage > 1) reloadLoginLogs(loginLogPage - 1);
}

function nextLoginLogPage() {
  if (loginLogPage < loginLogPages) reloadLoginLogs(loginLogPage + 1);
}
```

Hook the reload into the existing bootstrap/refresh flow:

```javascript
reloadLoginLogs(1);
setInterval(() => reloadLoginLogs(loginLogPage, { background: true }), 5000);
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```powershell
python -m unittest tests.test_login_audit.LoginAuditStoreTests.test_admin_page_contains_login_logs_tab_and_fetch_hook -v
```

Expected: `OK`

- [ ] **Step 5: Run the full login-audit test file**

Run:

```powershell
python -m unittest tests.test_login_audit -v
```

Expected: all login-audit tests pass.

- [ ] **Step 6: Commit**

If `.git` is available in the workspace:

```powershell
git add app/templates/index.html tests/test_login_audit.py
git commit -m "feat: add login audit web ui"
```

## Self-Review

- Spec coverage:
  - `login_logs` table: Task 1
  - success/failure audit writes: Tasks 1-2
  - IP and User-Agent capture: Task 1
  - paginated query API with `q` and `success`: Task 2
  - dedicated WebUI tab: Task 3
  - tests for storage, API, and page shell: Tasks 1-3
- Placeholder scan:
  - No `TODO`, `TBD`, or “similar to Task N” placeholders remain.
- Type consistency:
  - Store method names are consistently `add_login_log` and `search_login_logs`.
  - API filter values stay `"" | "true" | "false"` across store, API, and UI.
