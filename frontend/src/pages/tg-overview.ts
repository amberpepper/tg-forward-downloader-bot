import { tokens } from "lituix";
import { LitElement, html, css } from "lit";
import { customElement, property, state } from "lit/decorators.js";
import type { AppState, StatsSummary, HookConfig, ToolsStatusResponse, ToolInfo } from "../lib/types";
import { api } from "../lib/api";
import { escapeHtml, formatDateTime } from "../lib/utils";
import { notifyError, notifySuccess } from "../lib/notify";

@customElement("tg-overview")
export class TgOverview extends LitElement {
  @property({ attribute: false }) appState: AppState | null = null;
  @state() private _stats: StatsSummary | null = null;
  @state() private _hook: HookConfig | null = null;
  @state() private _tools: ToolsStatusResponse | null = null;
  @state() private _webhookUrl = "";
  @state() private _hookScript = "";
  @state() private _replyOnFinish = true;
  @state() private _requireAllowlist = true;
  @state() private _maxConcurrentJobs = 1;
  @state() private _toolActionInFlight = false;

  static styles = [tokens, css`
    :host { display: block; }
    .page { display: grid; gap: 16px; }
    .hero-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; }
    .main-grid { display: grid; grid-template-columns: minmax(0, 1.45fr) minmax(360px, 0.75fr); gap: 16px; align-items: start; }
    .stack { display: grid; gap: 16px; }
    .two-col { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
    .status-list { display: grid; gap: 12px; }
    .metric-row { display: grid; grid-template-columns: minmax(90px, 0.8fr) minmax(0, 1.4fr) auto; gap: 12px; align-items: center; }
    .metric-title { min-width: 0; }
    .metric-value { color: var(--lui-color-fg-muted); font-family: var(--lui-font-mono, monospace); font-size: 12px; }
    .summary-strip { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; }
    .summary-item { border: 1px solid var(--lui-color-border); border-radius: 12px; padding: 12px; background: var(--lui-color-bg-hover); }
    .summary-label { color: var(--lui-color-fg-muted); font-size: 12px; margin-bottom: 6px; }
    .summary-value { font-size: 20px; font-weight: 700; font-variant-numeric: tabular-nums; }
    .settings-section { display: grid; gap: 12px; }
    .section-title { display: flex; align-items: center; gap: 8px; font-size: 13px; font-weight: 700; }
    .form-stack { display: grid; gap: 10px; }
    .compact-number { width: 120px; max-width: 100%; }
    .help-text { font-size: 12px; color: var(--lui-color-fg-muted); line-height: 1.5; }
    .tool-status-list { display: grid; gap: 10px; }
    .tool-row {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      align-items: center;
      padding: 12px;
      border: 1px solid var(--lui-color-border);
      border-radius: 12px;
      background: var(--lui-color-bg-hover);
    }
    .tool-main { min-width: 0; display: grid; gap: 6px; }
    .tool-title { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; min-width: 0; }
    .tool-name { font-weight: 700; }
    .tool-meta {
      min-width: 0;
      display: grid;
      gap: 4px;
      color: var(--lui-color-fg-muted);
      font-size: 12px;
      line-height: 1.45;
    }
    .tool-meta code {
      font-family: var(--lui-font-mono, monospace);
      word-break: break-all;
    }
    .tool-empty {
      color: var(--lui-color-fg-muted);
      font-size: 12px;
    }
    .updated-at {
      margin-top: 10px;
      color: var(--lui-color-fg-muted);
      font-size: 12px;
    }
    lui-button, lui-button-group-item { --lui-icon-size: 14px; --lui-button-icon-size: 14px; }
    @media (max-width: 1180px) {
      .hero-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .main-grid { grid-template-columns: 1fr; }
    }
    @media (max-width: 720px) {
      .hero-grid, .two-col, .summary-strip { grid-template-columns: 1fr; }
      .metric-row { grid-template-columns: 1fr; gap: 8px; }
      .tool-row { grid-template-columns: 1fr; }
    }
    `,
  ];

  connectedCallback() {
    super.connectedCallback();
    this._load();
  }

