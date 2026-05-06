"""
World Updater - 世界状态刷新服务

职责：
1. 手动触发：POST /api/world/refresh 立即刷新
2. 定时触发：每 N 分钟执行一次（可配置）
3. 世界事件：LLM/规则评估世界状态，生成天气/经济/社交事件

设计原则：
- World Updater 只做"世界级"决策，不干涉具体 NPC 的 goal/plan
- 通过事件总线广播世界事件，供 NPC 系统消费
"""

import asyncio
import random
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from agent_world.db import get_session, NPCDB, WorldDB
from agent_world.models.world import World


# ============================================================
# 世界事件类型
# ============================================================

class WorldEvent:
    """世界事件基类"""
    def __init__(self, event_type: str, description: str, urgency: float = 0.5):
        self.event_type = event_type
        self.description = description
        self.urgency = urgency
        self.timestamp = datetime.now()

    def to_dict(self) -> dict:
        return {
            "type": self.event_type,
            "description": self.description,
            "urgency": self.urgency,
            "timestamp": self.timestamp.isoformat(),
        }


class WeatherEvent(WorldEvent):
    """天气事件"""
    def __init__(self, weather: str, description: str, urgency: float = 0.2):
        super().__init__("weather", description, urgency)
        self.weather = weather


class EconomyEvent(WorldEvent):
    """经济事件"""
    def __init__(self, description: str, affected_roles: list[str], urgency: float = 0.5):
        super().__init__("economy", description, urgency)
        self.affected_roles = affected_roles


class SocialEvent(WorldEvent):
    """社交事件"""
    def __init__(self, description: str, participants: list[str], urgency: float = 0.4):
        super().__init__("social", description, urgency)
        self.participants = participants


# ============================================================
# World Updater
# ============================================================

