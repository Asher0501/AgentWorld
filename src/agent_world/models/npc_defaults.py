"""
NPC 默认数据 — 16 个 NPC 构成一个小型社会模拟
"""

from .npc import NPC, NPCRole, NPCStatus, Position, PhysicalAttributes, PersonaTags
from datetime import datetime, timedelta

def make_farmer(name: str, seed: int = 0) -> NPC:
    """农民 NPC"""
    physical = PhysicalAttributes(
        energy_capacity=120.0,
        health=95.0,
        recovery_speed=0.8,
        age=28 + seed * 5,
    )
    persona = PersonaTags(
        work_ethic="勤奋",
        social_class="平民",
        reputation="好",
        interests=["农作物", "牧畜"],
        personality=["内向", "耐心"],
    )
    return NPC(
        attributes={"strength":10,"intelligence":10,"charisma":10,"endurance":10,"wisdom":10,"_recent_info":"家族世代务农，继承了祖上的土地。"},
        name=name,
        role=NPCRole.FARMER,
        physical=physical,
        persona_tags=persona,
        position=Position(zone_id="farm"),
        vitality=100.0,
        inventory=["小麦"] * 15 + ["金币"] * 5,
    )

def make_farmer2(name: str, seed: int = 0):
    """农民（女）NPC"""
    physical = PhysicalAttributes(
        energy_capacity=100.0,
        health=90.0,
        recovery_speed=0.7,
        age=26 + seed * 3,
    )
    persona = PersonaTags(
        work_ethic="勤奋",
        social_class="平民",
        reputation="好",
        interests=["蔬菜", "家禽", "孩子"],
        personality=["温和", "热情"],
    )
    return NPC(
        attributes={"strength":10,"intelligence":10,"charisma":10,"endurance":10,"wisdom":10,"_recent_info":"嫁到本村后和丈夫一起务农，还养了些鸡鸭。"},
        name=name,
        role=NPCRole.FARMER,
        physical=physical,
        persona_tags=persona,
        position=Position(zone_id="farm"),
        vitality=100.0,
        inventory=["蔬菜"] * 10 + ["金币"] * 3,
    )

def make_merchant(name: str, seed: int = 0) -> NPC:
    """商人 NPC"""
    physical = PhysicalAttributes(
        energy_capacity=100.0,
        health=90.0,
        recovery_speed=1.0,
        age=35 + seed * 5,
    )
    persona = PersonaTags(
        work_ethic="勤奋",
        social_class="商人",
        reputation="普通",
        interests=["金币", "货物"],
        personality=["谨慎", "外向"],
    )
    return NPC(
        attributes={"strength":10,"intelligence":10,"charisma":10,"endurance":10,"wisdom":10,"_recent_info":"在市场摆摊多年，积累了丰富的商业经验。"},
        name=name,
        role=NPCRole.MERCHANT,
        physical=physical,
        persona_tags=persona,
        position=Position(zone_id="market"),
        vitality=100.0,
        inventory=["金币"] * 10 + ["货物"] * 5,
    )

def make_market_owner(name: str, seed: int = 0):
    """市场女摊主 NPC"""
    physical = PhysicalAttributes(
        energy_capacity=90.0,
        health=85.0,
        recovery_speed=0.9,
        age=45 + seed * 5,
    )
    persona = PersonaTags(
        work_ethic="勤劳",
        social_class="平民",
        reputation="好",
        interests=["面包", "顾客"],
        personality=["热情", "爱聊天"],
    )
    return NPC(
        attributes={"strength":10,"intelligence":10,"charisma":10,"endurance":10,"wisdom":10,"_recent_info":"在市场摆面包摊二十年，老街坊都认识她。"},
        name=name,
        role=NPCRole.MERCHANT,
        physical=physical,
        persona_tags=persona,
        position=Position(zone_id="market"),
        vitality=100.0,
        inventory=["面包"] * 15 + ["金币"] * 8,
    )

