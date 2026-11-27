// @vitest-environment jsdom

import { createApp, h, nextTick, ref } from "vue";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import GlobalTaskPauseControl from "./components/GlobalTaskPauseControl.vue";
import {
  globalTaskPauseState,
  resetGlobalTaskPauseState,
} from "./globalTaskPauseState";

const mounted = [];

async function settle() {
  await Promise.resolve();
  await nextTick();
  await Promise.resolve();
  await nextTick();
}

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolveValue, rejectValue) => {
    resolve = resolveValue;
    reject = rejectValue;
  });
  return { promise, resolve, reject };
}

async function mountControl(request, props = {}) {
  const host = document.createElement("div");
  document.body.append(host);
  const control = ref(null);
  const app = createApp({
    render: () => h(GlobalTaskPauseControl, { ...props, request, ref: control }),
  });
  app.mount(host);
  mounted.push({ app, host });
  await settle();
  return { app, host, control };
}

async function mount(request, props = {}) {
  const { host } = await mountControl(request, props);
  return host;
}

function actionButton() {
  return document.querySelector('[data-testid="global-task-pause-action"]');
}

function confirmButton() {
  return document.querySelector('[data-testid="global-task-pause-confirm"]');
}

beforeEach(() => {
  resetGlobalTaskPauseState();
});