  private async _load() {
    try {
      const [tools, stats, hook] = await Promise.all([
        api.toolsStatus(),
        api.stats(),
        api.hookConfig(),
      ]);
      this._tools = tools;
      this._stats = stats;
      this._hook = hook;
      this._webhookUrl = hook.default_webhook_url || "";
      this._hookScript = hook.default_hook_script || "";
      this._replyOnFinish = hook.telegram_reply_on_finish;
      this._requireAllowlist = hook.require_allowlist;
      this._maxConcurrentJobs = hook.max_concurrent_jobs;
    } catch {}
  }

  private async _loadToolsStatus() {
    try {
      this._tools = await api.toolsStatus();
    } catch (e) {
      notifyError(e, "加载工具状态失败");
    }
  }

  private async _saveHook() {
    try {
      await api.saveHookConfig({
        webhook_url: this._webhookUrl,
        hook_script: this._hookScript,
        telegram_reply_on_finish: this._replyOnFinish,
        require_allowlist: this._requireAllowlist,
        max_concurrent_jobs: this._maxConcurrentJobs,
      });
      this.dispatchEvent(new CustomEvent("refresh"));
      notifySuccess("设置已保存");
    } catch (e) {
      notifyError(e, "保存设置失败");
    }
  }

  private async _testHook() {
    try {
      await api.testHook();
      notifySuccess("Hook 测试已触发");
    } catch (e) {
      notifyError(e, "Hook 测试失败");
    }
  }

  private async _runToolAction(name: string, tool: ToolInfo) {
    if (this._toolActionInFlight) return;
    this._toolActionInFlight = true;
    const action = tool.installed ? "update" : "install";
    try {
      await api.toolAction(name, action);
      await this._loadToolsStatus();
      notifySuccess(`${name} 已${action === "update" ? "更新" : "安装"}`);
    } catch (e) {
      notifyError(e, `${name}${action === "update" ? "更新" : "安装"}失败`);
    } finally {
      this._toolActionInFlight = false;
    }
  }

  private async _exportTdl() {
    try {
      const res = await api.tdlSessionExport();
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "tdl-session-export.tdl";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      notifyError(e, "导出 tdl 登录失败");
    }
  }

  private async _importTdl() {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".tdl";
    input.onchange = async () => {
      const file = input.files?.[0];
      if (!file) return;
      const fd = new FormData();
      fd.append("file", file);
      try {
        const res = await api.tdlSessionImport(fd);
        notifySuccess(res.message || "已导入");
      } catch (e) {
        notifyError(e, "导入 tdl 登录失败");
      }
    };
    input.click();
  }

  private _onTdlAction(e: CustomEvent) {
    const action = e.detail?.item?.value;
    if (action === "export") this._exportTdl();
    else if (action === "import") this._importTdl();
  }

  private _onHookAction(e: CustomEvent) {
    const action = e.detail?.item?.value;
    if (action === "save") this._saveHook();
    else if (action === "test") this._testHook();
  }

  private _toolEntries(): Array<[string, ToolInfo]> {
    const tools = this._tools?.tools || {};
    const preferred = ["tdl", "yt-dlp", "ffmpeg"];
    return Object.entries(tools).sort(([a], [b]) => {
      const ai = preferred.indexOf(a);
      const bi = preferred.indexOf(b);
      if (ai === -1 && bi === -1) return a.localeCompare(b);
      if (ai === -1) return 1;
      if (bi === -1) return -1;
      return ai - bi;
    });
  }

  private _toolStatusVariant(tool: ToolInfo): string {
    return tool.installed ? "success" : "danger";
  }

  private _toolStatusText(tool: ToolInfo): string {
    return tool.installed ? "已就绪" : "未检测到";
  }

  private _toolActionLabel(tool: ToolInfo): string {
    return tool.installed ? "更新" : "安装";
  }

  private _toolVersionText(tool: ToolInfo): string {
    return tool.version || (tool.installed ? "-" : "未安装");
  }

  private _toolPathText(tool: ToolInfo): string {
    return tool.path || tool.hint || "-";
  }

