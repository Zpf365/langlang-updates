from __future__ import annotations

import json
import threading
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
)

from ...db import Database
from ...models import Store
from ...shop_private import ShopPrivateClient
from ...ui.upload_page import (
    CategoryApprovalPending,
    CategoryAutoOpenError,
    TemplateRuleSkip,
    UploadCancelled,
    UploadPaused,
    _category_checks_all,
    _category_checks_from_options,
    _category_deposit_snapshot,
    _category_hard_failure,
    _category_hard_state,
    _category_pending_state,
    _category_status_labels,
    _definitive_upload_failure_message,
    _is_duplicate_upload,
    _json_rows,
    _normalize_duplicate_filter_mode,
    _normalize_listing_mode,
    _recover_upload_tasks,
    _required_category_ids,
    _retry_upload_after_category_open,
)
from ...ui.upload_page import (
    UploadPage as LegacyUploadPage,
)
from ..widgets import DataTable, PrimaryButton
from .common import TaskPage
from .dialogs import PayloadDialog


def _category_client_with_business_type(
    database: Database,
    store: Store,
    business_type: str,
) -> ShopPrivateClient:
    client = ShopPrivateClient(database, store)
    client.category_business_type = business_type
    return client


class _PayloadAdapter:
    def __init__(self, database: Database) -> None:
        self.db = database
        self._category_catalog_cache: dict[int, list[dict[str, Any]]] = {}


