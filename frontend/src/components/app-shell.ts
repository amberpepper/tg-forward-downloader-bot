import { LitElement, html, css, nothing } from "lit";
import { customElement, query, state } from "lit/decorators.js";
import { dialog, tokens } from "lituix";
import type { AppState } from "../lib/types";
import { setNotifyTarget } from "../lib/notify";
import "../pages/tg-login";
import "../pages/tg-overview";
import "../pages/tg-jobs";
import "../pages/tg-events";
import "../pages/tg-login-logs";
import "../pages/tg-access";

type RouteName = "login" | "overview" | "jobs" | "events" | "login-logs" | "access";

const ROUTE_MAP: Record<string, RouteName> = {
  "/admin": "overview",
  "/admin/jobs": "jobs",
  "/admin/events": "events",
  "/admin/login-logs": "login-logs",
  "/admin/access": "access",
  "/login": "login",
};

@customElement("app-shell")
export class AppShell extends LitElement {
  @query("lui-config-provider") private _configProvider?: HTMLElement;
  @state() private _route: RouteName = "overview";
  @state() private _state: AppState | null = null;
  @state() private _sidebarCollapsed = false;
  @state() private _theme: "dark" | "light" = "dark";
  private _stateTimer: ReturnType<typeof setInterval> | null = null;

  static styles = [
    tokens,
    css`
      :host {
        display: block;
        height: 100vh;
        --c-bg: var(--lui-color-bg);
        --c-bg-subtle: var(--lui-color-bg-subtle);
        --c-bg-hover: var(--lui-color-bg-hover);
        --c-border: var(--lui-color-border);
        --c-fg: var(--lui-color-fg);
        --c-fg-muted: var(--lui-color-fg-muted);
        --c-primary: var(--lui-color-primary);
        --c-success: var(--lui-color-success);
        --c-danger: var(--lui-color-danger);
        --c-warning: var(--lui-color-warning);
      }
      *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
      body { margin: 0; }
    .layout {
      display: flex;
      height: 100vh;
      overflow: hidden;
      background: var(--c-bg);
      color: var(--c-fg);
      font-family: var(--lui-font, "Segoe UI", Inter, sans-serif);
      font-size: 13px;
    }
    .sidebar {
      width: 230px;
      background: var(--c-bg-subtle);
      border-right: 1px solid var(--c-border);
      display: flex;
      flex-direction: column;
      transition: width .22s ease;
      overflow: hidden;
      flex-shrink: 0;
    }
    .sidebar.collapsed { width: 60px; }
    .brand {
      height: 56px;
      padding: 0 16px;
      display: flex;
      align-items: center;
      gap: 10px;
      border-bottom: 1px solid var(--c-border);
      font-weight: 700;
      font-size: 15px;
      white-space: nowrap;
    }
    .brand-icon {
      width: 30px; height: 30px;
      border-radius: 10px;
      display: flex; align-items: center; justify-content: center;
      background: linear-gradient(135deg, rgba(0,122,204,0.18), rgba(0,122,204,0.08));
      flex-shrink: 0;
    }
    .brand-text { overflow: hidden; text-overflow: ellipsis; transition: opacity .18s; }
    .sidebar.collapsed .brand-text { opacity: 0; width: 0; }
    .sidebar.collapsed .brand { justify-content: center; padding: 0; }
    .nav { padding: 12px 0; flex: 1; }
    .nav-item lui-icon { --lui-icon-size: 16px; }
    .brand-icon lui-icon { --lui-icon-size: 16px; }
    .toggle-btn lui-icon { --lui-icon-size: 16px; }
    .nav-item {
      display: flex; align-items: center; gap: 10px;
      padding: 10px 18px;
      color: var(--c-fg-muted);
      text-decoration: none;
      cursor: pointer;
      transition: all .15s;
      border-right: 3px solid transparent;
      white-space: nowrap;
    }
    .nav-item:hover { background: var(--c-bg-hover); color: var(--c-fg); }
    .nav-item.active {
      background: rgba(0,122,204,0.15);
      color: var(--c-primary);
      border-right-color: var(--c-primary);
    }
    .sidebar.collapsed .nav-item { justify-content: center; padding: 12px 0; gap: 0; }
    .sidebar.collapsed .nav-item span { display: none; }
    .main { flex: 1; display: flex; flex-direction: column; min-width: 0; overflow: hidden; }
    .header {
      height: 56px;
      border-bottom: 1px solid var(--c-border);
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 0 20px;
      gap: 12px;
      flex-shrink: 0;
    }
    .header-left { display: flex; align-items: center; gap: 12px; }
    .status-indicator {
      display: inline-flex; align-items: center; gap: 8px;
      padding: 5px 12px; background: var(--c-bg-subtle);
      border-radius: 999px; border: 1px solid var(--c-border);
      font-size: 12px; color: var(--c-fg-muted);
    }
    .status-dot {
      width: 8px; height: 8px; border-radius: 50%;
      background: var(--c-success);
      box-shadow: 0 0 8px var(--c-success);
    }
    .header-actions { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .header-actions lui-button { --lui-button-gap: 6px; --lui-button-icon-size: 14px; }
    .content { flex: 1; overflow-y: auto; padding: 20px; }
    .toggle-btn {
      background: none; border: 1px solid var(--c-border);
      color: var(--c-fg-muted); border-radius: 8px;
      width: 32px; height: 32px; cursor: pointer;
      display: inline-flex; align-items: center; justify-content: center;
      transition: all .15s;
    }
    .toggle-btn:hover { background: var(--c-bg-hover); color: var(--c-fg); }
    @media (max-width: 768px) {
      .layout { flex-direction: column; }
      .sidebar { width: 100%; border-right: none; border-bottom: 1px solid var(--c-border); }
      .sidebar.collapsed { width: 100%; height: auto; }
      .nav { display: flex; overflow-x: auto; padding: 0; }
      .nav-item { padding: 8px 12px; border-right: none; border-bottom: 2px solid transparent; }
      .nav-item.active { border-bottom-color: var(--c-primary); border-right: none; }
    }
    `,
  ];

