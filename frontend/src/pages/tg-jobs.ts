import { createDialog, createDialogForm, tokens, type FormSchema } from "lituix";
import { LitElement, html, css, nothing } from "lit";
import { customElement, state } from "lit/decorators.js";
import type { TableColumn } from "lituix";
import { api } from "../lib/api";
import type { JobsResponse } from "../lib/types";
import { escapeHtml, formatDateTime, getFileBaseName, getPreviewType, statusColor, statusLabel } from "../lib/utils";
import { notifyError, notifyInfo, notifySuccess } from "../lib/notify";

@customElement("tg-jobs")
export class TgJobs extends LitElement {
  @state() private _data: JobsResponse | null = null;
  @state() private _loading = false;
  @state() private _q = "";
  @state() private _status = "all";
  @state() private _pageSize = 20;
  @state() private _page = 1;
  @state() private _selected = new Set<string>();
  private _timer: ReturnType<typeof setInterval> | null = null;

  private _isVideo(path: string): boolean {
    return getPreviewType(path) === "video";
  }

  private _overlayContainer(): HTMLElement | undefined {
    return this.closest("lui-config-provider") ?? undefined;
  }

  private _columns: TableColumn[] = [
    { title: "状态", dataIndex: "status", width: 100, render: (_v: unknown, row: Record<string, unknown>) =>
      html`<lui-tag size="sm" variant=${statusColor(String(row.status || ""))}>${statusLabel(String(row.status || ""))}</lui-tag>` },
    { title: "进度", dataIndex: "progress_percent", width: 80, render: (v: unknown) =>
      v != null ? `${Math.round(Number(v))}%` : "--" },
    { title: "次数", dataIndex: "attempts", width: 70 },
    { title: "用户", dataIndex: "from_user", width: 160, render: (_v: unknown, row: Record<string, unknown>) =>
      html`<div style="display:flex;flex-direction:column;gap:2px"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${escapeHtml(String(row.from_user || ""))}</span><span style="color:var(--lui-color-fg-muted);font-size:11px">${escapeHtml(String(row.from_user_id || ""))}</span></div>` },
    { title: "类型", dataIndex: "source_type", width: 100 },
    { title: "消息", dataIndex: "source_value", width: 260, render: (v: unknown) =>
      html`<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:block;max-width:260px">${escapeHtml(String(v || ""))}</span>` },
    { title: "更新时间", dataIndex: "updated_at", width: 160, render: (v: unknown) => formatDateTime(String(v || "")) },
    { title: "失败原因", dataIndex: "error", width: 200, render: (v: unknown) =>
      html`<span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:block;max-width:200px">${escapeHtml(String(v || ""))}</span>` },
    { title: "操作", key: "_actions", width: 280, render: (_v: unknown, row: Record<string, unknown>) => {
      const id = String(row.job_id || "");
      const files = Array.isArray(row.files) ? row.files as unknown[] : [];
      const hasVideo = files.some(v => this._isVideo(String(v || "")));
      return html`
        <lui-button-group
          selection="none"
          size="xs"
          radius="sm"
          data-table-ignore-row-click
          @change=${(e: CustomEvent) => this._onRowAction(e, id)}
        >
          <lui-button-group-item value="detail">
            <lui-icon slot="prefix" name="eye"></lui-icon>
            详情
          </lui-button-group-item>
          ${hasVideo ? html`
            <lui-button-group-item value="preview">
              <lui-icon slot="prefix" name="image"></lui-icon>
              预览
            </lui-button-group-item>
          ` : nothing}
          ${row.can_cancel ? html`
            <lui-button-group-item value="cancel">
              <lui-icon slot="prefix" name="x"></lui-icon>
              取消
            </lui-button-group-item>
          ` : nothing}
          ${row.can_retry ? html`
            <lui-button-group-item value="retry">
              <lui-icon slot="prefix" name="rotate-ccw"></lui-icon>
              重试
            </lui-button-group-item>
          ` : nothing}
          ${!row.can_cancel ? html`
            <lui-button-group-item value="delete">
              <lui-icon slot="prefix" name="trash-2"></lui-icon>
              删除
            </lui-button-group-item>
          ` : nothing}
        </lui-button-group>
      `;
    }},
  ];