class UploadRunner:
    """Runs persisted 1.6 upload tasks without depending on Tk widgets."""

    def __init__(
        self,
        workspace,
        tasks: list[dict[str, Any]],
        *,
        progress: Callable[[str, int, int], None] | None = None,
        external_stop_requested: Callable[[], bool] | None = None,
        start_guard: Callable[[], bool] | None = None,
    ) -> None:
        self.workspace = workspace
        self.db = workspace.db
        self.service = workspace.service
        self.tasks = tasks
        self.lock = threading.RLock()
        self.paused = threading.Event()
        self.cancelled = threading.Event()
        self.progress = progress
        self.external_stop_requested = external_stop_requested
        self.start_guard = start_guard
        self._jobs_total = 0
        self._jobs_completed = 0
        self.adapter = _PayloadAdapter(self.db)

    def run(
        self,
        selected: list[dict[str, Any]],
        *,
        selected_item_ids: set[str] | None = None,
    ) -> list[str]:
        _recover_upload_tasks(self.tasks)
        self._save()
        stores = {store.id: store for store in self.db.list_stores()}
        errors: list[str] = []
        jobs_by_store: dict[int, list[tuple[dict, dict]]] = defaultdict(list)
        completed_sources: dict[int, set[str]] = defaultdict(set)
        completed_computer: set[str] = set()
        for task in selected:
            store_id = int(task.get("store_id") or 0)
            if store_id not in stores:
                task["status"] = "failed"
                task["message"] = "店铺不存在"
                continue
            completed_sources[store_id].update(self.db.uploaded_source_ids(store_id))
            completed_computer.update(self.db.uploaded_source_ids())
            task["status"] = "running"
            task["message"] = "正在上传"
            mode = _normalize_duplicate_filter_mode(task.get("template") or {})
            for item in task.get("items") or []:
                if (
                    selected_item_ids
                    and str(item.get("item_id") or "") not in selected_item_ids
                ):
                    continue
                if item.get("status") in {
                    "success",
                    "verification_pending",
                }:
                    continue
                source_id = str(item.get("source_product_id") or "")
                if mode != "off" and _is_duplicate_upload(
                    mode,
                    source_id,
                    store_id,
                    completed_sources,
                    completed_computer,
                ):
                    item["status"] = "skipped"
                    item["message"] = "重复过滤命中"
                    continue
                if source_id:
                    completed_sources[store_id].add(source_id)
                    completed_computer.add(source_id)
                item["status"] = "pending"
                item["message"] = "等待上传"
                jobs_by_store[store_id].append((task, item))
        self._jobs_total = sum(len(jobs) for jobs in jobs_by_store.values())
        self._jobs_completed = 0
        self._save()

        workers = max(1, min(8, len(jobs_by_store)))
        with ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="langlang-pyside-upload",
        ) as executor:
            futures = {
                executor.submit(
                    self._run_store,
                    stores[store_id],
                    jobs,
                    errors,
                ): store_id
                for store_id, jobs in jobs_by_store.items()
            }
            for future in as_completed(futures):
                future.result()
        with self.lock:
            for task in selected:
                self._refresh_task_status(task)
        self._save()
        return errors

    def _run_store(
        self,
        store: Store,
        jobs: list[tuple[dict, dict]],
        errors: list[str],
    ) -> None:
        category_refresh: dict[str, Any] = {
            "attempted": False,
            "options": None,
            "error": None,
        }
        catalog_refresh = getattr(
            self.service,
            "upload_category_options",
            None,
        )

        def stop_requested() -> str | None:
            if self.cancelled.is_set():
                return "cancelled"
            if self.paused.is_set():
                return "paused"
            if (
                callable(self.external_stop_requested)
                and self.external_stop_requested()
            ):
                return "external"
            return None

        refresh_categories = None
        if callable(catalog_refresh):
            def refresh_categories() -> dict[str, Any]:
                if not category_refresh["attempted"]:
                    category_refresh["attempted"] = True
                    try:
                        refreshed = catalog_refresh(
                            store,
                            refresh=True,
                        )
                        category_refresh["options"] = (
                            refreshed
                            if isinstance(refreshed, dict)
                            else {}
                        )
                    except Exception as exc:
                        category_refresh["error"] = exc
                error = category_refresh["error"]
                if isinstance(error, Exception):
                    raise error
                options = category_refresh["options"]
                return options if isinstance(options, dict) else {}

        for task, item in jobs:
            if self.cancelled.is_set():
                item["status"] = "cancelled"
                item["message"] = "任务已取消，未提交"
                continue
            if self.paused.is_set():
                item["status"] = "paused"
                item["message"] = "等待继续"
                continue
            if self.start_guard is not None and not self.start_guard():
                item["status"] = "paused"
                item["message"] = "全局任务状态已变化，等待人工恢复"
                continue
            template = task.get("template") or {}
            category_business_type = str(
                template.get("category_business_type") or "individual"
            )
            if category_business_type not in {
                "individual",
                "enterprise",
            }:
                category_business_type = "individual"
            try:
                payload = LegacyUploadPage._upload_payload(
                    self.adapter,
                    item,
                    template,
                )
                if payload.pop("_auto_category_required", False):
                    payload = LegacyUploadPage._auto_match_category(
                        self.adapter,
                        store,
                        payload,
                        template,
                    )
                selection = payload.get("auto_category")
                if isinstance(selection, dict):
                    item["category_selection"] = {
                        key: selection.get(key)
                        for key in (
                            "category_id",
                            "path",
                            "score",
                            "selection_source",
                            "application_state",
                            "deposit_amount",
                            "required_deposit",
                            "amount_to_pay",
                            "operation_required_deposit",
                            "deposit_balance",
                            "real_deposit_balance",
                            "deposit_shortfall",
                            "deposit_free_trial",
                            "business_type",
                        )
                    }
                    item["category_candidates"] = list(
                        selection.get("candidates") or []
                    )
                category_ids = _required_category_ids(payload)
                if item.get("category_auto_recovery"):
                    if not category_ids:
                        raise CategoryAutoOpenError(
                            "历史类目失败商品无法确定叶子类目，"
                            "已停止自动重提"
                        )
                    item["category_state"] = "waiting"
                    item["category_ids"] = list(category_ids)
                    item["category_apply_submitted"] = False
                payload.pop("category_apply_ids", None)
                payload.pop("category_name", None)
                payload.pop("category_hint", None)
                payload.pop("auto_category", None)
                listing_mode = _normalize_listing_mode(template)
                if category_refresh["attempted"]:
                    self._raise_if_category_snapshot_blocks(
                        category_refresh["options"],
                        category_refresh["error"],
                        category_ids,
                    )
            except TemplateRuleSkip as exc:
                self._set_item(task, item, "skipped", str(exc))
                continue
            except (UploadCancelled, UploadPaused) as exc:
                self._set_upload_interrupted(task, item, exc)
                continue
            except CategoryApprovalPending as exc:
                now = datetime.now()
                self._copy_category_pending_to_snapshot(
                    category_refresh["options"],
                    exc,
                )
                with self.lock:
                    item["category_state"] = str(
                        exc.state or "waiting"
                    )
                    item["category_ids"] = list(exc.category_ids)
                    item["category_statuses"] = dict(exc.statuses)
                    item["category_checks"] = dict(exc.checks)
                    if exc.deposit_snapshot:
                        item["category_deposit_snapshot"] = dict(
                            exc.deposit_snapshot
                        )
                        for key, value in (
                            (
                                "category_required_deposit",
                                exc.deposit_snapshot.get(
                                    "required_deposit"
                                ),
                            ),
                            (
                                "category_amount_to_pay",
                                exc.deposit_snapshot.get(
                                    "amount_to_pay"
                                ),
                            ),
                            (
                                "category_operation_required_deposit",
                                exc.deposit_snapshot.get(
                                    "operation_required_deposit"
                                ),
                            ),
                            (
                                "category_deposit_balance",
                                exc.deposit_snapshot.get(
                                    "deposit_balance"
                                ),
                            ),
                            (
                                "category_real_deposit_balance",
                                exc.deposit_snapshot.get(
                                    "real_deposit_balance"
                                ),
                            ),
                            (
                                "category_deposit_shortfall",
                                exc.deposit_snapshot.get(
                                    "deposit_shortfall"
                                ),
                            ),
                            (
                                "category_deposit_free_trial",
                                exc.deposit_snapshot.get(
                                    "deposit_free_trial"
                                ),
                            ),
                            (
                                "category_business_type",
                                exc.deposit_snapshot.get(
                                    "business_type"
                                ),
                            ),
                        ):
                            if value is not None:
                                item[key] = value
                    item["category_apply_submitted"] = bool(
                        exc.submitted
                    )
                    item["category_application_at"] = (
                        item.get("category_application_at")
                        or now.isoformat(timespec="seconds")
                    )
                    item["category_last_checked_at"] = now.isoformat(
                        timespec="seconds"
                    )
                    item["category_check_attempts"] = int(
                        exc.attempts or 1
                    )
                    item["category_next_check_at"] = (
                        now
                        + timedelta(
                            minutes=(
                                30
                                if str(exc.state or "")
                                == "deposit_required"
                                else 10
                            )
                        )
                    ).isoformat(timespec="seconds")
                    if exc.error:
                        item["category_error"] = str(exc.error)[:500]
                    else:
                        item.pop("category_error", None)
                self._set_item(
                    task,
                    item,
                    "waiting_category",
                    str(exc),
                )
                continue
            except CategoryAutoOpenError as exc:
                item["category_state"] = str(
                    getattr(exc, "state", "") or "failed_category"
                )
                item["category_error"] = str(exc)[:500]
                self._set_item(
                    task,
                    item,
                    "failed",
                    f"目标类目未开通，自动申请失败：{exc}"[:500],
                )
                errors.append(
                    f"{store.name} / {item.get('title', '')}: "
                    f"目标类目自动处理失败：{exc}"
                )
                continue
            except Exception as exc:
                self._set_item(task, item, "failed", str(exc)[:500])
                continue
            with self.lock:
                item["status"] = "submitting"
                if not item.get("submission_attempt_id"):
                    item["submission_attempt_id"] = uuid.uuid4().hex
                item["submission_started_at"] = datetime.now().isoformat(
                    timespec="seconds"
                )
                item["message"] = "正在提交，结果尚未确认"
            try:
                self._save()
            except Exception as exc:
                self._set_item(
                    task,
                    item,
                    "failed",
                    f"提交前状态无法保存，已取消远端提交：{exc}",
                )
                continue
            try:

                def submit_product():
                    stop_reason = stop_requested()
                    if stop_reason == "cancelled":
                        raise UploadCancelled(
                            "上传任务已取消，未提交当前商品",
                            category_ids=category_ids,
                        )
                    if stop_reason in {"paused", "external"}:
                        raise UploadPaused(
                            (
                                "上传任务已暂停，未提交当前商品"
                                if stop_reason == "paused"
                                else "账户或任务代次已变化，未提交当前商品"
                            ),
                            category_ids=category_ids,
                        )
                    return self.service.product_write(
                        store,
                        "add",
                        payload,
                        listing_mode=listing_mode,
                    )

                def update_category_progress(message: str) -> None:
                    with self.lock:
                        item["message"] = message
                    try:
                        self._save()
                    except Exception:
                        pass
                    if self.progress is not None:
                        self.progress(
                            message,
                            self._jobs_completed,
                            max(1, self._jobs_total),
                        )

                result = _retry_upload_after_category_open(
                    payload,
                    submit_product,
                    lambda: _category_client_with_business_type(
                        self.db,
                        store,
                        category_business_type,
                    ),
                    progress=update_category_progress,
                    category_ids=category_ids,
                    application=item,
                    refresh_categories=refresh_categories,
                    stop_requested=stop_requested,
                )
                product_id = str(result.get("product_id") or payload.get("product_id") or "")
                if not product_id:
                    self._set_item(
                        task,
                        item,
                        "verification_pending",
                        "远端响应缺少商品 ID，请先核实是否已创建",
                    )
                    continue
                item["product_id"] = product_id
                item["status"] = "success"
                item["category_state"] = "effective"
                item.pop("category_next_check_at", None)
                item["message"] = (
                    "上传成功，已转入仓库"
                    if listing_mode == "warehouse"
                    else (
                        "上传成功，已保存草稿"
                        if listing_mode == "draft"
                        else "上传成功，已提交出售中"
                    )
                )
                with self.lock:
                    snapshot = json.dumps(self.tasks, ensure_ascii=False)
                try:
                    self.db.commit_upload_task_result(
                        snapshot,
                        store.id,
                        str(item.get("source_product_id") or ""),
                        product_id,
                        str(item.get("title") or ""),
                    )
                except Exception as exc:
                    item["status"] = "verification_pending"
                    item["message"] = (
                        "远端已创建商品，但本地任务/历史保存失败，请先核实：" + str(exc)[:300]
                    )
            except (UploadCancelled, UploadPaused) as exc:
                self._set_upload_interrupted(task, item, exc)
            except CategoryApprovalPending as exc:
                now = datetime.now()
                self._copy_category_pending_to_snapshot(
                    category_refresh["options"],
                    exc,
                )
                with self.lock:
                    item["category_state"] = str(
                        exc.state or "waiting"
                    )
                    item["category_ids"] = list(exc.category_ids)
                    item["category_statuses"] = dict(exc.statuses)
                    item["category_checks"] = dict(exc.checks)
                    if exc.deposit_snapshot:
                        item["category_deposit_snapshot"] = dict(
                            exc.deposit_snapshot
                        )
                        for key, value in (
                            (
                                "category_required_deposit",
                                exc.deposit_snapshot.get(
                                    "required_deposit"
                                ),
                            ),
                            (
                                "category_amount_to_pay",
                                exc.deposit_snapshot.get(
                                    "amount_to_pay"
                                ),
                            ),
                            (
                                "category_operation_required_deposit",
                                exc.deposit_snapshot.get(
                                    "operation_required_deposit"
                                ),
                            ),
                            (
                                "category_deposit_balance",
                                exc.deposit_snapshot.get(
                                    "deposit_balance"
                                ),
                            ),
                            (
                                "category_real_deposit_balance",
                                exc.deposit_snapshot.get(
                                    "real_deposit_balance"
                                ),
                            ),
                            (
                                "category_deposit_shortfall",
                                exc.deposit_snapshot.get(
                                    "deposit_shortfall"
                                ),
                            ),
                            (
                                "category_deposit_free_trial",
                                exc.deposit_snapshot.get(
                                    "deposit_free_trial"
                                ),
                            ),
                            (
                                "category_business_type",
                                exc.deposit_snapshot.get(
                                    "business_type"
                                ),
                            ),
                        ):
                            if value is not None:
                                item[key] = value
                    item["category_apply_submitted"] = bool(exc.submitted)
                    item["category_application_at"] = (
                        item.get("category_application_at")
                        or now.isoformat(timespec="seconds")
                    )
                    item["category_last_checked_at"] = now.isoformat(
                        timespec="seconds"
                    )
                    item["category_check_attempts"] = int(exc.attempts or 1)
                    if exc.error:
                        item["category_error"] = str(exc.error)[:500]
                    else:
                        item.pop("category_error", None)
                    item["category_next_check_at"] = (
                        now
                        + timedelta(
                            minutes=(
                                30
                                if str(exc.state or "")
                                == "deposit_required"
                                else 10
                            )
                        )
                    ).isoformat(timespec="seconds")
                self._set_item(task, item, "waiting_category", str(exc))
            except CategoryAutoOpenError as exc:
                item["category_state"] = str(
                    getattr(exc, "state", "") or "failed_category"
                )
                item["category_error"] = str(exc)[:500]
                self._set_item(
                    task,
                    item,
                    "failed",
                    f"目标类目未开通，自动申请失败：{exc}"[:500],
                )
                errors.append(
                    f"{store.name} / {item.get('title', '')}: "
                    f"目标类目自动处理失败：{exc}"
                )
            except PermissionError as exc:
                self._set_item(
                    task,
                    item,
                    "failed",
                    "未提交到微信小店：本地授权拒绝：" + str(exc)[:400],
                )
                errors.append(f"{store.name} / {item.get('title', '')}: {exc}")
            except Exception as exc:
                rejection = _definitive_upload_failure_message(exc)
                if rejection:
                    self._set_item(task, item, "failed", rejection)
                else:
                    self._set_item(
                        task,
                        item,
                        "verification_pending",
                        "远端提交结果未知，请先核实：" + str(exc)[:400],
                    )
                errors.append(f"{store.name} / {item.get('title', '')}: {exc}")
            finally:
                with self.lock:
                    self._refresh_task_status(task)
                    self._jobs_completed += 1
                    completed = self._jobs_completed
                    total = self._jobs_total
                self._save()
                if self.progress is not None:
                    self.progress(
                        f"上传任务处理中 {completed}/{total}",
                        completed,
                        total,
                    )

    @staticmethod
    def _set_upload_interrupted(
        task: dict[str, Any],
        item: dict[str, Any],
        interrupted: Any,
    ) -> None:
        item["status"] = str(
            getattr(interrupted, "status", "") or "cancelled"
        )
        item["message"] = str(interrupted)
        item["finished_at"] = datetime.now().isoformat(
            timespec="seconds"
        )
        category_ids = list(
            getattr(interrupted, "category_ids", []) or []
        )
        if category_ids:
            item["category_ids"] = category_ids
        if bool(getattr(interrupted, "category_applied", False)):
            item["category_apply_submitted"] = True
            item["category_state"] = "waiting"
        UploadRunner._refresh_task_status(task)

    @staticmethod
    def _copy_category_pending_to_snapshot(
        options: Any,
        pending: CategoryApprovalPending,
    ) -> None:
        if not isinstance(options, dict):
            return
        category_ids = set(pending.category_ids)

        def walk(nodes: Any) -> None:
            if not isinstance(nodes, list):
                return
            for node in nodes:
                if not isinstance(node, dict):
                    continue
                category_id = str(
                    node.get("category_id") or ""
                ).strip()
                if category_id in category_ids:
                    node["application_state"] = str(
                        pending.state or "unknown"
                    )
                    node["application_label"] = str(
                        pending.statuses.get(category_id)
                        or pending.state
                        or "状态未知"
                    )
                    node["application_reason"] = str(
                        pending.error or ""
                    )[:500]
                    node["application_eligible"] = False
                walk(node.get("children") or [])

        walk(options.get("roots") or [])

    @staticmethod
    def _raise_if_category_snapshot_blocks(
        options: Any,
        refresh_error: Any,
        category_ids: list[str],
    ) -> None:
        if isinstance(refresh_error, Exception):
            message = (
                "平台类目错误后刷新类目失败，待核实："
                + str(refresh_error)[:500]
            )
            checks = {
                category_id: {
                    "state": "unknown",
                    "label": "刷新失败，待核实",
                    "reason": message,
                    "eligible": False,
                }
                for category_id in category_ids
            }
            raise CategoryApprovalPending(
                category_ids,
                _category_status_labels(checks),
                checks=checks,
                submitted=False,
                attempts=1,
                error=message,
                state="unknown",
            )
        if (
            not isinstance(options, dict)
            or not options
            or not isinstance(options.get("roots"), list)
            or not options.get("roots")
        ):
            message = (
                "平台类目错误后刷新结果无效，待核实："
                "未返回有效的官方类目根节点"
            )
            checks = {
                category_id: {
                    "state": "unknown",
                    "label": "刷新结果无效，待核实",
                    "reason": message,
                    "eligible": False,
                }
                for category_id in category_ids
            }
            raise CategoryApprovalPending(
                category_ids,
                _category_status_labels(checks),
                checks=checks,
                submitted=False,
                attempts=1,
                error=message,
                state="unknown",
            )
        refresh_error_text = str(
            options.get("refresh_error")
            or (
                options.get("error")
                if options.get("manual_refresh_failed")
                else ""
            )
            or ""
        ).strip()
        if options.get("manual_refresh_failed") or refresh_error_text:
            message = (
                "平台类目错误后刷新类目失败，待核实："
                + (refresh_error_text or "官方类目未返回有效结果")
            )
            checks = {
                category_id: {
                    "state": "unknown",
                    "label": "刷新失败，待核实",
                    "reason": message,
                    "eligible": False,
                }
                for category_id in category_ids
            }
            raise CategoryApprovalPending(
                category_ids,
                _category_status_labels(checks),
                checks=checks,
                submitted=False,
                attempts=1,
                error=message,
                state="unknown",
            )

        checks = _category_checks_from_options(options, category_ids)
        hard_error = _category_hard_failure(checks)
        if hard_error:
            raise CategoryAutoOpenError(
                "类目不可自动开通：" + hard_error,
                state=(
                    _category_hard_state(checks)
                    or "failed_category"
                ),
            )
        if _category_checks_all(checks, category_ids, "effective"):
            return
        state = _category_pending_state(checks)
        if state == "deposit_required":
            raise CategoryApprovalPending(
                category_ids,
                _category_status_labels(checks),
                checks=checks,
                submitted=False,
                attempts=1,
                state="deposit_required",
                deposit_snapshot=_category_deposit_snapshot(checks),
            )
        if state == "not_applied" and all(
            category_id in checks
            and checks[category_id].get("state") == "not_applied"
            and bool(checks[category_id].get("eligible"))
            for category_id in category_ids
        ):
            return
        raise CategoryApprovalPending(
            category_ids,
            _category_status_labels(checks),
            checks=checks,
            submitted=state == "pending",
            attempts=1,
            state=state,
        )

    def _set_item(
        self,
        task: dict[str, Any],
        item: dict[str, Any],
        status: str,
        message: str,
    ) -> None:
        with self.lock:
            item["status"] = status
            item["message"] = message
            item["finished_at"] = datetime.now().isoformat(timespec="seconds")
            self._refresh_task_status(task)

    def _save(self) -> None:
        with self.lock:
            self.db.set_setting(
                "upload_tasks",
                json.dumps(self.tasks, ensure_ascii=False),
            )

    @staticmethod
    def _refresh_task_status(task: dict[str, Any]) -> None:
        statuses = [str(item.get("status") or "pending") for item in task.get("items") or []]
        if any(status in {"submitting"} for status in statuses):
            task["status"] = "running"
        elif any(status == "verification_pending" for status in statuses):
            task["status"] = "needs_verification"
        elif any(status == "waiting_category" for status in statuses):
            task["status"] = "needs_category"
        elif any(status == "cancelled" for status in statuses):
            task["status"] = "cancelled"
        elif any(status in {"pending", "paused"} for status in statuses):
            task["status"] = "paused"
        elif any(status == "failed" for status in statuses):
            task["status"] = "partial"
        elif all(status in {"success", "skipped"} for status in statuses):
            task["status"] = "completed"
        else:
            task["status"] = "pending"
        success = statuses.count("success")
        failed = statuses.count("failed")
        skipped = statuses.count("skipped")
        pending = sum(
            status not in {"success", "failed", "skipped", "cancelled"}
            for status in statuses
        )
        task["message"] = f"成功 {success}，失败 {failed}，跳过 {skipped}，待处理 {pending}"


