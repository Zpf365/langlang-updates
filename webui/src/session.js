import { reactive } from "vue";
import { call } from "./api";

export const sessionState = reactive({
  loaded: false,
  loading: false,
  error: "",
  bootstrap: null,
  memberRole: "",
  permissions: {},
  entitlements: {},
  operationsPermissions: {},
});

let bootstrapPromise = null;

function applyBootstrap(payload) {
  sessionState.bootstrap = payload;
  sessionState.memberRole = String(payload?.member_role || "");
  sessionState.permissions = {
    ...(payload?.permissions || {}),
  };
  sessionState.entitlements = {
    ...(payload?.entitlements || payload?.capabilities || {}),
  };
  sessionState.operationsPermissions = {
    ...(payload?.operations_permissions || {}),
  };
  sessionState.loaded = true;
  sessionState.error = "";
}

export async function ensureBootstrap({ force = false } = {}) {
  if (!force && sessionState.loaded) return sessionState.bootstrap;
  if (!force && bootstrapPromise) return bootstrapPromise;
  sessionState.loading = true;
  bootstrapPromise = call("bootstrap")
    .then((payload) => {
      applyBootstrap(payload);
      return payload;
    })
    .catch((cause) => {
      sessionState.error = cause instanceof Error ? cause.message : String(cause);
      throw cause;
    })
    .finally(() => {
      sessionState.loading = false;
      bootstrapPromise = null;
    });
  return bootstrapPromise;
}

export function clearOperationsAccess() {
  if (!sessionState.bootstrap) return;
  sessionState.memberRole = "";
  sessionState.operationsPermissions = {
    operations_view: false,
    operations_export: false,
    operations_task_manage: false,
    operations_rule_manage: false,
    operations_open_backend: false,
  };
  sessionState.bootstrap = {
    ...sessionState.bootstrap,
    more_nav: (sessionState.bootstrap.more_nav || []).filter(
      (item) => item.key !== "operations",
    ),
    operations_permissions: sessionState.operationsPermissions,
  };
}

export function hasPermission(key) {
  return Boolean(sessionState.permissions?.[key]);
}

export function hasEntitlement(key) {
  return Boolean(sessionState.entitlements?.[key]);
}

export function hasOperationsPermission(key = "operations_view") {
  return (
    ["owner", "admin"].includes(sessionState.memberRole) ||
    Boolean(sessionState.operationsPermissions?.[key])
  );
}

export function resetSessionState() {
  bootstrapPromise = null;
  sessionState.loaded = false;
  sessionState.loading = false;
  sessionState.error = "";
  sessionState.bootstrap = null;
  sessionState.memberRole = "";
  sessionState.permissions = {};
  sessionState.entitlements = {};
  sessionState.operationsPermissions = {};
}