  static styles = [tokens, css`
    :host { display: block; }
    .toolbar { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 12px; align-items: center; }
    .toolbar-right { margin-left: auto; display: flex; gap: 8px; }
    lui-button { --lui-button-icon-size: 14px; }
    .detail-grid { display: grid; grid-template-columns: 140px 1fr; gap: 6px 14px; margin-bottom: 14px; font-size: 12px; }
    .detail-grid .muted { color: var(--lui-color-fg-muted); }
    .code-block {
      white-space: pre-wrap; word-break: break-word;
      background: var(--lui-color-bg);
      padding: 12px; border-radius: 10px;
      border: 1px solid var(--lui-color-border);
      max-height: 220px; overflow: auto;
      font-family: var(--lui-font-mono, monospace);
      font-size: 12px;
    }
    .file-list { list-style: none; padding: 0; display: grid; gap: 8px; }
    .file-list li {
      padding: 10px 12px; border: 1px solid var(--lui-color-border);
      border-radius: 10px; display: flex; justify-content: space-between; align-items: center; gap: 10px;
    }
    .file-name { font-weight: 600; word-break: break-all; }
    .muted { color: var(--lui-color-fg-muted); }
    .detail-chips { display: flex; gap: 8px; margin-bottom: 12px; }
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
    if (this._loading && !background) return;
    if (!background) this._loading = true;
    try {
      this._data = await api.jobs({ page: this._page, page_size: this._pageSize, q: this._q, status: this._status === "all" ? "" : this._status });
    } catch {}
    this._loading = false;
  }

  private async _detail(id: string) {
    try {
      const [d, log] = await Promise.all([api.jobDetail(id), api.jobLog(id)]);
      const job = d.job;
      const content = document.createElement("div");
      content.innerHTML = `
        <div style="display:grid;grid-template-columns:140px 1fr;gap:6px 14px;margin-bottom:14px;font-size:12px;">
          <span style="color:var(--lui-color-fg-muted);">job_id</span><span>${escapeHtml(String(job.job_id || ""))}</span>
          <span style="color:var(--lui-color-fg-muted);">status</span><span>${escapeHtml(statusLabel(String(job.status || "")))}</span>
          <span style="color:var(--lui-color-fg-muted);">source_type</span><span>${escapeHtml(String(job.source_type || ""))}</span>
          <span style="color:var(--lui-color-fg-muted);">source_value</span><span>${escapeHtml(String(job.source_value || ""))}</span>
          <span style="color:var(--lui-color-fg-muted);">from_user</span><span>${escapeHtml(String(job.from_user || ""))} (${escapeHtml(String(job.from_user_id || ""))})</span>
          <span style="color:var(--lui-color-fg-muted);">submitted_at</span><span>${formatDateTime(String(job.submitted_at || ""))}</span>
          <span style="color:var(--lui-color-fg-muted);">updated_at</span><span>${formatDateTime(String(job.updated_at || ""))}</span>
          <span style="color:var(--lui-color-fg-muted);">attempts</span><span>${escapeHtml(String(job.attempts || 0))}</span>
        </div>
        <h4 style="margin:12px 0 8px">失败原因</h4>
        <div style="white-space:pre-wrap;word-break:break-word;background:var(--lui-color-bg);padding:12px;border-radius:10px;border:1px solid var(--lui-color-border);max-height:220px;overflow:auto;font-family:var(--lui-font-mono, monospace);font-size:12px;">${escapeHtml(String(job.error || "无"))}</div>
        <h4 style="margin:12px 0 8px">下载日志</h4>
        <div style="white-space:pre-wrap;word-break:break-word;background:var(--lui-color-bg);padding:12px;border-radius:10px;border:1px solid var(--lui-color-border);max-height:220px;overflow:auto;font-family:var(--lui-font-mono, monospace);font-size:12px;">${escapeHtml(log || "暂无日志")}</div>
      `;
      const ctrl = createDialog({
        title: "任务详情",
        content,
        width: "min(920px, 100%)",
        backdrop: "opaque",
        closeOnOutsidePress: true,
        shakeOnOutsideClick: true,
        container: this._overlayContainer(),
      });
      ctrl.closed.finally(() => ctrl.destroy());
      ctrl.show();
    } catch (e) { notifyError(e, "加载任务详情失败"); }
  }

  private async _preview(id: string) {
    try {
      const d = await api.jobDetail(id);
      const job = d.job as Record<string, unknown>;
      const files = Array.isArray(job.files) ? job.files.map(v => String(v || "")).filter(Boolean) : [];
      const videoFiles = files
        .map((path, index) => ({ path, index }))
        .filter(item => this._isVideo(item.path));
      if (!videoFiles.length) {
        notifyInfo("暂无可预览视频");
        return;
      }

      const content = document.createElement("div");
      let current = 0;

      const renderPreview = (index: number) => {
        const item = videoFiles[index];
        const src = api.jobFileUrl(id, item.index);
        return `<video controls playsinline preload="metadata" src="${src}" style="width:100%;max-height:56vh;border-radius:10px;background:#000;"></video>`;
      };

      const render = () => {
        const rows = videoFiles.map((item, idx) => {
          const path = item.path;
          return `
            <li style="display:flex;justify-content:space-between;gap:10px;align-items:center;padding:8px 0;border-top:${idx ? "1px solid var(--lui-color-border)" : "none"};">
              <span style="word-break:break-all;">${escapeHtml(getFileBaseName(path))}</span>
              <span style="display:flex;gap:8px;flex-shrink:0;">
                <button type="button" data-preview-index="${idx}">预览</button>
                <a href="${api.jobFileUrl(id, item.index)}" target="_blank" rel="noopener"><button type="button">下载</button></a>
              </span>
            </li>
          `;
        }).join("");

        content.innerHTML = `
          <div style="display:grid;gap:12px;">
            <div id="job-preview-box">${renderPreview(current)}</div>
            <ul style="list-style:none;margin:0;padding:0;">${rows}</ul>
          </div>
        `;
      };

      render();
      content.addEventListener("click", (ev) => {
        const target = ev.target as HTMLElement | null;
        const btn = target?.closest("button[data-preview-index]") as HTMLButtonElement | null;
        if (!btn) return;
        const idx = Number(btn.dataset.previewIndex || "-1");
        if (!Number.isFinite(idx) || idx < 0 || idx >= videoFiles.length) return;
        current = idx;
        render();
      });

      const ctrl = createDialog({
        title: "文件预览",
        content,
        width: "min(980px, 100%)",
        backdrop: "opaque",
        closeOnOutsidePress: true,
        shakeOnOutsideClick: true,
        container: this._overlayContainer(),
      });
      ctrl.closed.finally(() => ctrl.destroy());
      ctrl.show();
    } catch (e) {
      notifyError(e, "加载预览失败");
    }
  }

  private async _cancel(id: string) {
    try { await api.cancelJob(id); notifySuccess("已取消任务"); this._reload(); } catch (e) { notifyError(e, "取消任务失败"); }
  }

  private async _retry(id: string) {
    try { await api.retryJob(id); notifySuccess("已重试任务"); this._reload(); } catch (e) { notifyError(e, "重试任务失败"); }
  }

  private async _delete(id: string) {
    try { await api.bulkDelete([id]); notifySuccess("已删除任务"); this._reload(); } catch (e) { notifyError(e, "删除任务失败"); }
  }

  private _onRowAction(e: CustomEvent, id: string) {
    const action = e.detail?.item?.value;
    if (action === "detail") this._detail(id);
    else if (action === "preview") this._preview(id);
    else if (action === "cancel") this._cancel(id);
    else if (action === "retry") this._retry(id);
    else if (action === "delete") this._delete(id);
  }

  private async _bulkAction(action: unknown) {
    if (action !== "cancel" && action !== "retry" && action !== "delete") return;
    if (!this._selected.size) { notifyInfo("请先勾选任务"); return; }
    try {
      const ids = Array.from(this._selected);
      if (action === "cancel") await api.bulkCancel(ids);
      else if (action === "retry") await api.bulkRetry(ids);
      else await api.bulkDelete(ids);
      this._selected.clear();
      notifySuccess("批量操作已完成");
      this._reload();
    } catch (e) { notifyError(e, "批量操作失败"); }
  }

  private _openAddDialog() {
    const schema: FormSchema = {
      fields: [
        {
          name: "links",
          type: "textarea",
          label: "下载链接",
          required: true,
          description: "每行一个链接",
        },
      ],
      submitLabel: "确认添加",
      onSubmit: async () => {},
    };

    const ctrl = createDialogForm({
      title: "添加下载",
      schema,
      width: "min(680px, 100%)",
      container: this._overlayContainer(),
    });

    ctrl.closed.then(async (values) => {
      if (!values) return;
      const lines = String(values.links ?? "").split("\n").map((l) => l.trim()).filter(Boolean);
      if (!lines.length) {
        notifyInfo("请输入下载链接");
        return;
      }
      for (const url of lines) {
        try {
          await api.manualJob(url);
        } catch (e) {
          notifyError(`${url}: ${e instanceof Error ? e.message : "提交失败"}`);
          return;
        }
      }
      notifySuccess("已添加下载任务");
      this._reload();
    }).finally(() => ctrl.destroy());

    ctrl.show();
  }

  private _onPageChange(e: CustomEvent) {
    this._page = e.detail.current || e.detail.page || 1;
    this._reload();
  }

  render() {
    const d = this._data;
    return html`
      <lui-card title="下载历史">
        <lui-tag size="sm" slot="extra">任务</lui-tag>
          <div class="toolbar">
            <lui-input size="sm" placeholder="搜索用户 / 链接 / 类型" style="width:220px"
              .value=${this._q} @input=${(e: CustomEvent) => { this._q = (e.target as HTMLInputElement).value; }}></lui-input>
            <lui-button size="xs" variant="primary" @click=${() => { this._page = 1; this._reload(); }} icon="search">搜索</lui-button>
            <lui-button size="xs" @click=${this._openAddDialog} icon="plus">添加下载</lui-button>
            <lui-select size="sm" style="width:110px" .value=${this._status}
              @change=${(e: CustomEvent) => { this._status = e.detail.value || "all"; }}>
              <lui-option value="all">全部状态</lui-option>
              <lui-option value="queued">queued</lui-option>
              <lui-option value="downloading">downloading</lui-option>
              <lui-option value="success">success</lui-option>
              <lui-option value="failed">failed</lui-option>
              <lui-option value="cancelled">cancelled</lui-option>
            </lui-select>
            <div class="toolbar-right">
              <lui-button-group
                selection="none"
                size="xs"
                radius="sm"
                @change=${(e: CustomEvent) => this._bulkAction(e.detail?.item?.value)}
              >
                <lui-button-group-item value="cancel" variant="danger">
                  <lui-icon slot="prefix" name="x"></lui-icon>
                  批量取消
                </lui-button-group-item>
                <lui-button-group-item value="retry" variant="primary">
                  <lui-icon slot="prefix" name="rotate-ccw"></lui-icon>
                  批量重试
                </lui-button-group-item>
                <lui-button-group-item value="delete" variant="danger">
                  <lui-icon slot="prefix" name="trash-2"></lui-icon>
                  批量删除
                </lui-button-group-item>
              </lui-button-group>
            </div>
          </div>

          <lui-table
            .columns=${this._columns}
            .data=${d?.items || []}
            .rowKey=${(row: Record<string, unknown>) => String(row.job_id || "")}
            selection="multiple"
            select-on-click
            .selectedRowKeys=${Array.from(this._selected)}
            @selection-change=${(e: CustomEvent) => {
              this._selected = new Set(e.detail.selectedRowKeys.map(String));
            }}
            ?loading=${this._loading}
            bordered
            .pagination=${true}
            .current=${d?.page || 1}
            .pageSize=${this._pageSize}
            .total=${d?.total || 0}
            .showSizeChanger=${false}
            emptyText="暂无下载记录"
            size="sm"
            @page-change=${this._onPageChange}
          >        </lui-table>
      </lui-card>
    `;
  }
}

declare global {
  interface HTMLElementTagNameMap {
    "tg-jobs": TgJobs;
  }
}
