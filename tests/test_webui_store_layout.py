from __future__ import annotations

from pathlib import Path


def test_store_table_uses_reference_row_density_and_internal_scroll() -> None:
    source = (
        Path(__file__).parents[1] / "webui" / "src" / "pages" / "StoresPage.vue"
    ).read_text(encoding="utf-8")

    assert ".store-page {\n  height: 100%;\n  min-height: 0;" in source
    assert "overflow: hidden;\n  background: #fff;" in source
    assert ".store-table-card > .table-scroll {\n  flex: 1 1 auto;\n  min-height: 0;\n  overflow: auto;" in source
    assert ".store-table td {\n  height: 44px;" in source
    assert '<td class="app-id-cell">' in source
    assert "maskAppId(store.app_id)" in source
    assert ':title="store.app_id"' not in source
    assert "trimHint(store.app_id)" not in source
    assert ".secret-reveal {\n  width: 100%;\n  min-width: 0;\n  display: flex;" in source
    assert ".secret-placeholder {\n  display: block;\n  min-width: 0;\n  overflow: hidden;" in source
    assert "text-overflow: ellipsis" in source
    assert 'from "../storeScores";' in source
    assert "scorePresentation(store, key)" in source
    score_source = (
        Path(__file__).parents[1] / "webui" / "src" / "storeScores.js"
    ).read_text(encoding="utf-8")
    assert "value !== null" in score_source
    assert "delta !== 0" in score_source
    assert 'changeText = delta > 0 ? "↑" : "↓"' in score_source
    assert "较昨日" in score_source
    assert 'direction = delta > 0 ? "up" : "down"' in score_source
    assert 'groupFilter.value = "全部店铺"' in source
    assert "已登录店铺" in source
    assert "未登录店铺" in source
    assert 'placeholder="输入店铺名称"' in source
    assert 'const groupFilter = ref("全部店铺")' in source
    assert 'if (groupFilter.value !== "全部店铺" && store.group_name !== groupFilter.value) return false;' in source
    assert 'class="store-group-row" aria-label="店铺分组筛选"' in source
    assert 'v-for="group in storeGroups"' in source
    assert "storeGroups" in source
    assert "@click=\"setGroupFilter(group)\"" in source
    assert 'selectedStoreIds.length ? `刷新选中 (${selectedStoreIds.length})` : "刷新全部店铺"' in source
    assert 'store_ids: storeIds.length ? storeIds : undefined' in source
    assert 'loading\n              ? "刷新中..."' in source
    assert 'class="store-refresh-progress"' in source
    assert "refreshProgress.current" in source
    assert "refreshProgress.total" in source
    assert 'v-if="storeViewMode === STORE_VIEW_OVERVIEW"' in source
    assert 'class="table-scroll overview-table-scroll"' in source
    assert ".overview-table-scroll {\n   width: 100%;\n   min-width: 0;\n   overflow-x: hidden;" in source
    assert "销售 / 退款（金额 / 单量）" in source
    assert ":aria-expanded=\"isStoreExpanded(store.id)\"" in source
    assert '<th rowspan="2">AppSecret</th>' in source
    secret_header = source.index('<th rowspan="2">AppSecret</th>')
    login_header = source.index('<th rowspan="2">登录状态</th>')
    operation_header = source.index('<th rowspan="2">操作</th>', login_header)
    assert secret_header < login_header < operation_header


def test_global_buttons_have_pressed_disabled_and_dynamic_states() -> None:
    root = Path(__file__).parents[1]
    styles = (root / "webui" / "src" / "styles.css").read_text(encoding="utf-8")
    app_source = (root / "webui" / "src" / "App.vue").read_text(encoding="utf-8")
    upload_source = (
        root / "webui" / "src" / "pages" / "ProductUploadParityPage.vue"
    ).read_text(encoding="utf-8")

    assert "button:disabled" in styles
    assert "opacity: 0.46" in styles
    assert "button.button-feedback" in styles
    assert "@keyframes button-feedback" in styles
    assert '"pointerdown", showButtonFeedback, true' in app_source
    assert ':disabled="!runnableTaskIds.length || activeRun"' in upload_source
    assert 'runnableTaskIds.length ? `开始上传（${runnableTaskIds.length}）`' in upload_source
    assert ':checked="allFilteredBatchSelected"' in upload_source
    assert ':class="{ selected: selectedBatchIds.includes(item.batch_id) }"' in upload_source
    assert 'call("upload.run.pause"' in upload_source
    assert 'call("upload.run.cancel"' in upload_source
    assert 'call("upload.tasks.retry"' in upload_source
    assert "active_run_job" in upload_source
    assert "applyUploadJob(activeJob)" in upload_source
    assert "当前上传 ·" in upload_source
    assert "retryableItemCount" in upload_source
    assert "product_image_upload" in upload_source
    assert "preflightMappingText" in upload_source
    assert "类目自动匹配" in upload_source


def test_route_pages_are_kept_alive_between_navigation() -> None:
    source = (
        Path(__file__).parents[1] / "webui" / "src" / "App.vue"
    ).read_text(encoding="utf-8")

    assert "<KeepAlive>" in source
    assert '<component :is="Component" />' in source