def make_artisan(name: str, seed: int = 0):
    """工匠 NPC"""
    physical = PhysicalAttributes(
        energy_capacity=130.0,
        health=95.0,
        recovery_speed=0.9,
        age=38 + seed * 5,
    )
    persona = PersonaTags(
        work_ethic="刻苦",
        social_class="工匠",
        reputation="好",
        interests=["打铁", "锻造", "工具"],
        personality=["沉默", "专注"],
    )
    return NPC(
        attributes={"strength":10,"intelligence":10,"charisma":10,"endurance":10,"wisdom":10,"_recent_info":"祖传铁匠，手艺全村一流。"},
        name=name,
        role=NPCRole.ARTISAN,
        physical=physical,
        persona_tags=persona,
        position=Position(zone_id="market"),
        vitality=100.0,
        inventory=["铁锭"] * 15 + ["金币"] * 10,
    )

def make_guard(name: str, seed: int = 0) -> NPC:
    """守卫 NPC"""
    physical = PhysicalAttributes(
        energy_capacity=130.0,
        health=100.0,
        recovery_speed=1.2,
        age=25 + seed * 8,
    )
    persona = PersonaTags(
        work_ethic="忠诚",
        social_class="平民",
        reputation="好",
        interests=["剑术", "巡逻"],
        personality=["外向", "警觉"],
        special_traits=["退役老兵"] if seed > 0 else [],
    )
    return NPC(
        attributes={"strength":10,"intelligence":10,"charisma":10,"endurance":10,"wisdom":10,"_recent_info":"曾在王国军队服役，后转任城市守卫。"},
        name=name,
        role=NPCRole.GUARD,
        physical=physical,
        persona_tags=persona,
        position=Position(zone_id="barracks"),
        vitality=100.0,
        inventory=["铁锭"] * 5 + ["金币"] * 8,
    )

def make_guard_leader(name: str, seed: int = 0):
    """卫兵队长 NPC"""
    physical = PhysicalAttributes(
        energy_capacity=140.0,
        health=100.0,
        recovery_speed=1.0,
        age=42 + seed * 5,
    )
    persona = PersonaTags(
        work_ethic="纪律",
        social_class="军官",
        reputation="敬畏",
        interests=["训练", "武器", "阅兵"],
        personality=["严厉", "公正"],
        special_traits=["老兵"],
    )
    return NPC(
        attributes={"strength":10,"intelligence":10,"charisma":10,"endurance":10,"wisdom":10,"_recent_info":"曾率队击退山贼，保卫了村庄。"},
        name=name,
        role=NPCRole.GUARD,
        physical=physical,
        persona_tags=persona,
        position=Position(zone_id="barracks"),
        vitality=100.0,
        inventory=["武器"] * 5 + ["金币"] * 20,
    )

def make_scholar(name: str, seed: int = 0) -> NPC:
    """学者 NPC"""
    physical = PhysicalAttributes(
        energy_capacity=80.0,
        health=80.0,
        recovery_speed=0.7,
        age=40 + seed * 3,
    )
    persona = PersonaTags(
        work_ethic="勤奋",
        social_class="学者",
        reputation="好",
        interests=["书籍", "历史", "星象"],
        personality=["内向", "好奇"],
        special_traits=["夜猫子"],
    )
    return NPC(
        attributes={"strength":10,"intelligence":10,"charisma":10,"endurance":10,"wisdom":10,"_recent_info":"在王国图书馆任职多年，研究古代历史。"},
        name=name,
        role=NPCRole.SCHOLAR,
        physical=physical,
        persona_tags=persona,
        position=Position(zone_id="library"),
        vitality=100.0,
        inventory=["书籍"] * 5 + ["金币"] * 15,
    )

def make_librarian(name: str, seed: int = 0):
    """图书管理员 NPC（女）"""
    physical = PhysicalAttributes(
        energy_capacity=75.0,
        health=85.0,
        recovery_speed=0.6,
        age=30 + seed * 3,
    )
    persona = PersonaTags(
        work_ethic="认真",
        social_class="学者",
        reputation="好",
        interests=["藏书", "整理", "园艺"],
        personality=["安静", "细致"],
    )
    return NPC(
        attributes={"strength":10,"intelligence":10,"charisma":10,"endurance":10,"wisdom":10,"_recent_info":"把图书馆管理得井井有条，知道每本书的位置。"},
        name=name,
        role=NPCRole.SCHOLAR,
        physical=physical,
        persona_tags=persona,
        position=Position(zone_id="library"),
        vitality=100.0,
        inventory=["书籍"] * 8 + ["金币"] * 5,
    )