  connectedCallback() {
    super.connectedCallback();
    this._parseRoute();
    window.addEventListener("popstate", this._onPopState);
    if (this._route !== "login") {
      this._loadState();
      this._stateTimer = setInterval(() => this._loadState(), 5000);
    }
    const saved = localStorage.getItem("tgfd-sidebar-collapsed");
    if (saved === "1") this._sidebarCollapsed = true;
    const savedTheme = localStorage.getItem("tgfd-theme");
    if (savedTheme === "light" || savedTheme === "dark") this._theme = savedTheme;
    this._applyTheme();
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    window.removeEventListener("popstate", this._onPopState);
    if (this._stateTimer) clearInterval(this._stateTimer);
    setNotifyTarget(null);
  }

  firstUpdated() {
    this._syncNotifyTarget();
  }

  updated() {
    this._syncNotifyTarget();
  }

  private _onPopState = () => {
    this._parseRoute();
  };

  private _parseRoute() {
    const path = window.location.pathname;
    this._route = ROUTE_MAP[path] || "overview";
  }

  private _navigate(path: string) {
    window.history.pushState({}, "", path);
    this._parseRoute();
    this.requestUpdate();
  }

  private async _loadState() {
    try {
      const res = await fetch("/api/state", { credentials: "same-origin" });
      if (res.status === 401) {
        window.location.href = "/login";
        return;
      }
      this._state = await res.json();
    } catch {}
  }

  private _toggleSidebar() {
    this._sidebarCollapsed = !this._sidebarCollapsed;
    localStorage.setItem("tgfd-sidebar-collapsed", this._sidebarCollapsed ? "1" : "0");
  }

  private _toggleTheme() {
    this._theme = this._theme === "dark" ? "light" : "dark";
    localStorage.setItem("tgfd-theme", this._theme);
    this._applyTheme();
  }

  private _applyTheme() {
    this.setAttribute("theme", this._theme);
    this.style.colorScheme = this._theme;
  }

  private _syncNotifyTarget() {
    setNotifyTarget(this._configProvider ?? null);
  }

