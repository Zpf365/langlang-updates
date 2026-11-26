import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const source = readFileSync(
  new URL("./pages/StoresPage.vue", import.meta.url),
  "utf8",
);

describe("store batch import drawer contract", () => {
  it("submits the entered store name and group for manual create and edit", () => {
    expect(source).toContain("name,");
    expect(source).toContain("app_id: form.app_id.trim()");
    expect(source).toContain("group_name: form.group_name.trim()");
    expect(source).not.toContain("name: form.app_id");
    expect(source).not.toContain("group_name: selectedStore.value.group_name");
  });

  it("uses real local preview and commit RPCs without native dialogs", () => {
    expect(source).toContain('call("stores.batch.preview"');
    expect(source).toContain('call("stores.batch.commit"');
    expect(source).toContain('call("stores.credentials.detail"');
    expect(source).toContain('call("stores.credentials.probe"');
    expect(source).not.toContain("window.confirm");
    expect(source).not.toContain("window.alert");
  });

  it("shows all six local outcomes and never renders a raw secret", () => {
    for (const status of [
      "new",
      "skip",
      "replace_candidate",
      "invalid",
      "duplicate",
      "name_conflict",
    ]) {
      expect(source).toContain(status);
    }
    expect(source).toContain("secret_masked");
    expect(source).toContain("fingerprint_prefix");
    expect(source).toContain("未访问平台");
  });

  it("keeps logged and unlogged store counts live during login", () => {
    expect(source).toContain("startRepeatingSync");
    expect(source).toContain("beginLiveStoreSync");
    expect(source).toContain('call("stores.list", { search: "" })');
    expect(source).toContain("登录进行中：已登录");
    expect(source).toContain("storeActionBusy");
    expect(source).toContain("not_logged_in_store_ids");
    expect(source).toContain("missing_store_ids");
    expect(source).toContain("正在处理店铺 ${index + 1}/${storeIds.length}");
    expect(source).toContain("店铺操作完成：成功");
  });

  it("does not place credential status labels under the store name", () => {
    expect(source).not.toContain(">密钥可用</small>");
    expect(source).not.toContain(">密钥失效</small>");
  });
});
