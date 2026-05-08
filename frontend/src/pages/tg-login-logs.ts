import { tokens } from "lituix";
import { LitElement, html, css } from "lit";
import { customElement, state } from "lit/decorators.js";
import type { TableColumn } from "lituix";
import { api } from "../lib/api";
import type { LoginLogsResponse } from "../lib/types";
import { escapeHtml, formatDateTime } from "../lib/utils";

@customElement("tg-login-logs")
export class TgLoginLogs extends LitElement {
  @state() private _data: LoginLogsResponse | null = null;
  @state() private _q = "";
  @state() private _success = "all";
  @state() private _page = 1;
  @state() private _pageSize = 20;
  private _timer: ReturnType<typeof setInterval> | null = null;

  private _columns: TableColumn[] = [
    { title: "时间", dataIndex: "created_at", width: 160, render: (v: unknown) => formatDateTime(String(v || "")) },
    { title: "结果", dataIndex: "success", width: 80, render: (v: unknown) =>
      html`<lui-tag size="sm" variant=${v ? "success" : "danger"}>${v ? "成功" : "失败"}</lui-tag>` },
    { title: "用户名", dataIndex: "username", width: 120 },
    { title: "IP", dataIndex: "ip", width: 140 },
    { title: "User-Agent", dataIndex: "user_agent", width: 280, render: (v: unknown) =>
      html`<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:block;max-width:280px">${escapeHtml(String(v || ""))}</span>` },
    { title: "失败原因", dataIndex: "failure_reason", width: 160, render: (v: unknown) => escapeHtml(String(v || "")) },
  ];

  static styles = [tokens, css`
    :host { display: block; }
    .toolbar { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; align-items: center; }
    lui-button { --lui-button-icon-size: 14px; }
    `,
  ];

  connectedCallback() {
    super.connectedCallback();
    this._reload();
    this._timer = setInterval(() => this._reload(true), 5000);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    if (this._timer) clearInterval(this._timer);
  }

  private async _reload(background = false) {
    try {
      this._data = await api.loginLogs({ page: this._page, page_size: this._pageSize, q: this._q, success: this._success === "all" ? "" : this._success });
    } catch {}
  }

  private _onPageChange(e: CustomEvent) {
    this._page = e.detail.current || e.detail.page || 1;
    this._reload();
  }

  render() {
    const d = this._data;
    return html`
      <lui-card title="登录日志">
        <lui-tag size="sm" slot="extra">审计</lui-tag>
          <div class="toolbar">
            <lui-input size="sm" placeholder="搜索用户名 / IP / UA" style="width:220px"
              .value=${this._q} @input=${(e: CustomEvent) => { this._q = (e.target as HTMLInputElement).value; }}></lui-input>
            <lui-select size="sm" style="width:110px" .value=${this._success}
              @change=${(e: CustomEvent) => { this._success = e.detail.value || "all"; }}>
              <lui-option value="all">全部结果</lui-option>
              <lui-option value="true">成功</lui-option>
              <lui-option value="false">失败</lui-option>
            </lui-select>
            <lui-button size="xs" variant="primary" @click=${() => { this._page = 1; this._reload(); }} icon="search">搜索</lui-button>
          </div>
          <lui-table
            .columns=${this._columns}
            .data=${d?.items || []}
            .rowKey=${(row: Record<string, unknown>, i: number) => String(row.id ?? i)}
            bordered
            .pagination=${true}
            .current=${d?.page || 1}
            .pageSize=${this._pageSize}
            .total=${d?.total || 0}
            .showSizeChanger=${false}
            emptyText="暂无登录日志"
            size="sm"
            @page-change=${this._onPageChange}
          >        </lui-table>
      </lui-card>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "tg-login-logs": TgLoginLogs;
  }
}
