import { createRouter, createWebHashHistory } from "vue-router";
import {
  clearOperationsAccess,
  ensureBootstrap,
  hasEntitlement,
  hasOperationsPermission,
} from "./session";

const routes = [
  { path: "/", redirect: "/shops" },
  { path: "/shops", component: () => import("./pages/StoresPage.vue") },
  {
    path: "/orders",
    component: () => import("./pages/OrdersParityPage.vue"),
    meta: { queryEntitlements: { after_sales: "module.after_sales" } },
  },
  { path: "/transit", component: () => import("./pages/TransitPage.vue") },
  { path: "/logistics", component: () => import("./pages/LogisticsPage.vue") },
  { path: "/statistics", component: () => import("./pages/StatisticsPage.vue") },
  { path: "/managed-products", component: () => import("./pages/ProductsPage.vue") },
  { path: "/product-upload", component: () => import("./pages/ProductUploadParityPage.vue") },
  { path: "/supply-market", component: () => import("./pages/SupplyMarketParityPage.vue") },
  { path: "/hosted", component: () => import("./pages/HostedPage.vue") },
  { path: "/activity", component: () => import("./pages/ActivityPage.vue") },
  {
    path: "/automation",
    component: () => import("./pages/AutomationPage.vue"),
    meta: { entitlement: "module.automation" },
  },
  {
    path: "/finance",
    component: () => import("./pages/FinancePage.vue"),
    meta: { entitlement: "module.finance" },
  },
  {
    path: "/storefront",
    component: () => import("./pages/StorefrontPage.vue"),
    meta: { entitlement: "module.storefront" },
  },
  { path: "/violations", component: () => import("./pages/ViolationsParityPage.vue") },
  {
    path: "/procurement-accounts",
    component: () => import("./pages/ProcurementPage.vue"),
    meta: { entitlement: "feature.procurement_association" },
  },
  { path: "/customer-service", component: () => import("./pages/CustomerServicePage.vue") },
  {
    path: "/operations/:section?",
    component: () => import("./pages/OperationsPage.vue"),
    meta: { permission: "operations_view" },
  },
  {
    path: "/more/team",
    component: () => import("./pages/TeamPermissionsPage.vue"),
    meta: { entitlement: "module.team" },
  },
  {
    path: "/more/delivery-ledger",
    component: () => import("./pages/DeliveryLedgerPage.vue"),
    meta: { entitlement: "module.delivery_ledger" },
  },
  {
    path: "/more/:section?",
    component: () => import("./pages/MorePage.vue"),
    meta: { entitlement: "module.runtime_logs" },
  },
  { path: "/:pathMatch(.*)*", redirect: "/shops" },
];

const router = createRouter({
  history: createWebHashHistory(),
  routes,
});

router.beforeEach(async (to) => {
  const queryEntitlements = to.meta.queryEntitlements || {};
  const requiredQueryEntitlements = Object.entries(queryEntitlements)
    .filter(([queryKey, _entitlement]) => String(to.query[queryKey] || "") === "1");
  if (
    !to.meta.permission
    && !to.meta.entitlement
    && !requiredQueryEntitlements.length
  ) return true;
  try {
    await ensureBootstrap();
  } catch {
    return "/shops";
  }
  if (
    to.meta.permission === "operations_view" &&
    !hasOperationsPermission("operations_view")
  ) {
    clearOperationsAccess();
    return "/shops";
  }
  if (
    to.meta.entitlement
    && !hasEntitlement(to.meta.entitlement)
  ) {
    return "/shops";
  }
  if (
    requiredQueryEntitlements.some(
      ([, entitlement]) => !hasEntitlement(entitlement),
    )
  ) {
    return {
      path: to.path,
      query: Object.fromEntries(
        Object.entries(to.query).filter(
          ([key]) => key !== "after_sales",
        ),
      ),
    };
  }
  return true;
});

export default router;
