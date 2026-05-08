import { createDialogForm, tokens, type FormSchema } from "lituix";
import { LitElement, html, css, nothing } from "lit";
import { customElement, property } from "lit/decorators.js";
import { z } from "zod";
import type { AppState } from "../lib/types";
import { api } from "../lib/api";
import { escapeHtml, formatDateTime } from "../lib/utils";
import { notifyError, notifySuccess } from "../lib/notify";

const accessSchema = z
  .object({
    user_id: z.string(),
    username: z.string(),
  })
  .superRefine((values, ctx) => {
    const userId = values.user_id.trim();
    const username = values.username.trim();
    if (userId || username) return;
    const message = "请至少填写一个 user_id 或 username";
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      path: ["user_id"],
      message,
    });
    ctx.addIssue({
      code: z.ZodIssueCode.custom,
      path: ["username"],
      message,
    });
  });

@customElement("tg-access")
export class TgAccess extends LitElement {
  @property({ attribute: false }) appState: AppState | null = null;

  private _overlayContainer(): HTMLElement | undefined {
    return this.closest("lui-config-provider") ?? undefined;
  }

  static styles = [tokens, css`
    :host { display: block; }
    .toolbar { display: flex; gap: 8px; margin-bottom: 12px; }
    .muted { color: var(--lui-color-fg-muted); font-size: 12px; }
    .access-list { display: grid; gap: 10px; }
    .access-item {
      display: flex; align-items: center; justify-content: space-between; gap: 14px;
      padding: 12px 14px;
      border: 1px solid var(--lui-color-border);
      border-radius: 12px;
      background: var(--lui-color-bg);
    }
    .access-main { min-width: 0; display: grid; gap: 3px; }
    .access-title { font-size: 14px; font-weight: 700; word-break: break-all; }
    .access-meta { display: flex; gap: 10px; flex-wrap: wrap; color: var(--lui-color-fg-muted); font-size: 12px; }
    .access-actions { display: flex; gap: 8px; flex-shrink: 0; }
    .pending-section { margin-top: 16px; }
    .section-title {
      font-size: 13px; font-weight: 700; margin-bottom: 12px;
      display: flex; align-items: center; gap: 8px;
    }
    lui-button { --lui-button-icon-size: 14px; }
    `,
  ];

  private async _allow(userId: number | null, username: string | null) {
    try {
      await api.allowUser(userId, username);
      notifySuccess("已授权");
      this.dispatchEvent(new CustomEvent("refresh"));
    } catch (e) { notifyError(e, "授权失败"); }
  }

  private async _deny(userId: number | null, username: string | null) {
    try {
      await api.denyUser(userId, username);
      notifySuccess("已移除");
      this.dispatchEvent(new CustomEvent("refresh"));
    } catch (e) { notifyError(e, "移除失败"); }
  }

  private _openAddDialog() {
    const schema: FormSchema = {
      validator: accessSchema as any,
      fields: [
        { name: "user_id", type: "text", label: "user_id" },
        { name: "username", type: "text", label: "username" },
      ],
      description: "只填一个就行；如果两个都填，会一起写入白名单。",
      submitLabel: "确认添加",
      onSubmit: async () => {},
    };

    const ctrl = createDialogForm({
      title: "添加白名单用户",
      schema,
      container: this._overlayContainer(),
    });

    ctrl.closed.then(async (values) => {
      if (!values) return;
      const rawUserId = String(values.user_id ?? "").trim();
      const rawUsername = String(values.username ?? "").trim();
      const userId = rawUserId ? Number(rawUserId) : null;
      const username = rawUsername ? rawUsername.replace(/^@/, "") : null;
      await this._allow(Number.isFinite(userId as number) ? userId : null, username);
    }).finally(() => ctrl.destroy());

    ctrl.show();
  }

  render() {
    const s = this.appState;
    const userIds = s?.allowed_user_ids || [];
    const usernames = s?.allowed_usernames || [];
    const pending = (s?.access_requests || []).filter(r => (r.status || "pending") === "pending");

    return html`
      <lui-card title="用户授权">
        <lui-tag size="sm" slot="extra">已授权 ${(userIds?.length ?? 0) + (usernames?.length ?? 0)}</lui-tag>
          <div class="toolbar">
            <lui-button size="xs" variant="primary" @click=${this._openAddDialog} icon="user-plus">添加用户</lui-button>
            <lui-button size="xs" @click=${() => this.dispatchEvent(new CustomEvent("refresh"))} icon="refresh-cw">刷新</lui-button>
          </div>

          <div class="section-title">
            <span>已授权用户</span>
            <span class="muted">${(userIds?.length ?? 0) + (usernames?.length ?? 0)} 人</span>
          </div>

          <div class="access-list">
            ${userIds.map(id => html`
              <div class="access-item">
                <div class="access-main">
                  <div class="access-title">${escapeHtml(String(id))}</div>
                  <div class="access-meta"><span>user_id</span></div>
                </div>
                <div class="access-actions">
                  <lui-button size="xs" variant="danger" @click=${() => this._deny(id, null)} icon="trash-2">移除</lui-button>
                </div>
              </div>
            `)}
            ${usernames.map(name => html`
              <div class="access-item">
                <div class="access-main">
                  <div class="access-title">${escapeHtml("@" + name)}</div>
                  <div class="access-meta"><span>username</span></div>
                </div>
                <div class="access-actions">
                  <lui-button size="xs" variant="danger" @click=${() => this._deny(null, name)} icon="trash-2">移除</lui-button>
                </div>
              </div>
            `)}
            ${!userIds.length && !usernames.length ? html`<div class="muted">暂无已授权用户</div>` : ""}
          </div>

          ${pending.length ? html`
            <div class="pending-section">
              <div class="section-title">
                <span>待审核用户</span>
                <span class="muted">${pending.length} 人</span>
              </div>
              <div class="access-list">
                ${pending.map(item => html`
                  <div class="access-item">
                    <div class="access-main">
                      <div class="access-title">${escapeHtml(item.full_name || "-")}</div>
                      <div class="access-meta">
                        <span>${escapeHtml(item.username ? "@" + item.username : "无 username")}</span>
                        <span>${escapeHtml(String(item.user_id || "无 user_id"))}</span>
                        <span>chat_id: ${escapeHtml(String(item.chat_id || ""))}</span>
                      </div>
                      <div class="access-meta">
                        <span>首次: ${formatDateTime(item.first_seen_at)}</span>
                        <span>最近: ${formatDateTime(item.last_seen_at)}</span>
                      </div>
                    </div>
                    <div class="access-actions">
                      <lui-button size="xs" variant="primary"
                        @click=${() => this._allow(item.user_id, item.username)} icon="user-check">授权</lui-button>
                      <lui-button size="xs"
                        @click=${() => this._deny(item.user_id, item.username)} icon="user-x">拒绝</lui-button>
                    </div>
                  </div>
                `)}
              </div>
            </div>
          ` : nothing}
      </lui-card>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "tg-access": TgAccess;
  }
}