  private async _logout() {
    const confirmed = await dialog.confirm({
      title: "退出登录",
      message: "确认退出当前后台登录？",
      confirmText: "确认退出",
      cancelText: "取消",
      danger: true,
      width: 420,
      container: this._configProvider,
    });
    if (!confirmed) return;

    const form = document.createElement("form");
    form.method = "POST";
    form.action = "/logout";
    document.body.appendChild(form);
    form.submit();
  }

  render() {
    if (this._route === "login") {
      return html`<tg-login></tg-login>`;
    }

    return html`
      <lui-config-provider .theme=${this._theme}>
        <div class="layout">
          <aside class="sidebar ${this._sidebarCollapsed ? "collapsed" : ""}">
            <div class="brand">
              <div class="brand-icon">
                <lui-icon name="download"></lui-icon>
              </div>
              <span class="brand-text">TG Downloader</span>
            </div>
            <nav class="nav">
              <a class="nav-item ${this._route === "overview" ? "active" : ""}"
                 @click=${(e: Event) => { e.preventDefault(); this._navigate("/admin"); }}>
                <lui-icon name="bar-chart-2"></lui-icon>
                <span>概览</span>
              </a>
              <a class="nav-item ${this._route === "jobs" ? "active" : ""}"
                 @click=${(e: Event) => { e.preventDefault(); this._navigate("/admin/jobs"); }}>
                <lui-icon name="download"></lui-icon>
                <span>下载历史</span>
              </a>
              <a class="nav-item ${this._route === "events" ? "active" : ""}"
                 @click=${(e: Event) => { e.preventDefault(); this._navigate("/admin/events"); }}>
                <lui-icon name="list"></lui-icon>
                <span>事件日志</span>
              </a>
              <a class="nav-item ${this._route === "login-logs" ? "active" : ""}"
                 @click=${(e: Event) => { e.preventDefault(); this._navigate("/admin/login-logs"); }}>
                <lui-icon name="log-in"></lui-icon>
                <span>登录日志</span>
              </a>
              <a class="nav-item ${this._route === "access" ? "active" : ""}"
                 @click=${(e: Event) => { e.preventDefault(); this._navigate("/admin/access"); }}>
                <lui-icon name="user-plus"></lui-icon>
                <span>白名单</span>
              </a>
            </nav>
          </aside>
          <main class="main">
            <header class="header">
              <div class="header-left">
                <button class="toggle-btn" @click=${this._toggleSidebar} title="折叠侧边栏">
                  <lui-icon name="panel-left"></lui-icon>
                </button>
                <div class="status-indicator"><span class="status-dot"></span> 服务运行中</div>
              </div>
              <div class="header-actions">
                <lui-tag size="sm">队列 ${this._state?.queue?.in_memory_size ?? 0}</lui-tag>
                <lui-tag size="sm" variant="success">成功 ${this._state?.job_counts?.success ?? 0}</lui-tag>
                <lui-tag size="sm" variant="danger">失败 ${this._state?.job_counts?.failed ?? 0}</lui-tag>
                <lui-button size="xs" variant="primary" @click=${() => this._loadState()} icon="refresh-cw">刷新</lui-button>
                <lui-button size="xs" @click=${this._toggleTheme} icon=${this._theme === "dark" ? "sun" : "moon"}>
                  ${this._theme === "dark" ? "浅色" : "深色"}
                </lui-button>
                <lui-button size="xs" @click=${this._logout} icon="log-out">退出登录</lui-button>
              </div>
            </header>
            <div class="content">
              ${this._route === "overview" ? html`<tg-overview .appState=${this._state} @refresh=${() => this._loadState()}></tg-overview>` : nothing}
              ${this._route === "jobs" ? html`<tg-jobs></tg-jobs>` : nothing}
              ${this._route === "events" ? html`<tg-events></tg-events>` : nothing}
              ${this._route === "login-logs" ? html`<tg-login-logs></tg-login-logs>` : nothing}
              ${this._route === "access" ? html`<tg-access .appState=${this._state} @refresh=${() => this._loadState()}></tg-access>` : nothing}
            </div>
          </main>
        </div>
      </lui-config-provider>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "app-shell": AppShell;
  }
}
