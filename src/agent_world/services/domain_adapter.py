"""
DomainAdapter — 域适配器基类。

每个 LLM prompt 的"内容层"槽位由 DomainAdapter 按 slot name 提供。
换域 = 继承此类，实现所有 render_slot 分发的 slot 方法。
拓扑层槽位由引擎固定提供，不受域影响。
"""
from __future__ import annotations
from abc import ABC


class DomainAdapter(ABC):
    """域适配器 — 为 LLM prompt 的内容层槽位提供域特定的文字。

    子类需实现 ContentDispatcher 接口：即注册所有支持的 slot name 对应的渲染方法。
    不支持的 slot name 返回空字符串。
    """

    def render_slot(self, slot_name: str, **kw) -> str:
        """分发 slot 到具体渲染方法。不支持的 slot → 空字符串。"""
        method_name = f"slot_{slot_name}"
        handler = getattr(self, method_name, None)
        if handler is None:
            return ""
        return handler(**kw)
