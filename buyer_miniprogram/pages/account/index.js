const api = require("../../utils/api");
const session = require("../../utils/session");

Page({
  data: { identity: null },
  onShow() { this.setData({ identity: session.load()?.identity || null }); },
  unbind() {
    wx.showModal({
      title: "解除绑定",
      content: "确认解除此微信身份与电脑澜琅任务的绑定？",
      success: ({ confirm }) => {
        if (!confirm) return;
        api.request("unbind").then(() => {
          session.clear();
          wx.reLaunch({ url: "/pages/login/index" });
        }).catch((error) => {
          wx.showModal({ title: "解除失败", content: error.message, showCancel: false });
        });
      },
    });
  },
});