class WorldUpdater:
    """
    世界状态刷新服务。
    
    核心职责：
      1. 推进世界时间
      2. LLM/规则评估世界状态
      3. 生成世界事件
      4. 广播事件
      5. 定时刷新循环
    """

    def __init__(
        self,
        llm_available: bool = True,
        refresh_interval_minutes: float = 5.0,
    ):
        self.refresh_interval = refresh_interval_minutes
        self.llm_available = llm_available
        self.reasoner = None
        self._running = False
        self._refresh_count = 0

        if llm_available:
            from agent_world.cognition.reasoner import _get_minimax_credentials
            _, api_key = _get_minimax_credentials()
            if api_key:
                self.reasoner = True
                print(f"[WorldUpdater] LLM 世界评估启用 (间隔 {refresh_interval_minutes} 分钟)")
            else:
                print(f"[WorldUpdater] 未配置 LLM，使用规则评估 (间隔 {refresh_interval_minutes} 分钟)")
        else:
            print(f"[WorldUpdater] LLM 已禁用 (间隔 {refresh_interval_minutes} 分钟)")

        self._event_listeners: list[Callable] = []
        self._event_history: list[WorldEvent] = []
        self._max_history = 50

        # 天气状态
        self._current_weather = "晴朗"
        self._weather_duration = 0

    def add_event_listener(self, callback: Callable):
        """添加事件监听器"""
        self._event_listeners.append(callback)

    def _broadcast_event(self, event: WorldEvent):
        """广播事件到所有监听器"""
        for listener in self._event_listeners:
            try:
                listener(event)
            except Exception as e:
                print(f"[WorldUpdater] 事件广播失败: {e}")

        self._event_history.append(event)
        if len(self._event_history) > self._max_history:
            self._event_history = self._event_history[-self._max_history:]

    def _generate_weather_event(self) -> Optional[WeatherEvent]:
        """生成天气事件"""
        self._weather_duration -= 1
        if self._weather_duration <= 0:
            weathers = ["晴朗", "多云", "小雨", "大雾", "雷阵雨"]
            self._current_weather = random.choice(weathers)
            self._weather_duration = random.randint(3, 10)

            desc_map = {
                "晴朗": "天空晴朗，阳光明媚",
                "多云": "天色阴沉，云层密布",
                "小雨": "淅淅沥沥的小雨",
                "大雾": "浓雾弥漫，视野受限",
                "雷阵雨": "电闪雷鸣，暴雨倾盆",
            }
            return WeatherEvent(
                weather=self._current_weather,
                description=desc_map.get(self._current_weather, self._current_weather),
                urgency=0.2,
            )
        return None

    def _rule_evaluate(self, world: World) -> list[WorldEvent]:
        """基于规则的世界评估"""
        events = []

        # 天气事件
        weather_evt = self._generate_weather_event()
        if weather_evt:
            events.append(weather_evt)
            self._broadcast_event(weather_evt)

        # 夜间事件（只在刚入夜时触发一次）
        if world.is_night() and world.world_time.hour == 20:
            evt = WorldEvent(
                event_type="time",
                description="夜幕降临，村民们陆续回家休息",
                urgency=0.3,
            )
            events.append(evt)
            self._broadcast_event(evt)

        return events

    def _llm_evaluate(self, world: World, npc_summary: str) -> list[WorldEvent]:
        """
        使用 LLM 评估世界状态（未来扩展）。
        当前委托给规则评估。
        """
        return self._rule_evaluate(world)

    def refresh(self) -> dict:
        """
        执行一次世界刷新。
        
        Returns:
            dict 包含 world_state, events, refresh_count
        """
        start = datetime.now()

        with get_session() as conn:
            world_db = WorldDB(conn)
            npc_db = NPCDB(conn)

            world = world_db.get_world()
            if not world:
                return {"error": "世界未初始化"}

            npcs = npc_db.get_all_npcs()

            # 生成世界事件（时间由 GraphNPCEngine 每 tick 推进 30 分钟）
            npc_summary = ", ".join([f"{n.name}({n.role})" for n in npcs[:5]])

            if self.reasoner:
                events = self._llm_evaluate(world, npc_summary)
            else:
                events = self._rule_evaluate(world)

            # 保存世界
            world_db.save_world(world)

        self._refresh_count += 1
        duration_ms = int((datetime.now() - start).total_seconds() * 1000)

        return {
            "world": {
                "name": world.name,
                "time": world.world_time.to_display_str(),
                "is_night": world.is_night(),
                "weather": self._current_weather,
            },
            "npc_count": len(npcs),
            "events": [e.to_dict() for e in events],
            "recent_events": [e.to_dict() for e in self._event_history[-10:]],
            "refreshed_at": start.isoformat(),
            "duration_ms": duration_ms,
            "refresh_count": self._refresh_count,
        }

    async def run_periodically(self):
        """持续运行定时刷新循环"""
        self._running = True
        print(f"[WorldUpdater] 启动定时刷新，每 {self.refresh_interval} 分钟一次")
        while self._running:
            try:
                result = self.refresh()
                print(
                    f"[WorldUpdater] 刷新 #{result['refresh_count']} | "
                    f"时间: {result['world']['time']} | "
                    f"天气: {result['world']['weather']} | "
                    f"事件: {len(result['events'])}"
                )
            except Exception as e:
                print(f"[WorldUpdater] 刷新出错: {e}")
            await asyncio.sleep(self.refresh_interval * 60)

    def stop(self):
        """停止定时刷新"""
        self._running = False
        print("[WorldUpdater] 已停止")


# ============================================================
# 全局实例
# ============================================================

_world_updater: Optional[WorldUpdater] = None


def get_world_updater() -> WorldUpdater:
    global _world_updater
    if _world_updater is None:
        _world_updater = WorldUpdater()
    return _world_updater


def init_world_updater(
    llm_available: bool = True,
    refresh_interval_minutes: float = 5.0,
) -> WorldUpdater:
    global _world_updater
    _world_updater = WorldUpdater(
        llm_available=llm_available,
        refresh_interval_minutes=refresh_interval_minutes,
    )
    return _world_updater
