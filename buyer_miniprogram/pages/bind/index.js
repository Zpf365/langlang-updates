const api = require("../../utils/api");
const session = require("../../utils/session");

Page({
  data: {
    ticket: "",
    loading: false,
    claimed: false,
    identityHint: "",
    displayName: "微信买家",
  },
  onLoad(options) {
    const ticket = decodeURIComponent(options.ticket || options.scene || "");
    this.setData({ ticket });
    if (ticket) this.exchange();
  },
  exchange() {
    this.setData({ loading: true });
    wx.login({
      success: async ({ code }) => {
        try {
          const result = await api.request("exchange_code", {
            ticket: this.data.ticket,
            code,
          });
          session.save({ ...result, ticket: this.data.ticket });
          this.setData({
            claimed: true,
            identityHint: `微信身份尾号 ${result.identity.openid_tail}`,
          });
        } catch (error) {
          wx.showModal({ title: "绑定失败", content: error.message, showCancel: false });
        } finally {
          this.setData({ loading: false });
        }
      },
      fail() {
        wx.showToast({ title: "微信登录失败", icon: "none" });
      },
    });
  },
  onNameInput(event) {
    this.setData({ displayName: event.detail.value });
  },
  confirm() {
    this.setData({ loading: true });
    api.request("confirm_binding", {
      display_name: this.data.displayName,
      device_label: "微信小程序",
    }).then((result) => {
      const current = session.load();
      session.save({ ...current, identity: result });
      wx.showToast({ title: "绑定成功", icon: "success" });
      wx.redirectTo({ url: "/pages/identity/index" });
    }).catch((error) => {
      wx.showModal({ title: "确认失败", content: error.message, showCancel: false });
    }).finally(() => this.setData({ loading: false }));
  },
});
