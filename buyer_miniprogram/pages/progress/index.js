const api = require("../../utils/api");

Page({
  data: { events: [] },
  onLoad(options) {
    this.id = options.id;
    this.load();
  },
  load() {
    api.request("history").then((result) => {
      const events = (result.events || []).filter((item) => item.assistant_task_id === this.id);
      this.setData({ events });
    });
  },
});
