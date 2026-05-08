import { createToastHost, type MountTarget } from "lituix";

const toastHost = createToastHost({
  position: "top-right",
  max: 5,
});

let toastTarget: MountTarget | null = null;

function ensureToastHost() {
  if (!toastHost.mounted) {
    toastHost.mount(toastTarget ?? undefined);
  }
}

function showToast(options: { level: "success" | "error" | "info"; message: string; duration?: number }) {
  ensureToastHost();
  toastHost.show(options);
}

export function setNotifyTarget(target: MountTarget | null | undefined): void {
  const next = target ?? null;
  if (toastTarget === next && toastHost.mounted) return;
  toastTarget = next;
  toastHost.unmount();
  toastHost.mount(toastTarget ?? undefined);
}

export function errorMessage(error: unknown, fallback = "操作失败"): string {
  if (error instanceof Error && error.message) return error.message;
  if (typeof error === "string" && error) return error;
  return fallback;
}

export function notifySuccess(message: string): void {
  showToast({ level: "success", message });
}

export function notifyError(error: unknown, fallback = "操作失败"): void {
  showToast({ level: "error", message: errorMessage(error, fallback), duration: 4000 });
}

export function notifyInfo(message: string): void {
  showToast({ level: "info", message });
}
