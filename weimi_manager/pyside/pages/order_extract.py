from __future__ import annotations

from .orders import OrdersPage


class OrderExtractPage(OrdersPage):
    def __init__(self, workspace) -> None:
        super().__init__(workspace)
        self.title = "订单提取"
        self.description = "从微信小店拉取待发货订单，解析收货信息并导出工作表"
        self._set_tab("pending_delivery")


class ParsedOrdersPage(OrdersPage):
    def __init__(self, workspace) -> None:
        super().__init__(workspace)
        self.title = "已解析订单"
        self.description = "查询已解析和已打印的本地订单记录"
        self._set_tab("marked")
