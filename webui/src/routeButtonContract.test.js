import { existsSync, readdirSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const root = new URL("./", import.meta.url);
const router = readFileSync(new URL("./router.js", root), "utf8");
const pages = [
  ...new Set(
    [...router.matchAll(/import\("\.\/pages\/([^"?]+)/g)].map(
      (match) => match[1],
    ),
  ),
];
const componentNames = readdirSync(new URL("./components", root))
  .filter((name) => name.endsWith(".vue"));
const reachableComponents = new Set();
const surfaceQueue = pages.map((page) => `pages/${page}`);
const visitedSurfaces = new Set();
while (surfaceQueue.length) {
  const surface = surfaceQueue.pop();
  if (visitedSurfaces.has(surface)) continue;
  visitedSurfaces.add(surface);
  const source = readFileSync(new URL(`./${surface}`, root), "utf8");
  for (const match of source.matchAll(/from\s+["'][^"']*\/([^/"']+\.vue)["']/g)) {
    const name = match[1];
    if (!componentNames.includes(name)) continue;
    const component = `components/${name}`;
    reachableComponents.add(component);
    surfaceQueue.push(component);
  }
}
const surfaces = [
  "App.vue",
  ...pages.map((page) => `pages/${page}`),
  ...reachableComponents,
];

describe("actual route page button contract", () => {
  it("has no explanatory action placeholders or native dialogs", () => {
    for (const surface of surfaces) {
      const source = readFileSync(new URL(`./${surface}`, root), "utf8");
      expect(source, surface).not.toContain('@click="action(');
      expect(source, surface).not.toContain("function action(");
      expect(source, surface).not.toContain("window.confirm");
      expect(source, surface).not.toContain("window.alert");
      expect(source, surface).not.toContain("入口已建立");
    }
    expect(existsSync(new URL("./pages/UploadPage.vue", root))).toBe(false);
    expect(existsSync(new URL("./pages/SupplyMarketPage.vue", root))).toBe(false);
    expect(existsSync(new URL("./pages/ViolationsPage.vue", root))).toBe(false);
  });

  it("gives every button a handler or a disabled reason", () => {
    for (const surface of surfaces) {
      const source = readFileSync(new URL(`./${surface}`, root), "utf8");
      const buttons = source.match(/<button\b[\s\S]*?<\/button>/gi) || [];
      for (const button of buttons) {
        if (button.includes("@click")) continue;
        const safelyDisabled =
          (button.includes("disabled") || button.includes(":disabled")) &&
          (button.includes("title=") || button.includes(":title="));
        expect(safelyDisabled, `${surface}: ${button.slice(0, 140)}`).toBe(true);
      }
    }
  });

  it("keeps unmounted component files out of the actual-page coverage claim", () => {
    const orphanComponents = componentNames
      .map((name) => `components/${name}`)
      .filter((name) => !reachableComponents.has(name));
    expect(orphanComponents).toEqual(["components/OrderWorkbenchPanel.vue"]);
  });
});
