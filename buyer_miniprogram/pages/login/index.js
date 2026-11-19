const session = require("../../utils/session");

Page({
  data: { ticket: "", message: "先使用电脑澜琅生成一次性绑定票据。" },
  onLoad(options) {
    const ticket = decodeURIComponent(options.ticket || options.scene || "");
    this.setData({ ticket });
    if (session.load()?.session_token) {
      wx.redirectTo({ url: "/pages/tasks/index" });
    }
  },
  scan() {
    wx.scanCode({
      success: (result) => {
        const ticket = String(result.result || "").replace(/^ticket=/, "");
        wx.navigateTo({ url: `/pages/bind/index?ticket=${encodeURIComponent(ticket)}` });
      },
    });
  },
  continueBind() {
    if (!this.data.ticket) {
      wx.showToast({ title: "请先扫描电脑绑定码", icon: "none" });
      return;
    }
    wx.navigateTo({
      url: `/pages/bind/index?ticket=${encodeURIComponent(this.data.ticket)}`,
    });
  },
});
