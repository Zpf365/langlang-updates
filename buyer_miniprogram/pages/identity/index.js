const session = require("../../utils/session");

Page({
  data: { identity: null },
  onShow() {
    this.setData({ identity: session.load()?.identity || null });
  },
  tasks() {
    wx.navigateTo({ url: "/pages/tasks/index" });
  },
  account() {
    wx.navigateTo({ url: "/pages/account/index" });
  },
});