def make_healer(name: str, seed: int = 0) -> NPC:
    """治疗师 NPC"""
    physical = PhysicalAttributes(
        energy_capacity=85.0,
        health=85.0,
        recovery_speed=1.3,
        age=45 + seed * 3,
    )
    persona = PersonaTags(
        work_ethic="仁慈",
        social_class="平民",
        reputation="好",
        interests=["草药", "医术", "阅读"],
        personality=["内向", "温和"],
        special_traits=["药师"] if seed > 0 else [],
    )
    return NPC(
        attributes={"strength":10,"intelligence":10,"charisma":10,"endurance":10,"wisdom":10,"_recent_info":"在神庙学习医术多年，治愈了无数病患。"},
        name=name,
        role=NPCRole.HEALER,
        physical=physical,
        persona_tags=persona,
        position=Position(zone_id="temple"),
        vitality=100.0,
        inventory=["药水"] * 8 + ["金币"] * 12,
    )

def make_monk(name: str, seed: int = 0):
    """僧人 NPC"""
    physical = PhysicalAttributes(
        energy_capacity=90.0,
        health=95.0,
        recovery_speed=1.5,
        age=55 + seed * 5,
    )
    persona = PersonaTags(
        work_ethic="禅定",
        social_class="僧人",
        reputation="敬重",
        interests=["修行", "佛法", "化缘"],
        personality=["平和", "寡言"],
        special_traits=["素食"],
    )
    return NPC(
        attributes={"strength":10,"intelligence":10,"charisma":10,"endurance":10,"wisdom":10,"_recent_info":"在村中寺庙修行三十年，深得村民敬重。"},
        name=name,
        role=NPCRole.HEALER,
        physical=physical,
        persona_tags=persona,
        position=Position(zone_id="temple"),
        vitality=100.0,
        inventory=["佛经"] * 3 + ["金币"] * 5,
    )

def make_herbalist(name: str, seed: int = 0):
    """采药女 NPC"""
    physical = PhysicalAttributes(
        energy_capacity=110.0,
        health=90.0,
        recovery_speed=1.0,
        age=22 + seed * 3,
    )
    persona = PersonaTags(
        work_ethic="勤奋",
        social_class="平民",
        reputation="好",
        interests=["草药", "山林", "动物"],
        personality=["活泼", "勇敢"],
    )
    return NPC(
        attributes={"strength":10,"intelligence":10,"charisma":10,"endurance":10,"wisdom":10,"_recent_info":"从小在山里长大，认识上百种草药。"},
        name=name,
        role=NPCRole.HEALER,
        physical=physical,
        persona_tags=persona,
        position=Position(zone_id="forest"),
        vitality=100.0,
        inventory=["草药"] * 12 + ["金币"] * 2,
    )

def make_hunter(name: str, seed: int = 0):
    """猎人 NPC"""
    physical = PhysicalAttributes(
        energy_capacity=120.0,
        health=100.0,
        recovery_speed=1.2,
        age=32 + seed * 5,
    )
    persona = PersonaTags(
        work_ethic="坚韧",
        social_class="平民",
        reputation="普通",
        interests=["打猎", "山林", "皮货"],
        personality=["独立", "警觉"],
    )
    return NPC(
        attributes={"strength":10,"intelligence":10,"charisma":10,"endurance":10,"wisdom":10,"_recent_info":"在森林里打猎十余年，熟知每一片区域。"},
        name=name,
        role=NPCRole.WANDERER,
        physical=physical,
        persona_tags=persona,
        position=Position(zone_id="forest"),
        vitality=100.0,
        inventory=["皮毛"] * 5 + ["金币"] * 3,
    )

def make_tavern_owner(name: str, seed: int = 0):
    """酒馆老板 NPC"""
    physical = PhysicalAttributes(
        energy_capacity=100.0,
        health=95.0,
        recovery_speed=1.0,
        age=50 + seed * 3,
    )
    persona = PersonaTags(
        work_ethic="勤快",
        social_class="商人",
        reputation="好",
        interests=["酿酒", "待客", "听八卦"],
        personality=["热情", "好客"],
    )
    return NPC(
        attributes={"strength":10,"intelligence":10,"charisma":10,"endurance":10,"wisdom":10,"_recent_info":"经营这家酒馆二十年，认识全村所有人。"},
        name=name,
        role=NPCRole.MERCHANT,
        physical=physical,
        persona_tags=persona,
        position=Position(zone_id="tavern"),
        vitality=100.0,
        inventory=["酒"] * 20 + ["金币"] * 30,
    )

