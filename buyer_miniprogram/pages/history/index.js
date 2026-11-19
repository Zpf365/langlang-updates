const api = require("../../utils/api");

Page({
  data: { events: [] },
  onShow() {
    api.request("history").then((result) => this.setData({ events: result.events || [] }));
  },
});
