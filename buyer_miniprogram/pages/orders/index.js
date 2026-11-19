const api = require("../../utils/api");

Page({
  data: { orders: [] },
  onShow() {
    api.request("orders").then((result) => this.setData({ orders: result.orders || [] }));
  },
  detail(event) {
    wx.navigateTo({
      url: `/pages/order-detail/index?id=${event.currentTarget.dataset.id}`,
    });
  },
});