afterEach(() => {
  for (const { app, host } of mounted.splice(0)) {
    app.unmount();
    host.remove();
  }
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("global task pause control", () => {
  it("shows paused state and the backend running task count", async () => {
    const request = vi.fn().mockResolvedValue({
      paused: true,
      running: 3,
      paused_count: 2,
      count: 5,
      error: "",
      changed: false,
    });
    await mount(request);

    expect(request).toHaveBeenCalledWith("tasks.status", {});
    expect(actionButton().textContent).toContain("继续操作");
    expect(actionButton().textContent).toContain("已暂停");
    expect(actionButton().textContent).toContain("运行任务 3");
    expect(actionButton().dataset.state).toBe("paused");
  });

  it("confirms pause, explains safe boundaries, and prevents duplicate submit", async () => {
    let releasePause;
    const pauseResult = new Promise((resolve) => {
      releasePause = resolve;
    });
    const request = vi.fn((action) => {
      if (action === "tasks.status") {
        return Promise.resolve({
          paused: false,
          running: 2,
          paused_count: 0,
          count: 2,
          error: "",
        });
      }
      if (action === "tasks.pause-all") return pauseResult;
      return Promise.reject(new Error(`unexpected ${action}`));
    });
    await mount(request);

    actionButton().click();
    await settle();
    const dialog = document.querySelector(".global-task-pause-dialog");
    expect(dialog).not.toBeNull();
    const mask = dialog.closest(".modal-mask");
    expect(mask.classList.contains("global-task-pause-mask")).toBe(true);
    expect(getComputedStyle(mask).zIndex).toBe("2400");
    expect(getComputedStyle(mask).backgroundColor).toBe(
      "rgba(10, 22, 34, 0.74)",
    );
    expect(getComputedStyle(dialog).backgroundColor).toBe(
      "rgb(255, 255, 255)",
    );
    expect(getComputedStyle(dialog).opacity).toBe("1");
    expect(dialog.textContent).toContain("当前请求会收尾");
    expect(dialog.textContent).toContain("不会强行中断在途平台写");
    expect(dialog.textContent).toContain("账号切换或退出登录仍可使用");
    expect(confirmButton().disabled).toBe(false);
    expect(dialog.querySelector(".global-task-pause-copy").textContent.trim())
      .not.toBe("");

    confirmButton().click();
    confirmButton().click();
    await settle();
    expect(
      request.mock.calls.filter(([action]) => action === "tasks.pause-all"),
    ).toHaveLength(1);

    releasePause({
      paused: true,
      running: 0,
      paused_count: 2,
      count: 2,
      error: "",
      changed: true,
    });
    await settle();

    expect(actionButton().textContent).toContain("继续操作");
    expect(actionButton().textContent).toContain("运行任务 0");
    expect(actionButton().dataset.state).toBe("paused");
  });

  it("keeps the prior state and reports an error when pause fails", async () => {
    const request = vi.fn((action) => {
      if (action === "tasks.status") {
        return Promise.resolve({
          paused: false,
          running: 1,
          paused_count: 0,
          count: 1,
          error: "",
        });
      }
      return Promise.reject(new Error("暂停写入失败"));
    });
    await mount(request);

    actionButton().click();
    await settle();
    confirmButton().click();
    await settle();

    expect(actionButton().dataset.state).toBe("active");
    expect(actionButton().textContent).toContain("暂停操作");
    expect(document.body.textContent).toContain("暂停写入失败");
  });

  it("resumes directly and reports resume failure without claiming success", async () => {
    const request = vi.fn((action) => {
      if (action === "tasks.status") {
        return Promise.resolve({
          paused: true,
          running: 2,
          paused_count: 2,
          count: 3,
          error: "",
        });
      }
      if (action === "tasks.resume-all") {
        return Promise.reject(new Error("恢复写入失败"));
      }
      return Promise.reject(new Error(`unexpected ${action}`));
    });
    await mount(request);

    actionButton().click();
    await settle();

    expect(request).toHaveBeenCalledWith("tasks.resume-all", {});
    expect(actionButton().dataset.state).toBe("paused");
    expect(actionButton().textContent).toContain("继续操作");
    expect(document.body.textContent).toContain("恢复写入失败");
  });

  it("resumes successfully without reopening the pause confirmation", async () => {
    const request = vi.fn((action) => {
      if (action === "tasks.status") {
        return Promise.resolve({
          paused: true,
          running: 2,
          paused_count: 2,
          count: 2,
          error: "",
        });
      }
      if (action === "tasks.resume-all") {
        return Promise.resolve({
          paused: false,
          running: 0,
          paused_count: 0,
          count: 2,
          error: "",
          changed: true,
        });
      }
      return Promise.reject(new Error(`unexpected ${action}`));
    });
    await mount(request);

    actionButton().click();
    await settle();

    expect(document.querySelector(".global-task-pause-dialog")).toBeNull();
    expect(request).toHaveBeenCalledWith("tasks.resume-all", {});
    expect(actionButton().dataset.state).toBe("active");
    expect(actionButton().textContent).toContain("暂停操作");
    expect(actionButton().textContent).toContain("运行任务 0");
  });

  it("marks failed status reads as unknown and allows retry", async () => {
    const request = vi.fn()
      .mockResolvedValueOnce({
        paused: null,
        running: null,
        count: null,
        error: "读取全局任务状态失败",
      })
      .mockResolvedValueOnce({
        paused: false,
        running: 0,
        paused_count: 0,
        count: 0,
        error: "",
      });
    await mount(request);

    expect(actionButton().dataset.state).toBe("unknown");
    expect(actionButton().textContent).toContain("状态未知");
    expect(document.body.textContent).toContain("读取全局任务状态失败");
    const retry = document.querySelector(
      '[data-testid="global-task-pause-retry"]',
    );
    expect(retry).not.toBeNull();

    retry.click();
    await settle();

    expect(request).toHaveBeenCalledTimes(2);
    expect(actionButton().dataset.state).toBe("active");
    expect(actionButton().textContent).toContain("暂停操作");
  });

  it("retains paused state across page/account remount until refresh completes", async () => {
    const oldRefresh = deferred();
    let statusCalls = 0;
    const request = vi.fn()
      .mockResolvedValueOnce({
        paused: true,
        running: 1,
        paused_count: 1,
        count: 1,
        error: "",
      })
      .mockImplementationOnce((action) => {
        if (action === "tasks.status") return oldRefresh.promise;
        return Promise.reject(new Error(`unexpected ${action}`));
      });
    const firstMount = await mountControl(request);
    statusCalls = request.mock.calls.length;
    const staleRefresh = firstMount.control.value.refreshStatus();
    expect(request).toHaveBeenCalledTimes(statusCalls + 1);
    expect(request.mock.calls.at(-1)[0]).toBe("tasks.status");
    firstMount.app.unmount();
    mounted.pop();
    document.body.innerHTML = "";

    let releaseStatus;
    const pendingStatus = new Promise((resolve) => {
      releaseStatus = resolve;
    });
    request.mockReturnValueOnce(pendingStatus);
    const host = document.createElement("div");
    document.body.append(host);
    const app = createApp({
      render: () => h(GlobalTaskPauseControl, { request }),
    });
    app.mount(host);
    mounted.push({ app, host });
    await nextTick();

    expect(actionButton().textContent).toContain("继续操作");
    expect(actionButton().textContent).toContain("已暂停");
    window.dispatchEvent(new CustomEvent("langlang-account-handoff"));
    await settle();
    expect(actionButton().textContent).toContain("继续操作");
    oldRefresh.resolve({
      paused: false,
      running: 0,
      paused_count: 0,
      count: 1,
      revision: 99,
      error: "",
    });
    await staleRefresh;
    await settle();
    expect(actionButton().textContent).toContain("继续操作");

    releaseStatus({
      paused: true,
      running: 0,
      paused_count: 1,
      count: 1,
      error: "",
    });
    await settle();
    expect(actionButton().dataset.state).toBe("paused");
  });

  it("does not let an old status override a successful pause", async () => {
    const slowStatus = deferred();
    let statusCalls = 0;
    const request = vi.fn((action) => {
      if (action === "tasks.status") {
        statusCalls += 1;
        return statusCalls === 1
          ? Promise.resolve({
            paused: false,
            running: 2,
            paused_count: 0,
            count: 2,
            revision: 1,
            error: "",
          })
          : slowStatus.promise;
      }
      if (action === "tasks.pause-all") {
        return Promise.resolve({
          paused: true,
          running: 0,
          paused_count: 2,
          count: 2,
          revision: 2,
          error: "",
          changed: true,
        });
      }
      return Promise.reject(new Error(`unexpected ${action}`));
    });
    const { control } = await mountControl(request);
    const staleRefresh = control.value.refreshStatus();

    actionButton().click();
    await settle();
    confirmButton().click();
    await settle();
    slowStatus.resolve({
      paused: false,
      running: 2,
      paused_count: 0,
      count: 2,
      revision: 1,
      error: "",
    });
    await staleRefresh;
    await settle();

    expect(actionButton().dataset.state).toBe("paused");
    expect(actionButton().textContent).toContain("运行任务 0");
    expect(globalTaskPauseState.revision).toBe(2);
  });

  it("does not let an old status override a successful resume", async () => {
    const slowStatus = deferred();
    let statusCalls = 0;
    const request = vi.fn((action) => {
      if (action === "tasks.status") {
        statusCalls += 1;
        return statusCalls === 1
          ? Promise.resolve({
            paused: true,
            running: 2,
            paused_count: 2,
            count: 2,
            revision: 1,
            error: "",
          })
          : slowStatus.promise;
      }
      if (action === "tasks.resume-all") {
        return Promise.resolve({
          paused: false,
          running: 0,
          paused_count: 0,
          count: 2,
          revision: 2,
          error: "",
          changed: true,
        });
      }
      return Promise.reject(new Error(`unexpected ${action}`));
    });
    const { control } = await mountControl(request);
    const staleRefresh = control.value.refreshStatus();

    actionButton().click();
    await settle();
    slowStatus.resolve({
      paused: true,
      running: 2,
      paused_count: 2,
      count: 2,
      revision: 1,
      error: "",
    });
    await staleRefresh;
    await settle();

    expect(actionButton().dataset.state).toBe("active");
    expect(actionButton().textContent).toContain("运行任务 0");
    expect(globalTaskPauseState.revision).toBe(2);
  });

  it("keeps a pause failure error when the pre-action status arrives late", async () => {
    const slowStatus = deferred();
    let statusCalls = 0;
    const request = vi.fn((action) => {
      if (action === "tasks.status") {
        statusCalls += 1;
        return statusCalls === 1
          ? Promise.resolve({
            paused: false,
            running: 2,
            paused_count: 0,
            count: 2,
            revision: 1,
            error: "",
          })
          : slowStatus.promise;
      }
      if (action === "tasks.pause-all") {
        return Promise.reject(new Error("暂停写入失败"));
      }
      return Promise.reject(new Error(`unexpected ${action}`));
    });
    const { control } = await mountControl(request);
    const staleRefresh = control.value.refreshStatus();

    actionButton().click();
    await settle();
    confirmButton().click();
    await settle();
    slowStatus.resolve({
      paused: false,
      running: 2,
      paused_count: 0,
      count: 2,
      revision: 1,
      error: "",
    });
    await staleRefresh;
    await settle();

    expect(actionButton().dataset.state).toBe("active");
    expect(document.body.textContent).toContain("暂停写入失败");
    expect(globalTaskPauseState.running).toBe(2);
    expect(actionButton().getAttribute("aria-busy")).toBe("false");
  });

  it("keeps a resume failure error when the pre-action status arrives late", async () => {
    const slowStatus = deferred();
    let statusCalls = 0;
    const request = vi.fn((action) => {
      if (action === "tasks.status") {
        statusCalls += 1;
        return statusCalls === 1
          ? Promise.resolve({
            paused: true,
            running: 1,
            paused_count: 1,
            count: 1,
            revision: 1,
            error: "",
          })
          : slowStatus.promise;
      }
      if (action === "tasks.resume-all") {
        return Promise.reject(new Error("恢复写入失败"));
      }
      return Promise.reject(new Error(`unexpected ${action}`));
    });
    const { control } = await mountControl(request);
    const staleRefresh = control.value.refreshStatus();

    actionButton().click();
    await settle();
    slowStatus.resolve({
      paused: true,
      running: 1,
      paused_count: 1,
      count: 1,
      revision: 1,
      error: "",
    });
    await staleRefresh;
    await settle();

    expect(actionButton().dataset.state).toBe("paused");
    expect(document.body.textContent).toContain("恢复写入失败");
    expect(globalTaskPauseState.running).toBe(1);
    expect(actionButton().getAttribute("aria-busy")).toBe("false");
  });
});