class UploadPage(TaskPage):
    def __init__(self, workspace) -> None:
        super().__init__(
            workspace,
            "上传商品",
            "数据包、模板和店铺上货任务",
            stale_seconds=0,
        )
        self._batch = _json_rows(
            self.workspace.db.get_setting("upload_batch", "[]"),
            [],
        )
        self._templates = _json_rows(
            self.workspace.db.get_setting("upload_templates", "[]"),
            [],
        )
        self._tasks = _json_rows(
            self.workspace.db.get_setting("upload_tasks", "[]"),
            [],
        )
        _recover_upload_tasks(self._tasks)
        self._runner = UploadRunner(workspace, self._tasks)
        self._selected_batch_ids: set[str] = set()
        self._selected_task_ids: set[str] = set()

        import_bar = QHBoxLayout()
        self.package_label = QLabel("尚未导入数据包")
        self.import_button = PrimaryButton("选择数据包")
        self.folder_button = QPushButton("导入文件夹")
        self.clear_batch_button = QPushButton("清空批次")
        import_bar.addWidget(self.package_label, 1)
        import_bar.addWidget(self.import_button)
        import_bar.addWidget(self.folder_button)
        import_bar.addWidget(self.clear_batch_button)
        self.root_layout.addLayout(import_bar)
        self.import_button.clicked.connect(self._import_package)
        self.folder_button.clicked.connect(self._import_folder)
        self.clear_batch_button.clicked.connect(self._clear_batch)

        controls = QHBoxLayout()
        store_box = QGroupBox("目标店铺")
        store_layout = QHBoxLayout(store_box)
        self.store_list = QListWidget()
        self.store_list.setMaximumHeight(150)
        store_layout.addWidget(self.store_list)
        controls.addWidget(store_box, 1)
        template_box = QGroupBox("上货模板")
        template_layout = QHBoxLayout(template_box)
        self.template_combo = QComboBox()
        template_layout.addWidget(self.template_combo, 1)
        self.template_edit_button = QPushButton("编辑 JSON")
        self.template_new_button = QPushButton("新建模板")
        template_layout.addWidget(self.template_new_button)
        template_layout.addWidget(self.template_edit_button)
        controls.addWidget(template_box, 1)
        self.root_layout.addLayout(controls)
        self.template_new_button.clicked.connect(self._new_template)
        self.template_edit_button.clicked.connect(self._edit_template)

        action_row = QHBoxLayout()
        self.create_button = PrimaryButton("创建任务")
        self.start_button = QPushButton("开始上传")
        self.pause_button = QPushButton("暂停")
        self.retry_button = QPushButton("重试失败")
        task_filter = QComboBox()
        task_filter.addItems(("全部状态", "需处理", "待核实", "待类目"))
        action_row.addWidget(self.create_button)
        action_row.addWidget(self.start_button)
        action_row.addWidget(self.pause_button)
        action_row.addWidget(self.retry_button)
        action_row.addStretch(1)
        action_row.addWidget(task_filter)
        self.root_layout.addLayout(action_row)
        self.create_button.clicked.connect(self._create_tasks)
        self.start_button.clicked.connect(self._start_tasks)
        self.pause_button.clicked.connect(lambda: self._runner.paused.set())
        self.retry_button.clicked.connect(self._retry_failed)

        self.batch_table = DataTable(
            [
                ("selected", "选"),
                ("title", "商品标题"),
                ("platform", "来源"),
                ("source_product_id", "来源ID"),
                ("price", "价格"),
                ("stock", "库存"),
            ]
        )
        self.batch_table.setSelectionMode(DataTable.SelectionMode.ExtendedSelection)
        self.root_layout.addWidget(self.batch_table, 1)
        self.task_table = DataTable(
            [
                ("created_at", "创建时间"),
                ("store_name", "店铺"),
                ("template", "模板"),
                ("status", "状态"),
                ("progress", "进度"),
                ("message", "说明"),
            ]
        )
        self.root_layout.addWidget(self.task_table, 1)
        self.summary = QLabel()
        self.summary.setObjectName("页面说明")
        self.root_layout.addWidget(self.summary)
        self._load_stores()
        self._load_templates()
        self.refresh()

    def _load_stores(self) -> None:
        checked = {
            int(self.store_list.item(index).data(256))
            for index in range(self.store_list.count())
            if self.store_list.item(index).checkState() == 2
        }
        self.store_list.clear()
        self._stores = list(self.workspace.db.list_stores())
        for store in self._stores:
            item = QListWidgetItem(f"{store.name} · {store.web_status}")
            item.setData(256, store.id)
            item.setFlags(item.flags() | 0x10)
            item.setCheckState(2 if store.id in checked else 0)
            self.store_list.addItem(item)

    def _load_templates(self) -> None:
        current = self.template_combo.currentData()
        self._templates = _json_rows(
            self.workspace.db.get_setting("upload_templates", "[]"),
            [],
        )
        self.template_combo.clear()
        for index, template in enumerate(self._templates):
            if isinstance(template, dict):
                self.template_combo.addItem(
                    str(template.get("name") or f"模板 {index + 1}"),
                    index,
                )
        self.template_combo.setCurrentIndex(
            max(0, self.template_combo.findData(current)) if self.template_combo.count() else -1
        )

    def refresh(self) -> None:
        self._load_stores()
        self._load_templates()
        self.batch_table.set_rows(
            [
                {
                    **row,
                    "selected": "✓",
                    "price": f"¥{int(row.get('price') or 0) / 100:.2f}",
                }
                for row in self._batch
                if isinstance(row, dict)
            ]
        )
        status_labels = {
            "pending": "待执行",
            "paused": "已暂停",
            "running": "执行中",
            "completed": "已完成",
            "partial": "需处理",
            "needs_verification": "待核实",
            "needs_category": "待类目",
        }
        self.task_table.set_rows(
            [
                {
                    **task,
                    "template": (task.get("template") or {}).get("name", "不使用模板"),
                    "status": status_labels.get(task.get("status"), task.get("status")),
                    "progress": self._progress_text(task),
                }
                for task in self._tasks
                if isinstance(task, dict)
            ]
        )
        self.summary.setText(
            f"批次商品 {len(self._batch)} · 任务 {len(self._tasks)} · "
            "任务和模板保存在旧版 app_settings，关闭页面不会丢失"
        )
        super().refresh()

    @staticmethod
    def _progress_text(task: dict[str, Any]) -> str:
        items = task.get("items") or []
        completed = sum(item.get("status") in {"success", "failed", "skipped"} for item in items)
        return f"{completed}/{len(items)}"

    def _import_package(self) -> None:
        path, _filter = QFileDialog.getOpenFileName(
            self,
            "选择商品数据包",
            "",
            "商品数据包 (*.json *.jsonl *.csv *.tsv *.txt);;所有文件 (*.*)",
        )
        if path:
            self._import_files([Path(path)])

    def _import_folder(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择商品数据包文件夹")
        if not path:
            return
        extensions = {".json", ".jsonl", ".csv", ".tsv", ".txt"}
        files = [
            item
            for item in sorted(Path(path).rglob("*"))
            if item.is_file() and item.suffix.lower() in extensions
        ]
        self._import_files(files)

    def _import_files(self, paths: list[Path]) -> None:
        if not paths:
            return
        adapter = _PayloadAdapter(self.workspace.db)
        added = 0
        errors = []
        existing = {
            (str(row.get("source_product_id")), str(row.get("title")))
            for row in self._batch
            if isinstance(row, dict)
        }
        for path in paths:
            try:
                rows = LegacyUploadPage._parse_package(adapter, path)
            except Exception as exc:
                errors.append(f"{path.name}: {exc}")
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                identity = (
                    str(row.get("source_product_id") or ""),
                    str(row.get("title") or ""),
                )
                if identity in existing:
                    continue
                existing.add(identity)
                self._batch.append(row)
                added += 1
        self.workspace.db.set_setting(
            "upload_batch",
            json.dumps(self._batch, ensure_ascii=False),
        )
        self.package_label.setText(f"已导入 {len(paths)} 个文件 · 新增 {added} 个商品")
        self.refresh()
        if errors:
            QMessageBox.warning(self, "部分文件导入失败", "\n".join(errors[:10]))

    def _clear_batch(self) -> None:
        self._batch = []
        self._selected_batch_ids.clear()
        self.workspace.db.set_setting("upload_batch", "[]")
        self.refresh()

    def _new_template(self) -> None:
        dialog = PayloadDialog(
            "新建上货模板",
            {
                "name": "新模板",
                "title_prefix": "",
                "title_suffix": "",
                "price_multiplier": 1,
                "price_offset": 0,
                "stock": 0,
                "listing_mode": "immediate",
                "duplicate_filter_mode": "store",
                "title_rules": "",
                "sku_rules": "",
                "image_rules": "",
                "attribute_rules": "",
                "fallback_category": "",
            },
            self,
            editable=True,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._templates.append(dialog.payload)
        self.workspace.db.set_setting(
            "upload_templates",
            json.dumps(self._templates, ensure_ascii=False),
        )
        self.refresh()
        self.template_combo.setCurrentIndex(self.template_combo.count() - 1)

    def _edit_template(self) -> None:
        index = self.template_combo.currentData()
        if not isinstance(index, int) or not (0 <= index < len(self._templates)):
            return
        dialog = PayloadDialog(
            "编辑上货模板",
            self._templates[index],
            self,
            editable=True,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._templates[index] = dialog.payload
        self.workspace.db.set_setting(
            "upload_templates",
            json.dumps(self._templates, ensure_ascii=False),
        )
        self.refresh()

    def _selected_stores(self) -> list[Store]:
        ids = {
            int(self.store_list.item(index).data(256))
            for index in range(self.store_list.count())
            if self.store_list.item(index).checkState() == 2
        }
        return [store for store in self._stores if store.id in ids]

    def _create_tasks(self) -> None:
        selected_titles = {
            str(self.batch_table.model_data.row_at(index.row()).get("title") or "")
            for index in self.batch_table.selectionModel().selectedRows()
            if self.batch_table.model_data.row_at(index.row())
        }
        products = [
            row
            for row in self._batch
            if not selected_titles or str(row.get("title") or "") in selected_titles
        ]
        if not products:
            self.notify.emit("提示", "请选择要上传的商品")
            return
        stores = self._selected_stores()
        if not stores:
            self.notify.emit("提示", "请选择目标店铺")
            return
        index = self.template_combo.currentData()
        if not isinstance(index, int) or not (0 <= index < len(self._templates)):
            self.notify.emit("提示", "请先创建或选择上货模板")
            return
        template = self._templates[index]
        for store in stores:
            task = {
                "id": f"up_{uuid.uuid4().hex[:12]}",
                "store_id": store.id,
                "store_name": store.name,
                "template": template,
                "status": "pending",
                "created_at": datetime.now().isoformat(timespec="seconds"),
                "message": "等待开始",
                "items": [
                    {
                        **product,
                        "item_id": uuid.uuid4().hex,
                        "status": "pending",
                        "message": "等待上传",
                        "product_id": "",
                    }
                    for product in products
                ],
            }
            self._tasks.insert(0, task)
        self.workspace.db.set_setting(
            "upload_tasks",
            json.dumps(self._tasks, ensure_ascii=False),
        )
        self.refresh()
        self.notify.emit("任务已创建", f"{len(stores)} 个店铺任务")

    def _selected_tasks(self) -> list[dict]:
        rows = [
            self.task_table.model_data.row_at(index.row())
            for index in self.task_table.selectionModel().selectedRows()
        ]
        ids = {str(row.get("id")) for row in rows if row}
        return [task for task in self._tasks if str(task.get("id")) in ids]

    def _start_tasks(self) -> None:
        selected = self._selected_tasks() or [
            task
            for task in self._tasks
            if task.get("status")
            in {
                "pending",
                "paused",
                "partial",
                "needs_verification",
                "needs_category",
            }
        ]
        if not selected:
            self.notify.emit("提示", "没有可执行的上传任务")
            return
        answer = QMessageBox.question(
            self,
            "确认上传商品",
            f"即将向微信小店提交 {sum(len(task.get('items') or []) for task in selected)} "
            "个商品任务，可能消耗提审额度。是否继续？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._runner.paused.clear()
        self._runner.cancelled.clear()
        self.submit(
            "upload:start",
            lambda: self._runner.run(selected),
            lambda errors: self._after_run(errors),
            self.start_button,
        )

    def _after_run(self, errors: list[str]) -> None:
        self.refresh()
        self.notify.emit("上传任务结束", f"问题 {len(errors)} 条")

    def _retry_failed(self) -> None:
        for task in self._selected_tasks() or self._tasks:
            for item in task.get("items") or []:
                if item.get("status") == "failed":
                    item["status"] = "pending"
                    item["message"] = "等待重试"
        self.workspace.db.set_setting(
            "upload_tasks",
            json.dumps(self._tasks, ensure_ascii=False),
        )
        self.refresh()