  render() {
    const s = this.appState;
    const sc = s?.job_counts;
    const success = sc?.success ?? 0;
    const failed = sc?.failed ?? 0;
    const queued = sc?.queued ?? 0;
    const cancelled = sc?.cancelled ?? 0;
    const total = success + failed + queued + cancelled;
    const issueCount = failed + cancelled;
    const allowCount = (s?.allowed_user_ids?.length ?? 0) + (s?.allowed_usernames?.length ?? 0);
    const successRate = total ? Math.round((success / total) * 100) : 0;
    const queueSize = s?.queue?.in_memory_size ?? 0;
    const toolEntries = this._toolEntries();

    return html`
      <div class="page">
        <div class="hero-grid">
          <lui-card size="sm">
            <lui-statistic title="成功率" .value=${successRate} suffix="%" status="success" size="sm"></lui-statistic>
            <lui-progress percent=${successRate} status="success" size="sm" style="margin-top:10px" .showText=${false}></lui-progress>
          </lui-card>
          <lui-card size="sm">
            <lui-statistic title="队列任务" .value=${queueSize} status=${queueSize > 0 ? "warning" : "primary"} size="sm"></lui-statistic>
            <lui-progress percent=${Math.min(100, queueSize * 10)} status=${queueSize > 0 ? "warning" : "normal"} size="sm" style="margin-top:10px" .showText=${false}></lui-progress>
          </lui-card>
          <lui-card size="sm">
            <lui-statistic title="异常任务" .value=${issueCount} status=${issueCount > 0 ? "danger" : "success"} size="sm"></lui-statistic>
            <lui-progress percent=${total ? Math.round((issueCount / total) * 100) : 0} status=${issueCount > 0 ? "danger" : "success"} size="sm" style="margin-top:10px" .showText=${false}></lui-progress>
          </lui-card>
          <lui-card size="sm">
            <lui-statistic title="白名单用户" .value=${allowCount} status="primary" size="sm"></lui-statistic>
          </lui-card>
        </div>

        <div class="main-grid">
          <div class="stack">
            <lui-card title="任务概览" subtitle="按状态和来源聚合">
              <div class="summary-strip">
                <div class="summary-item"><div class="summary-label">总任务</div><div class="summary-value">${total}</div></div>
                <div class="summary-item"><div class="summary-label">已完成</div><div class="summary-value">${success}</div></div>
                <div class="summary-item"><div class="summary-label">等待中</div><div class="summary-value">${queued}</div></div>
              </div>
              <lui-divider></lui-divider>
              <div class="two-col">
                ${this._renderDistribution("任务状态", this._stats?.by_status || [], "status")}
                ${this._renderDistribution("来源类型", this._stats?.by_source_type || [], "source_type")}
              </div>
            </lui-card>

            <lui-card title="工具状态" subtitle="检测 tdl / yt-dlp / ffmpeg">
              <div class="tool-status-list">
                ${toolEntries.length === 0
                  ? html`<div class="tool-empty">暂无工具状态</div>`
                  : toolEntries.map(([name, tool]) => html`
                      <div class="tool-row">
                        <div class="tool-main">
                          <div class="tool-title">
                            <span class="tool-name">${name}</span>
                            <lui-tag size="sm" variant=${this._toolStatusVariant(tool)}>${this._toolStatusText(tool)}</lui-tag>
                          </div>
                          <div class="tool-meta">
                            <div>版本：<code>${this._toolVersionText(tool)}</code></div>
                            <div>路径：<code>${this._toolPathText(tool)}</code></div>
                          </div>
                        </div>
                        <div>
                          <lui-button
                            size="xs"
                            variant=${tool.installed ? "default" : "primary"}
                            ?loading=${this._toolActionInFlight}
                            ?disabled=${this._toolActionInFlight}
                            @click=${() => this._runToolAction(name, tool)}
                          >
                            ${this._toolActionLabel(tool)}
                          </lui-button>
                        </div>
                      </div>
                    `)}
              </div>
              <div class="updated-at">最后检测：${formatDateTime(this._tools?.updated_at) || "-"}</div>
            </lui-card>
          </div>

          <div class="stack">
            <lui-card title="运行设置" subtitle="队列和访问控制">
              <div class="settings-section">
                <div class="section-title"><lui-icon name="sliders-horizontal"></lui-icon>系统开关</div>
                <div class="form-stack">
                  <lui-form-item label="开启白名单限制" size="sm">
                    <lui-switch size="sm" .checked=${this._requireAllowlist}
                      @change=${(e: CustomEvent) => { this._requireAllowlist = e.detail.checked; }}></lui-switch>
                  </lui-form-item>
                  <lui-form-item label="下载完成后发送文本通知" size="sm">
                    <lui-switch size="sm" .checked=${this._replyOnFinish}
                      @change=${(e: CustomEvent) => { this._replyOnFinish = e.detail.checked; }}></lui-switch>
                  </lui-form-item>
                  <lui-form-item label="同时下载数" size="sm">
                    <lui-input-number class="compact-number" size="sm" .value=${this._maxConcurrentJobs} min=${1} step=${1}
                      @change=${(e: CustomEvent) => { this._maxConcurrentJobs = e.detail.value; }}></lui-input-number>
                  </lui-form-item>
                </div>
              </div>
            </lui-card>

            <lui-card title="tdl 登录" subtitle="导入或导出登录会话">
              <lui-button-group selection="none" size="xs" radius="sm" @change=${this._onTdlAction}>
                <lui-button-group-item value="export">
                  <lui-icon slot="prefix" name="download"></lui-icon>
                  导出 tdl 登录
                </lui-button-group-item>
                <lui-button-group-item value="import" variant="primary">
                  <lui-icon slot="prefix" name="upload"></lui-icon>
                  导入 tdl 登录
                </lui-button-group-item>
              </lui-button-group>
            </lui-card>

            <lui-card title="事件通知" subtitle="Webhook 和脚本可独立启用">
              <div class="form-stack">
                <lui-form-item label="Webhook URL（留空关闭）" size="sm">
                  <lui-input size="sm" placeholder="https://example.com/hook" .value=${this._webhookUrl}
                    @input=${(e: CustomEvent) => { this._webhookUrl = (e.target as HTMLInputElement).value; }}></lui-input>
                </lui-form-item>
                <lui-form-item label="脚本命令（留空关闭）" size="sm">
                  <lui-input size="sm" placeholder="python3 /opt/hook.py" .value=${this._hookScript}
                    @input=${(e: CustomEvent) => { this._hookScript = (e.target as HTMLInputElement).value; }}></lui-input>
                </lui-form-item>
                <lui-button-group selection="none" size="xs" radius="sm" @change=${this._onHookAction}>
                  <lui-button-group-item value="save" variant="primary">
                    <lui-icon slot="prefix" name="save"></lui-icon>
                    保存设置
                  </lui-button-group-item>
                  <lui-button-group-item value="test">
                    <lui-icon slot="prefix" name="send"></lui-icon>
                    测试通知
                  </lui-button-group-item>
                </lui-button-group>
                <div class="help-text">Webhook 和脚本都可以单独关闭；留空就是不启用。</div>
              </div>
            </lui-card>
          </div>
        </div>
      </div>
    `;
  }

