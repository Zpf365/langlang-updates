const api = require("../../utils/api");
const config = require("../../config");

Page({
  data: { tasks: [], shopAppId: config.shopAppId, loading: false },
  onShow() { this.load(); },
  load() {
    this.setData({ loading: true });
    api.request("list_tasks").then((result) => {
      this.setData({ tasks: result.tasks || [] });
    }).catch((error) => {
      wx.showModal({ title: "任务读取失败", content: error.message, showCancel: false });
    }).finally(() => this.setData({ loading: false }));
  },
  openTask(event) {
    wx.navigateTo({
      url: `/pages/task-detail/index?id=${event.currentTarget.dataset.id}`,
    });
  },
  history() { wx.navigateTo({ url: "/pages/history/index" }); },
  orders() { wx.navigateTo({ url: "/pages/orders/index" }); },
});
