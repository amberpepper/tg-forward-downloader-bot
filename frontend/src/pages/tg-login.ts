import { tokens } from "lituix";
import { LitElement, html, css } from "lit";
import { customElement, state } from "lit/decorators.js";
import type { FormSchema } from "lituix";
import { z } from "zod";

const loginSchema = z.object({
  username: z.string().min(1, "请输入用户名"),
  password: z.string().min(1, "请输入密码"),
});

@customElement("tg-login")
export class TgLogin extends LitElement {
  @state() private _error = "";
  @state() private _loading = false;

  private _schema: FormSchema = {
    validator: loginSchema as any,
    fields: [
      { name: "username", type: "text", label: "用户名", required: true },
      { name: "password", type: "password", label: "密码", required: true },
    ],
    submitLabel: "登录",
    submitProps: { block: true, icon: "log-in" },
    onSubmit: async (values) => {
      this._error = "";
      this._loading = true;
      try {
        const form = new FormData();
        form.append("username", values.username);
        form.append("password", values.password);
        const res = await fetch("/login", { method: "POST", body: form, credentials: "same-origin" });
        if (res.redirected) {
          window.location.href = res.url;
          return;
        }
        if (res.status === 401) {
          this._error = "用户名或密码错误";
          return;
        }
        window.location.href = "/admin";
      } catch {
        this._error = "登录请求失败";
      } finally {
        this._loading = false;
      }
    },
  };

  static styles = [tokens, css`
    :host {
      display: flex;
      align-items: center;
      justify-content: center;
      min-height: 100vh;
      background:
        radial-gradient(circle at top right, rgba(0,122,204,0.15), transparent 28%),
        radial-gradient(circle at bottom left, rgba(54,148,50,0.1), transparent 22%),
        var(--lui-color-bg, #1e1e1e);
      color: var(--lui-color-fg, #d4d4d4);
      font-family: var(--lui-font, "Segoe UI", Inter, sans-serif);
      padding: 24px;
    }
    .box {
      width: 400px;
      background: var(--lui-color-bg-subtle, #252526);
      border: 1px solid var(--lui-color-border, #313131);
      border-radius: 20px;
      padding: 28px;
      box-shadow: var(--lui-shadow-lg, 0 16px 36px rgba(0,0,0,0.28));
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
      margin-bottom: 18px;
    }
    .brand-icon {
      width: 30px; height: 30px;
      border-radius: 10px;
      display: flex; align-items: center; justify-content: center;
      background: linear-gradient(135deg, rgba(0,122,204,0.18), rgba(0,122,204,0.08));
    }
    .brand-icon lui-icon { --lui-icon-size: 16px; }
    h1 { font-size: 20px; margin: 0 0 4px; }
    .muted { color: var(--lui-color-fg-muted, #9da2a6); font-size: 13px; }
    .error-box {
      background: rgba(244,71,71,0.12);
      color: #fca5a5;
      padding: 12px;
      border-radius: 12px;
      margin-bottom: 14px;
      border: 1px solid rgba(244,71,71,0.35);
      font-size: 13px;
    }
    lui-button { --lui-button-icon-size: 16px; }
    `,
  ];

  render() {
    return html`
      <div class="box">
        <div class="brand">
          <div class="brand-icon">
            <lui-icon name="download"></lui-icon>
          </div>
          <div>
            <h1>Web 管理后台</h1>
            <div class="muted">请输入后台账号登录。</div>
          </div>
        </div>
        ${this._error ? html`<div class="error-box">${this._error}</div>` : ""}
        <lui-form
          .schema=${this._schema}
          size="sm"
        ></lui-form>
      </div>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "tg-login": TgLogin;
  }
}
