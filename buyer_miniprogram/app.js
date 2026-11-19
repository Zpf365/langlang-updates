const config = require("./config");
const session = require("./utils/session");

App({
  globalData: {
    config,
    session: session.load(),
  },
  onLaunch() {
    if (!config.apiBaseUrl) {
      console.warn("buyer assistant API base URL is not configured");
    }
  },
});
