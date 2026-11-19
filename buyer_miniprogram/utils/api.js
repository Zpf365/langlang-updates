const config = require("../config");
const session = require("./session");

function request(action, payload = {}, options = {}) {
  if (!config.apiBaseUrl || config.apiBaseUrl.includes("YOUR-PROJECT")) {
    return Promise.reject(new Error("买家助手后端地址尚未配置"));
  }
  const current = session.load();
  return new Promise((resolve, reject) => {
    wx.request({
      url: `${config.apiBaseUrl}/buyer-assistant-mini`,
      method: "POST",
      timeout: options.timeout || 20000,
      header: {
        "content-type": "application/json",
        "x-buyer-session": current?.session_token || "",
      },
      data: { action, ...payload },
      success(response) {
        if (response.statusCode >= 200 && response.statusCode < 300) {
          resolve(response.data);
        } else {
          reject(new Error(response.data?.error || "请求失败"));
        }
      },
      fail(error) {
        reject(new Error(error.errMsg || "网络请求失败"));
      },
    });
  });
}

module.exports = { request };
