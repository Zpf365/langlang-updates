const KEY = "langlang_buyer_assistant_session";

function load() {
  return wx.getStorageSync(KEY) || null;
}

function save(value) {
  wx.setStorageSync(KEY, value);
}

function clear() {
  wx.removeStorageSync(KEY);
}

module.exports = { load, save, clear };