def make_tavern_helper(name: str, seed: int = 0):
    """酒馆服务员 NPC（老陈女儿）"""
    physical = PhysicalAttributes(
        energy_capacity=110.0,
        health=90.0,
        recovery_speed=1.3,
        age=18 + seed * 2,
    )
    persona = PersonaTags(
        work_ethic="勤快",
        social_class="平民",
        reputation="好",
        interests=["唱歌", "八卦", "帮忙"],
        personality=["开朗", "乖巧"],
    )
    return NPC(
        attributes={"strength":10,"intelligence":10,"charisma":10,"endurance":10,"wisdom":10,"_recent_info":"从小在酒馆帮忙，熟悉每一位客人。"},
        name=name,
        role=NPCRole.WANDERER,
        physical=physical,
        persona_tags=persona,
        position=Position(zone_id="tavern"),
        vitality=100.0,
        inventory=["金币"] * 5,
    )

def make_messenger(name: str, seed: int = 0):
    """杂役少年 NPC（满村跑）"""
    physical = PhysicalAttributes(
        energy_capacity=150.0,
        health=100.0,
        recovery_speed=2.0,
        age=14 + seed * 2,
    )
    persona = PersonaTags(
        work_ethic="机灵",
        social_class="平民",
        reputation="普通",
        interests=["跑腿", "零食", "看热闹"],
        personality=["活泼", "好奇"],
        special_traits=["话多"],
    )
    return NPC(
        attributes={"strength":10,"intelligence":10,"charisma":10,"endurance":10,"wisdom":10,"_recent_info":"帮村里人跑腿送信挣几个铜板。"},
        name=name,
        role=NPCRole.WANDERER,
        physical=physical,
        persona_tags=persona,
        position=Position(zone_id="village_square"),
        vitality=100.0,
        inventory=["纸张"] * 5 + ["金币"] * 1,
    )

# 所有 16 个 NPC 的完整配置
DEFAULT_NPCS = [
    # 农民 / 农田 (3)
    ("老张", NPCRole.FARMER, make_farmer, 0),
    ("田嫂", NPCRole.FARMER, make_farmer2, 0),
    # 市场 / 商业 (4)
    ("王老板", NPCRole.MERCHANT, make_merchant, 0),
    ("铁匠王", NPCRole.ARTISAN, make_artisan, 0),
    ("张大娘", NPCRole.MERCHANT, make_market_owner, 0),
    # 酒馆 (2)
    ("老陈", NPCRole.MERCHANT, make_tavern_owner, 0),
    ("陈小梅", NPCRole.WANDERER, make_tavern_helper, 0),
    # 军营 (2)
    ("赵铁柱", NPCRole.GUARD, make_guard, 0),
    ("方统领", NPCRole.GUARD, make_guard_leader, 0),
    # 图书馆 (2)
    ("李夫子", NPCRole.SCHOLAR, make_scholar, 0),
    ("林秀英", NPCRole.SCHOLAR, make_librarian, 0),
    # 神庙 (2)
    ("孙大夫", NPCRole.HEALER, make_healer, 0),
    ("慧明", NPCRole.HEALER, make_monk, 0),
    # 森林 (2)
    ("刘猎户", NPCRole.WANDERER, make_hunter, 0),
    ("翠花", NPCRole.HEALER, make_herbalist, 0),
    # 无固定场所 (1)
    ("小虎子", NPCRole.WANDERER, make_messenger, 0),
]

# 小型测试集：6 个 NPC（精简经济模型）
SMALL_NPCS = [
    # 农民 (2)
    ("老张", NPCRole.FARMER, make_farmer, 0),
    ("田嫂", NPCRole.FARMER, make_farmer2, 0),
    # 市场 / 商业 (3)
    ("王老板", NPCRole.MERCHANT, make_merchant, 0),
    ("铁匠王", NPCRole.ARTISAN, make_artisan, 0),
    ("张大娘", NPCRole.MERCHANT, make_market_owner, 0),
    # 酒馆 (1)
    ("老陈", NPCRole.MERCHANT, make_tavern_owner, 0),
]


def create_diverse_npcs(small: bool = False) -> list[NPC]:
    """创建 NPC 列表"""
    source = SMALL_NPCS if small else DEFAULT_NPCS
    npcs = []
    for name, role, factory, seed in source:
        npcs.append(factory(name, seed))
    return npcs