  private _renderDistribution(title: string, items: { count: number; [k: string]: unknown }[], key: string) {
    const max = Math.max(1, ...items.map(i => i.count || 0));
    return html`
      <lui-card size="sm" shadow="none" title=${title}>
        <div class="status-list">
          ${items.length === 0 ? html`<lui-empty-state description="暂无数据"></lui-empty-state>` : items.slice(0, 6).map(item => {
            const value = String(item[key] || "unknown");
            const count = item.count || 0;
            return html`
              <div class="metric-row">
                <div class="metric-title">
                  <lui-tag size="sm" variant=${this._tagVariant(value)}>${escapeHtml(value)}</lui-tag>
                </div>
                <lui-progress
                  percent=${Math.max(4, Math.round((count / max) * 100))}
                  status=${this._progressStatus(value)}
                  size="sm"
                  .showText=${false}
                ></lui-progress>
                <div class="metric-value">${count}</div>
              </div>
            `;
          })}
        </div>
      </lui-card>
    `;
  }

  private _tagVariant(value: string): string {
    if (["success", "tdl", "video"].includes(value)) return "success";
    if (["failed", "cancelled", "error"].includes(value)) return "danger";
    if (["queued", "downloading", "warning"].includes(value)) return "warning";
    return "primary";
  }

  private _progressStatus(value: string): string {
    if (["success", "tdl", "video"].includes(value)) return "success";
    if (["failed", "cancelled", "error"].includes(value)) return "danger";
    if (["queued", "downloading", "warning"].includes(value)) return "warning";
    return "normal";
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "tg-overview": TgOverview;
  }
}