def test_order_quick_refresh_calls_network_job_and_shows_feedback() -> None:
    source = (
        Path(__file__).parents[1] / "webui" / "src" / "pages" / "OrdersParityPage.vue"
    ).read_text(encoding="utf-8")

    assert 'call("orders.quick-refresh.start"' in source
    assert 'call("orders.quick-refresh.status"' in source
    assert 'quickRefreshing ? "刷新中..." : "快速刷新"' in source
    assert 'class="refresh-feedback"' in source
    assert "quickRefresh.current" in source
    assert "quickRefreshPercent" in source
    assert "@click=\"load\">快速刷新" not in source
    assert "min-width: 1900px;" in source
    assert ".order-table-a .col-order { width: 232px; }" in source
    assert ".order-table-a .col-money { width: 140px; }" in source
    assert ".order-table-a .col-purchase { width: 238px; }" in source
    assert ".order-table-a .col-fulfill { width: 154px; }" in source
    assert "class=\"order-horizontal-range\"" in source
    assert "@input=\"moveHorizontal($event.target.value)\"" in source
    assert "position: sticky;" in source
    assert 'title="标记旗帜与备注"' in source
    assert 'orders.update-note-state' in source
    assert "const hasOfficialState = (" in source
    assert "order.official_note_color_code !== undefined" in source
    assert 'noteScope.value = hasOfficialState ? "店铺备注" : "软件备注";' in source
    assert "v-for=\"flag in ['红', '橙', '绿', '蓝']\"" in source
    assert ">无色</button>" in source
    assert "默认仅保存在澜琅" in source
    assert 'title="同步店铺备注"' in source
    assert "storeSyncConfirmOpen" in source
    assert "<OrderTableRow" in source
    assert "<OrderWorkbenchPanel" not in source
    row_source = (
        Path(__file__).parents[1]
        / "webui"
        / "src"
        / "components"
        / "OrderTableRow.vue"
    ).read_text(encoding="utf-8")
    assert "orders.workbench.detail" in row_source
    assert "height: 132px;" in row_source
    assert "grid-template-columns: 76px minmax(0, 1fr);" in row_source
    assert "position: sticky" in row_source


def test_order_workbench_exposes_real_actions_and_feedback_states() -> None:
    root = Path(__file__).parents[1]
    source = (
        root / "webui" / "src" / "components" / "OrderWorkbenchPanel.vue"
    ).read_text(encoding="utf-8")
    profit_source = (
        root / "webui" / "src" / "components" / "ProfitReportPanel.vue"
    ).read_text(encoding="utf-8")
    order_source = (
        root / "webui" / "src" / "pages" / "OrdersParityPage.vue"
    ).read_text(encoding="utf-8")
    procurement_source = (
        root / "webui" / "src" / "pages" / "ProcurementPage.vue"
    ).read_text(encoding="utf-8")

    for endpoint in (
        "orders.workbench.detail",
        "orders.workbench.manual-purchase",
        "orders.workbench.purchase-focus",
        "orders.workbench.browse-procurement",
        "orders.workbench.sync-procurement",
        "orders.workbench.sync-procurement-note",
        "orders.workbench.address-update",
        "orders.workbench.price-update",
        "orders.workbench.delivery-update",
        "orders.workbench.open-official",
        "orders.after-sales.sync",
        "orders.after-sales.action",
        "orders.transfer-cases.list",
        "orders.transfer-cases.create",
        "orders.transfer-cases.update",
        "orders.workbench.messages",
        "orders.workbench.logs",
    ):
        assert endpoint in source
    assert "actionBusy === \"刷新订单\" ? \"刷新中...\" : \"刷新订单\"" in source
    assert 'class="wb-feedback"' in source
    assert "console" not in source.lower()
    assert "window.confirm" not in (
        source + profit_source + order_source + procurement_source
    )
    assert "orders.profit.report" in profit_source
    assert "库存" not in profit_source
    assert "data-range-warning" in order_source
    assert 'call("orders.list", { limit: 100000 })' in order_source


def test_order_number_opens_the_official_order_page() -> None:
    root = Path(__file__).parents[1]
    source = (
        root / "webui" / "src" / "pages" / "OrdersParityPage.vue"
    ).read_text(encoding="utf-8")
    row_source = (
        root / "webui" / "src" / "components" / "OrderTableRow.vue"
    ).read_text(encoding="utf-8")

    assert "async function openOfficialOrder(order)" in source
    assert 'class="order-link"' in row_source
    assert 'call("orders.workbench.open-official"' in source


def test_order_workbench_is_removed_from_primary_navigation() -> None:
    root = Path(__file__).parents[1]
    controller = (
        root / "weimi_manager" / "webui" / "controller.py"
    ).read_text(encoding="utf-8")
    more_page = (
        root / "webui" / "src" / "pages" / "MorePage.vue"
    ).read_text(encoding="utf-8")

    assert '("workbench", "订单工作台"' not in controller
    assert '["workbench", "订单工作台"' not in more_page
    assert '"/orders?after_sales=1"' in controller
    assert '"/orders?after_sales=1"' in more_page


def test_product_page_uses_reference_density_and_section_sizes() -> None:
    root = Path(__file__).parents[1]
    source = (
        root / "webui" / "src" / "pages" / "ProductsPage.vue"
    ).read_text(encoding="utf-8")
    desktop = (root / "weimi_manager" / "webui" / "desktop.py").read_text(
        encoding="utf-8"
    )
    styles = (root / "webui" / "src" / "styles.css").read_text(encoding="utf-8")

    assert 'class="page-header card product-title-card"' in source
    assert "grid-template-columns: minmax(0, 1fr) 426px;" in source
    assert "width: 56px;\n  height: 56px;" in source
    assert "height: 78px;" in source
    assert "window.resize(1780, 1100)" in desktop
    assert "--sidebar-width: 190px;" in styles
