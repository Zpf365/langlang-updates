from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import TYPE_CHECKING, Any

from ..cloud_commands import AuthorizedServiceAdapter
from ..team_service import (
    DEFAULT_SUBACCOUNT_PERMISSIONS,
    TEAM_PERMISSIONS,
    TeamService,
)
from .theme import MUTED, button

if TYPE_CHECKING:
    from .app import MainWindow


class TeamPage(ttk.Frame):
    title = "团队账号"
    subtitle = "主账号创建子账号、分配店铺和功能权限"

    def __init__(self, parent: tk.Widget, window: "MainWindow") -> None:
        super().__init__(parent, style="TFrame", padding=(24, 16, 24, 14))
        self.window = window
        self.db = window.db
        self.members: list[dict[str, Any]] = []
        self.member_rows: dict[str, dict[str, Any]] = {}
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(self, style="TFrame")
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        ttk.Label(
            toolbar,
            text="主账号可设置子账号密码；子账号不能修改或重置自己的密码",
            style="Subtitle.TLabel",
        ).pack(side="left")
        button(toolbar, "刷新成员", self.refresh).pack(side="right")
        button(
            toolbar,
            "添加共同主账号",
            self._add_existing_account,
        ).pack(side="right", padx=6)
        button(
            toolbar,
            "创建子账号",
            self._create_subaccount,
            "Primary",
        ).pack(side="right")

        body = ttk.Frame(self, style="Panel.TFrame")
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=1)
        body.rowconfigure(0, weight=1)
        self.tree = ttk.Treeview(
            body,
            columns=("username", "role", "status", "stores", "permissions"),
            show="headings",
            selectmode="browse",
        )
        for key, label, width in (
            ("username", "账号", 180),
            ("role", "角色", 100),
            ("status", "状态", 90),
            ("stores", "可管理店铺", 320),
            ("permissions", "功能权限", 480),
        ):
            self.tree.heading(key, text=label)
            self.tree.column(key, width=width, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            body,
            orient="vertical",
            command=self.tree.yview,
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scrollbar.set)

        actions = ttk.Frame(self, style="TFrame")
        actions.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        self.edit_button = button(
            actions,
            "编辑权限和店铺",
            self._edit_member,
        )
        self.edit_button.pack(side="left")
        self.reset_button = button(
            actions,
            "重置子账号密码",
            self._reset_password,
        )
        self.reset_button.pack(side="left", padx=6)
        self.disable_button = button(
            actions,
            "停用/启用",
            self._toggle_member,
        )
        self.disable_button.pack(side="left")
        self.tree.bind("<<TreeviewSelect>>", self._update_actions)

    def _service(self) -> TeamService | None:
        context = getattr(self.window, "auth_context", None)
        client = getattr(self.window, "auth_client", None)
        if context is None or client is None:
            messagebox.showerror(
                "无法管理团队",
                "当前账户没有可用的服务端账号通道。",
                parent=self,
            )
            return None
        if context.member_role not in {"owner", "admin"}:
            messagebox.showerror(
                "没有权限",
                "只有主账号或共同主账号可以管理团队。",
                parent=self,
            )
            return None
        service = TeamService(client, context.tokens.access_token)
        commands = getattr(self.window, "cloud_commands", None)
        if commands is None:
            return service
        return AuthorizedServiceAdapter(service, commands)

    def refresh(self) -> None:
        service = self._service()
        if service is None:
            return

        def done(members: list[dict[str, Any]]) -> None:
            self.members = members
            self.tree.delete(*self.tree.get_children())
            self.member_rows.clear()
            role_labels = {
                "owner": "主账号",
                "admin": "共同主账号",
                "manager": "管理员",
                "operator": "运营",
                "viewer": "只读",
            }
            for index, member in enumerate(members):
                iid = str(index)
                self.member_rows[iid] = member
                enabled = [
                    label
                    for key, label in TEAM_PERMISSIONS
                    if member.get("role") in {"owner", "admin"}
                    or (member.get("permissions") or {}).get(key)
                ]
                grants = list(member.get("grants") or [])
                self.tree.insert(
                    "",
                    "end",
                    iid=iid,
                    values=(
                        member.get("username", ""),
                        role_labels.get(
                            str(member.get("role") or ""),
                            str(member.get("role") or ""),
                        ),
                        "启用" if member.get("is_active", True) else "停用",
                        "、".join(grants) if grants else "全部店铺"
                        if member.get("role") in {"owner", "admin"}
                        else "未分配",
                        "、".join(enabled)
                        if member.get("role") not in {"owner", "admin"}
                        else "全部功能",
                    ),
                )
            self._update_actions()

        self.window.run_task(
            lambda _progress: service.list_members(),
            done,
            lambda error: messagebox.showerror(
                "团队账号加载失败",
                error,
                parent=self,
            ),
            log_title="加载团队账号",
        )

    def _selected_member(self) -> dict[str, Any] | None:
        selection = self.tree.selection()
        return self.member_rows.get(selection[0]) if selection else None

    def _update_actions(self, _event=None) -> None:
        member = self._selected_member()
        context = getattr(self.window, "auth_context", None)
        allowed = bool(
            member
            and context
            and context.member_role in {"owner", "admin"}
        )
        for widget in (
            self.edit_button,
            self.disable_button,
        ):
            widget.configure(state="normal" if allowed else "disabled")
        self.reset_button.configure(
            state=(
                "normal"
                if allowed
                and not (
                    member
                    and member.get("role") == "owner"
                    and context.member_role != "owner"
                )
                else "disabled"
            )
        )

    def _store_choices(self) -> list[tuple[str, str]]:
        return [
            (store.name, store.app_id)
            for store in self.db.list_stores()
        ]

    def _member_dialog(
        self,
        *,
        member: dict[str, Any] | None = None,
    ) -> None:
        service = self._service()
        if service is None:
            return
        creating = member is None
        dialog = tk.Toplevel(self)
        dialog.title("创建子账号" if creating else "编辑账号权限")
        dialog.geometry("760x720")
        dialog.transient(self.winfo_toplevel())
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(3, weight=1)

        account_row = ttk.Frame(dialog, style="TFrame")
        account_row.grid(row=0, column=0, sticky="ew", padx=18, pady=(18, 8))
        ttk.Label(account_row, text="账号名").pack(side="left")
        username_var = tk.StringVar(value=str((member or {}).get("username") or ""))
        username_entry = ttk.Entry(
            account_row,
            textvariable=username_var,
            width=30,
        )
        username_entry.pack(side="left", padx=(8, 20))
        username_entry.configure(state="normal" if creating else "readonly")
        ttk.Label(account_row, text="角色").pack(side="left")
        role_var = tk.StringVar(value=str((member or {}).get("role") or "operator"))
        ttk.Combobox(
            account_row,
            textvariable=role_var,
            state="readonly",
            width=16,
            values=("owner", "admin", "manager", "operator", "viewer"),
        ).pack(side="left", padx=8)

        password_row = ttk.Frame(dialog, style="TFrame")
        password_row.grid(row=1, column=0, sticky="ew", padx=18, pady=8)
        password_var = tk.StringVar()
        confirm_var = tk.StringVar()
        if creating:
            for label, variable in (
                ("初始密码", password_var),
                ("确认密码", confirm_var),
            ):
                ttk.Label(password_row, text=label).pack(side="left")
                ttk.Entry(
                    password_row,
                    textvariable=variable,
                    show="●",
                    width=20,
                ).pack(side="left", padx=(6, 18))
        else:
            ttk.Label(
                password_row,
                text="密码只能在列表底部通过“重置子账号密码”单独设置。",
                foreground=MUTED,
            ).pack(side="left")

        permission_box = ttk.LabelFrame(
            dialog,
            text="功能权限",
            padding=(12, 10),
        )
        permission_box.grid(row=2, column=0, sticky="ew", padx=18, pady=8)
        permission_vars: dict[str, tk.BooleanVar] = {}
        existing_permissions = (
            member.get("permissions")
            if member
            else DEFAULT_SUBACCOUNT_PERMISSIONS
        )
        for index, (key, label) in enumerate(TEAM_PERMISSIONS):
            variable = tk.BooleanVar(
                value=bool((existing_permissions or {}).get(key))
            )
            permission_vars[key] = variable
            ttk.Checkbutton(
                permission_box,
                text=label,
                variable=variable,
            ).grid(
                row=index // 4,
                column=index % 4,
                sticky="w",
                padx=6,
                pady=4,
            )

        store_box = ttk.LabelFrame(
            dialog,
            text="可管理店铺",
            padding=(10, 8),
        )
        store_box.grid(row=3, column=0, sticky="nsew", padx=18, pady=8)
        store_box.columnconfigure(0, weight=1)
        store_box.rowconfigure(0, weight=1)
        store_list = tk.Listbox(
            store_box,
            selectmode="extended",
            exportselection=False,
        )
        store_list.grid(row=0, column=0, sticky="nsew")
        store_scroll = ttk.Scrollbar(
            store_box,
            orient="vertical",
            command=store_list.yview,
        )
        store_scroll.grid(row=0, column=1, sticky="ns")
        store_list.configure(yscrollcommand=store_scroll.set)
        choices = self._store_choices()
        existing_grants = set((member or {}).get("grants") or [])
        for index, (name, app_id) in enumerate(choices):
            store_list.insert("end", f"{name} · {app_id}")
            if app_id in existing_grants:
                store_list.selection_set(index)

        control_row = ttk.Frame(dialog, style="TFrame")
        control_row.grid(row=4, column=0, sticky="ew", padx=18, pady=8)
        active_var = tk.BooleanVar(value=bool((member or {}).get("is_active", True)))
        ttk.Checkbutton(
            control_row,
            text="账号启用",
            variable=active_var,
        ).pack(side="left")
        message = ttk.Label(
            control_row,
            text="",
            foreground="#b55449",
        )
        message.pack(side="left", padx=12)
        actions = ttk.Frame(dialog, style="TFrame")
        actions.grid(row=5, column=0, sticky="e", padx=18, pady=(8, 18))

        def save() -> None:
            username = username_var.get().strip()
            password = password_var.get()
            role = role_var.get()
            permissions = {
                key: bool(variable.get())
                for key, variable in permission_vars.items()
            }
            grants = [
                choices[int(iid)][1]
                for iid in store_list.curselection()
            ]
            if creating:
                if not username:
                    message.configure(text="请输入账号名。")
                    return
                if len(password) < 8:
                    message.configure(text="密码至少需要8位。")
                    return
                if password != confirm_var.get():
                    message.configure(text="两次密码不一致。")
                    return

                def finish(_result: Any) -> None:
                    dialog.destroy()
                    self.refresh()
                    self.window.set_status(
                        f"子账号 {username} 已创建"
                    )

                self.window.run_task(
                    lambda _progress: service.create_subaccount(
                        username=username,
                        password=password,
                        role=role,
                        permissions=permissions,
                        grants=grants,
                    ),
                    finish,
                    lambda error: message.configure(text=str(error)),
                    log_title="创建子账号",
                )
                return

            def finish(_result: Any) -> None:
                dialog.destroy()
                self.refresh()
                self.window.set_status(
                    f"账号 {username} 的权限已更新"
                )

            self.window.run_task(
                lambda _progress: service.update_member(
                    str(member["member_id"]),
                    role=role,
                    permissions=permissions,
                    grants=grants,
                    is_active=bool(active_var.get()),
                ),
                finish,
                lambda error: message.configure(text=str(error)),
                log_title="更新子账号权限",
            )

        button(actions, "取消", dialog.destroy).pack(side="left", padx=6)
        button(
            actions,
            "保存",
            save,
            "Primary",
        ).pack(side="left")
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.grab_set()

    def _create_subaccount(self) -> None:
        self._member_dialog()

    def _edit_member(self) -> None:
        member = self._selected_member()
        if member is None:
            messagebox.showinfo("请选择账号", "请先选择一个成员。", parent=self)
            return
        self._member_dialog(member=member)

    def _add_existing_account(self) -> None:
        service = self._service()
        if service is None:
            return
        dialog = tk.Toplevel(self)
        dialog.title("添加共同主账号")
        dialog.geometry("480x210")
        dialog.transient(self.winfo_toplevel())
        ttk.Label(
            dialog,
            text="输入已注册的澜琅账号名，将其加入当前组织。",
        ).pack(anchor="w", padx=18, pady=(18, 8))
        username_var = tk.StringVar()
        ttk.Entry(dialog, textvariable=username_var).pack(
            fill="x",
            padx=18,
        )
        status = ttk.Label(dialog, text="", foreground="#b55449")
        status.pack(anchor="w", padx=18, pady=8)

        def submit() -> None:
            username = username_var.get().strip()
            if not username:
                status.configure(text="请输入账号名。")
                return

            def done(_result: Any) -> None:
                dialog.destroy()
                self.refresh()
                self.window.set_status(f"{username} 已加入团队")

            self.window.run_task(
                lambda _progress: service.add_existing_account(username),
                done,
                lambda error: status.configure(text=str(error)),
                log_title="添加共同主账号",
            )

        actions = ttk.Frame(dialog, style="TFrame")
        actions.pack(fill="x", padx=18, pady=(4, 16))
        button(actions, "取消", dialog.destroy).pack(side="right", padx=6)
        button(
            actions,
            "添加",
            submit,
            "Primary",
        ).pack(side="right")
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.grab_set()

    def _reset_password(self) -> None:
        member = self._selected_member()
        if member is None:
            messagebox.showinfo("请选择账号", "请先选择一个子账号。", parent=self)
            return
        service = self._service()
        if service is None:
            return
        dialog = tk.Toplevel(self)
        dialog.title("重置子账号密码")
        dialog.geometry("460x220")
        dialog.transient(self.winfo_toplevel())
        ttk.Label(
            dialog,
            text=f"为 {member.get('username', '')} 设置新密码",
        ).pack(anchor="w", padx=18, pady=(18, 8))
        password_var = tk.StringVar()
        confirm_var = tk.StringVar()
        ttk.Entry(
            dialog,
            textvariable=password_var,
            show="●",
        ).pack(fill="x", padx=18, pady=4)
        ttk.Entry(
            dialog,
            textvariable=confirm_var,
            show="●",
        ).pack(fill="x", padx=18, pady=4)
        status = ttk.Label(dialog, text="", foreground="#b55449")
        status.pack(anchor="w", padx=18, pady=6)

        def submit() -> None:
            password = password_var.get()
            if len(password) < 8:
                status.configure(text="密码至少需要8位。")
                return
            if password != confirm_var.get():
                status.configure(text="两次密码不一致。")
                return

            def done(_result: Any) -> None:
                dialog.destroy()
                self.window.set_status(
                    f"{member.get('username', '')} 的密码已重置"
                )

            self.window.run_task(
                lambda _progress: service.reset_password(
                    str(member["member_id"]),
                    password,
                ),
                done,
                lambda error: status.configure(text=str(error)),
                log_title="重置子账号密码",
            )

        actions = ttk.Frame(dialog, style="TFrame")
        actions.pack(fill="x", padx=18, pady=10)
        button(actions, "取消", dialog.destroy).pack(side="right", padx=6)
        button(actions, "确认重置", submit, "Primary").pack(side="right")
        dialog.grab_set()

    def _toggle_member(self) -> None:
        member = self._selected_member()
        if member is None:
            messagebox.showinfo("请选择账号", "请先选择一个成员。", parent=self)
            return
        service = self._service()
        if service is None:
            return
        active = not bool(member.get("is_active", True))

        def done(_result: Any) -> None:
            self.refresh()
            self.window.set_status(
                f"{member.get('username', '')} 已"
                + ("启用" if active else "停用")
            )

        self.window.run_task(
            lambda _progress: service.update_member(
                str(member["member_id"]),
                role=str(member.get("role") or "operator"),
                permissions=dict(member.get("permissions") or {}),
                grants=list(member.get("grants") or []),
                is_active=active,
            ),
            done,
            lambda error: messagebox.showerror(
                "更新账号状态失败",
                error,
                parent=self,
            ),
            log_title="更新账号状态",
        )
