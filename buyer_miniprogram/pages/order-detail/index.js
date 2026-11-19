Page({
  onLoad(options) { this.orderId = options.id; },
  open() {
    wx.openStoreOrderDetail({
      orderId: this.orderId,
      fail(error) {
        wx.showModal({
          title: "无法打开订单",
          content: `${error.errMsg || "订单详情不可用"}（${error.code || ""}）`,
          showCancel: false,
        });
      },
    });
  },
});
