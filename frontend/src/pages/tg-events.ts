import { tokens } from "lituix";
import { LitElement, html, css } from "lit";
import { customElement, state } from "lit/decorators.js";
import type { TableColumn } from "lituix";
import { api } from "../lib/api";
import type { EventsResponse } from "../lib/types";
import { escapeHtml, formatDateTime, translateEventType } from "../lib/utils";

@customElement("tg-events")
export class TgEvents extends LitElement {
  @state() private _data: EventsResponse | null = null;
  @state() private _q = "";
  @state() private _eventType = "";
  @state() private _page = 1;
  @state() private _pageSize = 20;
  private _timer: ReturnType<typeof setInterval> | null = null;

  private _columns: TableColumn[] = [
    { title: "时间", dataIndex: "time", width: 160, render: (v: unknown) => formatDateTime(String(v || "")) },
    { title: "类型", dataIndex: "event_type", width: 120, render: (v: unknown) =>
      html`<lui-tag size="sm">${translateEventType(String(v || ""))}</lui-tag>` },
    { title: "用户", dataIndex: "username", width: 160, render: (_v: unknown, row: Record<string, unknown>) =>
      html`<span style="color:var(--lui-color-fg-muted);font-size:11px">${escapeHtml(String(row.user_id || ""))}</span>` },
    { title: "会话", dataIndex: "chat_id", width: 120 },
    { title: "消息", dataIndex: "message_id", width: 100 },
    { title: "详情", key: "_detail", render: (_v: unknown, row: Record<string, unknown>) => {
      const detail = Object.entries(row)
        .filter(([k]) => !["id","time","event_type","chat_id","user_id","username","full_name","message_id"].includes(k))
        .map(([k,v]) => `${escapeHtml(k)}=${escapeHtml(typeof v === "object" ? JSON.stringify(v) : v)}`)
        .join(" | ");
      return detail || "";
    }},
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
      this._data = await api.events({ page: this._page, page_size: this._pageSize, q: this._q, event_type: this._eventType });
    } catch {}
  }

  private _onPageChange(e: CustomEvent) {
    this._page = e.detail.current || e.detail.page || 1;
    this._reload();
  }

  render() {
    const d = this._data;
    return html`
      <lui-card title="事件历史">
        <lui-tag size="sm" slot="extra">事件</lui-tag>
          <div class="toolbar">
            <lui-input size="sm" placeholder="搜索类型 / 用户 / 详情" style="width:220px"
              .value=${this._q} @input=${(e: CustomEvent) => { this._q = (e.target as HTMLInputElement).value; }}></lui-input>
            <lui-input size="sm" placeholder="按事件类型过滤" style="width:160px"
              .value=${this._eventType} @input=${(e: CustomEvent) => { this._eventType = (e.target as HTMLInputElement).value; }}></lui-input>
            <lui-button size="xs" variant="primary" @click=${() => { this._page = 1; this._reload(); }} icon="search">搜索</lui-button>
            <lui-button size="xs" @click=${() => { this._eventType = ""; this._page = 1; this._reload(); }} icon="list">全部</lui-button>
            <lui-button size="xs" @click=${() => { this._eventType = "job.failed"; this._page = 1; this._reload(); }} icon="x">下载失败</lui-button>
            <lui-button size="xs" @click=${() => { this._eventType = "job.success"; this._page = 1; this._reload(); }} icon="check">下载成功</lui-button>
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
            emptyText="暂无事件记录"
            size="sm"
            @page-change=${this._onPageChange}
          >        </lui-table>
      </lui-card>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "tg-events": TgEvents;
  }
}
