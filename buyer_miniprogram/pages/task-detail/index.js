const api = require("../../utils/api");
const config = require("../../config");

Page({
  data: { task: null, items: [], index: 0, current: null, shopAppId: config.shopAppId },
  onLoad(options) {
    api.request("list_tasks").then(({ tasks }) => {
      const task = (tasks || []).find((item) => item.assistant_task_id === options.id);
      if (!task) throw new Error("任务不存在");
      const items = task.buyer_assistant_task_items || [];
      this.setData({ task, items, index: 0, current: items[0] || null });
    }).catch((error) => {
      wx.showModal({ title: "任务详情失败", content: error.message, showCancel: false });
    });
  },
  identity() { wx.navigateTo({ url: "/pages/identity/index" }); },
  next() {
    const next = Math.min(this.data.items.length - 1, this.data.index + 1);
    this.setData({ index: next, current: this.data.items[next] || null });
  },
  previous() {
    const next = Math.max(0, this.data.index - 1);
    this.setData({ index: next, current: this.data.items[next] || null });
  },
  confirmItem() {
    const current = this.data.current;
    if (!current) return;
    api.request("item_action", {
      assistant_item_id: current.assistant_item_id,
      status: "user_confirmed",
    }).then(() => {
      const items = this.data.items.slice();
      items[this.data.index] = { ...current, status: "user_confirmed" };
      this.setData({ items });
      wx.showToast({ title: "已记录用户确认", icon: "success" });
    }).catch((error) => {
      wx.showModal({ title: "确认失败", content: error.message, showCancel: false });
    });
  },
  pause() { this.taskAction("paused"); },
  resume() { this.taskAction("running"); },
  cancel() { this.taskAction("cancelled"); },
  awaitingOrder() { this.taskAction("awaiting_order"); },
  taskAction(status) {
    if (!this.data.task) return;
    api.request("task_action", {
      assistant_task_id: this.data.task.assistant_task_id,
      status,
    }).then(() => {
      this.setData({ "task.status": status });
      wx.showToast({ title: "任务状态已更新", icon: "success" });
    }).catch((error) => {
      wx.showModal({ title: "任务操作失败", content: error.message, showCancel: false });
    });
  },
  progress() {
    wx.navigateTo({
      url: `/pages/progress/index?id=${this.data.task.assistant_task_id}`,
    });
  },
});
