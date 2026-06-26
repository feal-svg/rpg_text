# -*- coding: utf-8 -*-
"""
검과 마법의 텍스트 RPG  ―  D&D식 d20 전투 + 캐릭터 성장(레벨/아이템)

전투 규칙(D&D 5e 풍):
  - 명중: 1d20 + 명중보너스 ≥ 대상 AC 이면 명중
  - 자연 20 = 무조건 명중 + 치명타(피해 주사위 2배)
  - 자연 1  = 무조건 빗나감
  - 피해 = 무기/주문 주사위 + 능력 수정치 (+마법 보너스)
  - 명중보너스 = 숙련 보너스 + 능력 수정치 + 무기 마법보너스
  - AC = 10 + 민첩 수정치 + 방어구/장신구 보너스

실행:  python rpg.py
"""

import random
import json
import os
import sys
import time

SAVE_FILE = "rpg_save.json"
NG_MULT = 1.0          # 2회차(NG+) 난이도 배율 — 던전/보스 진입 시 플레이어 회차로 설정
NG_STEP = 0.4          # 회차당 적 강화율

# ── 밸런스 미세조정용 상수 (여기 숫자만 바꾸면 난이도 튜닝 가능) ──
BASE_HP_BONUS = 6      # 1레벨 시작 HP에 더해지는 기본치 (초반 생존성 ↑, 특히 마법사 즉사 방지)
LEVELUP_HP_FLOOR = True # 레벨업 HP 굴림이 너무 낮게 나오면 히트다이스 절반으로 보정

# ── 희귀도/지역 난이도 보정 상수 ──
LEVEL_RARE_STEP = 0.025   # 캐릭터 레벨 1당 던전 전리품 희귀도 보정 증가
SHOP_LEVEL_RARE_STEP = 0.04   # 상점: 레벨 1당 희귀도 보정 증가 (레벨이 오를수록 좋은 물건↑)
REGION_RARE_STEP = 0.12   # 지역 1단계(난이도)당 희귀도 보정 증가 (깊은 던전일수록 좋은 전리품↑)
REGION_DIFF_STEP = 0.10   # 지역 1단계당 적 HP·공격 강화율 (깊은 던전일수록 강함)
REGION_REWARD_STEP = 0.14 # 지역 1단계당 XP·골드 보상 증가 (위험에 비례한 보상)

# ─────────────────────────────────────────────────────────────
#  색상 / 출력 유틸
# ─────────────────────────────────────────────────────────────
class C:
    RESET = "\033[0m"; BOLD = "\033[1m"; DIM = "\033[2m"
    RED = "\033[31m"; GREEN = "\033[32m"; YELLOW = "\033[33m"
    BLUE = "\033[34m"; MAGENTA = "\033[35m"; CYAN = "\033[36m"
    WHITE = "\033[37m"; GRAY = "\033[90m"


def cprint(text, color="", bold=False):
    print((C.BOLD if bold else "") + color + text + C.RESET)


def line(ch="─", n=48):
    print(C.GRAY + ch * n + C.RESET)


def slow(text, color="", delay=0.012):
    for ch in text:
        sys.stdout.write(color + ch + C.RESET)
        sys.stdout.flush()
        time.sleep(delay)
    print()


def ask(prompt):
    try:
        return input(C.CYAN + prompt + C.RESET).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return "quit"


def pause():
    ask("\n[Enter]로 계속... ")


# ─────────────────────────────────────────────────────────────
#  주사위
# ─────────────────────────────────────────────────────────────
def d20(advantage=False, disadvantage=False):
    a = random.randint(1, 20)
    if advantage and not disadvantage:
        return max(a, random.randint(1, 20))
    if disadvantage and not advantage:
        return min(a, random.randint(1, 20))
    return a


def roll(die):
    """die=[n, sides] -> (합계, [개별눈]) """
    n, s = die
    rolls = [random.randint(1, s) for _ in range(n)]
    return sum(rolls), rolls


def fmt_die(die):
    return f"{die[0]}d{die[1]}"


def mod_str(m):
    return f"+{m}" if m >= 0 else str(m)


# ─────────────────────────────────────────────────────────────
#  아이템 / 장비  (모든 주사위는 [n, sides] 리스트로 저장 → JSON 호환)
# ─────────────────────────────────────────────────────────────
RARITY = {
    # 등급: (가중치, 마법보너스(+enchant), 색상)
    "일반": (60, 0, C.WHITE),
    "고급": (24, 1, C.GREEN),
    "희귀": (11, 2, C.BLUE),
    "영웅": (4,  3, C.MAGENTA),
    "전설": (1,  4, C.YELLOW),
}

WEAPONS = [  # (이름, 피해주사위)
    ("단검",       [1, 4]),
    ("레이피어",   [1, 8]),
    ("장검",       [1, 8]),
    ("전투 도끼",  [1, 10]),
    ("대검",       [2, 6]),
    ("워해머",     [1, 12]),
    ("활",         [1, 12]),
    ("방패",       [1, 6]),
    ("마법 지팡이", [1, 6]),
    ("책",         [1, 4]),
    ("보주",       [1, 4]),
]
ARMORS = [  # (이름, 기본 AC 보너스, 분류)
    ("마법사 로브", 1, "로브"),
    ("가죽 갑옷",   2, "경갑"),
    ("사슬 갑옷",   4, "중갑"),
    ("판금 갑옷",   6, "중장갑"),
]
ARMOR_CAT = {n: c for n, _ac, c in ARMORS}      # 이름 → 분류
ACCESSORIES = [  # (이름, 부여 스탯키)
    ("수호의 반지",   "ac"),
    ("힘의 팔찌",     "atk_bonus"),
    ("정밀의 부적",   "dmg_bonus"),
    ("활력의 목걸이", "max_hp"),
    ("질풍의 부적",   "init"),
]

# ── 직업별 갑옷 숙련(분류) ──  미숙련 갑옷 착용 시 AC 절반 + 공격 불리점
ARMOR_PROF = {
    "전사":   {"로브", "경갑", "중갑", "중장갑"},
    "마법사": {"로브"},
    "도적":   {"로브", "경갑"},
}

# ── 전직 시 갑옷 숙련 변화(전직명: add/remove 분류) ──
ADV_ARMOR_MODS = {
    "버서커":   {"remove": {"중장갑"}},   # 기동성 위주 → 중장갑 상실
    "검성":     {"remove": {"중장갑"}},   # 정밀·민첩 → 중장갑 상실
    "강령술사": {"add": {"경갑"}},        # 경갑 숙련 획득
    "파괴왕":   {"remove": {"중갑"}},      # 광기의 경량화 → 평갑(중갑) 상실
    "검신":     {"remove": {"중장갑"}},   # 상위도 중장갑 없음(유지)
    "사령왕":   {"add": {"경갑"}},        # 상위도 경갑 유지
    # ── 추가 직업 ──
    "레인저 나이트": {"add": {"중갑"}},     # 평갑(중갑) 숙련 획득
    "워든":     {"add": {"중갑"}},        # 상위도 평갑 유지
    "배틀메이지": {"add": {"경갑", "중갑"}}, # 평갑(중갑)까지 숙련 — 전사급 방어
    "마검군주": {"add": {"경갑", "중갑"}},  # 상위도 평갑 유지
    # 팔라딘/철벽군주는 전사 기본(중장갑 포함) 그대로 유지 → 보정 없음
}


def armor_profs(p):
    """베이스 직업 + 전직 보정을 반영한 실제 숙련 갑옷 분류 집합."""
    cats = set(ARMOR_PROF.get(p.cls, set()))
    for adv in (getattr(p, "advanced", None), getattr(p, "advanced2", None)):
        mod = ADV_ARMOR_MODS.get(adv)
        if mod:
            cats |= mod.get("add", set())
            cats -= mod.get("remove", set())
    return cats

# ── 무기 특화(스펙) ── 생성 시 접두사로 방향성 부여
WEAPON_SPECS = {
    "평범한": {"weight": 38},
    "예리한": {"weight": 16, "dmg": 3, "desc": "순수 추가 피해"},
    "연타의": {"weight": 16, "multidie": True, "desc": "주사위 개수↑·크기↓ (2d…)"},
    "신속한": {"weight": 14, "init": 3, "desc": "선제 굴림 보너스"},
    "수호의": {"weight": 10, "ac": 2, "desc": "AC 보너스(방어형)"},
}

# ── 마법사(INT) 무기 전용 수식어 ──  지팡이·책에만 등장
MAGE_WEAPON_SPECS = {
    "평범한": {"weight": 38},
    "현자의": {"weight": 18, "atk_bonus": 2, "desc": "주문 명중 +2"},
    "방벽의": {"weight": 14, "ac": 2, "desc": "AC +2(보호)"},
    "예지의": {"weight": 12, "init": 3, "desc": "선제 굴림 보너스"},
}
# ── 갑옷 특화(스펙) ──
ARMOR_SPECS = {
    "평범한": {"weight": 45},
    "견고한": {"weight": 22, "ac": 1, "desc": "AC 추가"},
    "가벼운": {"weight": 18, "init": 2, "desc": "선제 굴림 보너스"},
    "보호의": {"weight": 15, "max_hp": 6, "desc": "최대 HP 추가"},
}

# ── 무기 종류별 공격 능력치(명중·피해 수정치 출처) ──
WEAPON_STAT = {
    "단검": "dex", "레이피어": "dex", "활": "dex",       # 민첩
    "장검": "str", "전투 도끼": "str", "대검": "str", "워해머": "str", "방패": "str",  # 근력
    "마법 지팡이": "int", "책": "int", "보주": "int",   # 지능
}
# ── 무기 종류별 고유 특성(생성 시 기본 부여) ──
WEAPON_TRAITS = {
    "단검":       {"init": 4, "atk_bonus": 2},        # 최고 명중·최고 선제(정밀/속공)
    "레이피어":   {"init": 2, "ac": 1},              # 선제권 + AC
    "장검":       {"atk_bonus": 2},                  # 명중 보너스
    "전투 도끼":  {"dmg_bonus": 3},                  # 주사위 외 고정 추가피해
    "대검":       {"dmg_bonus": 1},                  # 2d6 큰 주사위 + 고정피해(치명·안정 특화)
    "워해머":     {"dmg_bonus": 4, "atk_bonus": -2}, # 큰 피해 + 명중 패널티
    "활":         {"atk_bonus": -2},                 # 1d12 저격(큰 주사위·치명 특화, 명중 패널티)
    "방패":       {"ac": 2},                         # 공격을 포기하고 AC를 챙기는 방어 무기
    "마법 지팡이": {"atk_bonus": 2, "dmg_bonus": -2}, # 명중 보너스 + 무기피해 패널티
    "책":         {"mp_regen": 2},                   # 턴당 MP +2 회복(지속 시전형, 근접 약함)
    "보주":       {"spell_dmg": 3, "atk_bonus": -2},  # 주문 피해↑·주문 명중↓(유리대포 시전)
}
# ── 갑옷 분류별 고유 특성 ──
ARMOR_TRAITS = {
    "로브":   {"atk_bonus": 2},                      # 명중 보너스
    "경갑":   {"init": 2},                            # 선제권 보너스
    "중갑":   {"max_hp": 8},                          # 체력 보너스(평갑)
    "중장갑": {"max_hp": 14, "init": -2},             # 높은 체력 + 선제권 패널티(중갑)
}


def weapon_base_name(item):
    """무기 아이템의 기본 종류명(단검/장검 등)을 반환."""
    if not item:
        return None
    if item.get("base"):
        return item["base"]
    nm = item.get("name", "")
    for w, _ in WEAPONS:
        if w in nm:
            return w
    return None


def _merge_traits(stats, traits):
    for k, v in traits.items():
        stats[k] = stats.get(k, 0) + v
    return stats


def _smaller_die(s):
    order = [4, 6, 8, 10, 12]
    return order[max(0, order.index(s) - 1)] if s in order else s


def roll_spec(table):
    keys = list(table)
    return random.choices(keys, weights=[table[k]["weight"] for k in keys], k=1)[0]


def roll_rarity(bonus=0.0):
    """bonus(≥0)가 클수록 상위 희귀도 확률↑.
    등급이 높을수록(전설 쪽) 보정의 영향을 크게 받도록 가중치를 보정한다."""
    keys = list(RARITY)
    weights = [RARITY[k][0] * (1 + max(0.0, bonus) * i) for i, k in enumerate(keys)]
    return random.choices(keys, weights=weights, k=1)[0]


def make_weapon(level=1, name=None, die=None, rarity=None, spec=None, rarity_bonus=0.0):
    if name is None:
        name, die = random.choice(WEAPONS)
    die = list(die)
    rarity = rarity or roll_rarity(rarity_bonus)
    ench = RARITY[rarity][1]
    spec_table = MAGE_WEAPON_SPECS if WEAPON_STAT.get(name) == "int" else WEAPON_SPECS
    spec = spec or roll_spec(spec_table)
    sp = spec_table[spec]
    stats = {"die": die, "atk_bonus": ench, "dmg_bonus": ench}
    if "dmg" in sp:
        stats["dmg_bonus"] += sp["dmg"] + level // 6
    if "atk_bonus" in sp:
        stats["atk_bonus"] += sp["atk_bonus"]
    if sp.get("multidie"):
        stats["die"] = [die[0] + 1, _smaller_die(die[1])]
    if "init" in sp:
        stats["init"] = sp["init"] + level // 8
    if "ac" in sp:
        stats["ac"] = sp["ac"]
    _merge_traits(stats, WEAPON_TRAITS.get(name, {}))     # 무기 종류 고유 특성
    adj = "" if spec == "평범한" else f"{spec} "
    full = f"{adj}{name}" + (f" +{ench}" if ench else "")
    value = 25 + ench * 45 + level * 3 + (10 if spec != "평범한" else 0)
    return {"kind": "equip", "slot": "weapon", "name": f"{rarity} {full}", "base": name,
            "rarity": rarity, "stats": stats, "value": value, "spec": spec}


def make_armor(level=1, name=None, base_ac=None, rarity=None, spec=None, allowed_cats=None, rarity_bonus=0.0):
    if name is None:
        pool = ARMORS
        if allowed_cats:                                  # 상점: 숙련 분류만 등장
            pool = [a for a in ARMORS if a[2] in allowed_cats] or ARMORS
        name, base_ac, cat = random.choice(pool)
    else:
        cat = ARMOR_CAT.get(name, "경갑")
    rarity = rarity or roll_rarity(rarity_bonus)
    ench = RARITY[rarity][1]
    spec = spec or roll_spec(ARMOR_SPECS)
    sp = ARMOR_SPECS[spec]
    stats = {"ac": base_ac + ench}
    if "ac" in sp:
        stats["ac"] += sp["ac"]
    if "init" in sp:
        stats["init"] = sp["init"]
    if "max_hp" in sp:
        stats["max_hp"] = sp["max_hp"] + level // 4
    _merge_traits(stats, ARMOR_TRAITS.get(cat, {}))       # 갑옷 분류 고유 특성
    adj = "" if spec == "평범한" else f"{spec} "
    full = f"{adj}{name}" + (f" +{ench}" if ench else "")
    value = 25 + stats["ac"] * 12 + level * 3 + (10 if spec != "평범한" else 0)
    return {"kind": "equip", "slot": "armor", "name": f"{rarity} {full}",
            "rarity": rarity, "stats": stats, "value": value, "spec": spec, "category": cat}


def make_accessory(level=1, name=None, key=None, rarity=None, rarity_bonus=0.0):
    if name is None:
        name, key = random.choice(ACCESSORIES)
    rarity = rarity or roll_rarity(rarity_bonus)
    ench = RARITY[rarity][1]
    mult = 4 if key == "max_hp" else (2 if key == "init" else 1)
    amount = (ench + 1) * mult
    stats = {key: amount}
    value = 25 + amount * 10 + level * 3
    return {"kind": "equip", "slot": "accessory", "name": f"{rarity} {name}",
            "rarity": rarity, "stats": stats, "value": value}


SPECIAL_ACCESSORIES = {
    # 특수 몬스터 확정 드랍.  grant = 부여 패시브(PASSIVES), stats = 추가 능력치
    "처형자의 인장": {"grant": "예리함",  "stats": {"dmg_bonus": 3}, "desc": "치명타 19~20 + 추가피해"},
    "사신의 인장":   {"grant": "필멸",    "stats": {"dmg_bonus": 4}, "desc": "치명타 18~20 + 추가피해"},
    "흡혈귀의 송곳니": {"grant": "흡혈",    "stats": {"max_hp": 12}, "desc": "흡혈 + 최대HP"},
    "불굴의 핵":     {"grant": "재생",    "stats": {"ac": 2},      "desc": "매턴 재생 + AC"},
    "질풍신의 깃털": {"grant": "신속",    "stats": {"init": 4},    "desc": "선제 + 추가 선제"},
    "현자의 눈동자": {"grant": "주문 강화", "stats": {"atk_bonus": 2}, "desc": "주문 피해 ×1.5 + 명중"},
    "뇌신의 룬":     {"grant": "분노",     "stats": {"atk_bonus": 2, "dmg_bonus": 1}, "desc": "저체력 피해↑ + 명중·추가피해"},
    "수호자의 문장": {"grant": "수호",     "stats": {"ac": 2, "max_hp": 10}, "desc": "받는 피해 -3 + AC·최대HP"},
}


def make_special_accessory(name):
    info = SPECIAL_ACCESSORIES[name]
    return {"kind": "equip", "slot": "accessory", "name": f"★{name}", "rarity": "전설",
            "stats": dict(info["stats"]), "value": 300, "grant": info["grant"], "special": True}


def make_equipment(level=1, rarity=None, armor_cats=None, rarity_bonus=0.0):
    maker = random.choice([make_weapon, make_armor, make_accessory])
    if maker is make_armor:
        return make_armor(level, rarity=rarity, allowed_cats=armor_cats, rarity_bonus=rarity_bonus)
    return maker(level, rarity=rarity, rarity_bonus=rarity_bonus)


def make_potion(kind="hp", greater=False):
    if kind == "hp":
        die = [4, 4] if greater else [2, 4]
        flat = 4 if greater else 2
        nm = "상급 치유 물약" if greater else "치유 물약"
        return {"kind": "potion", "name": nm, "effect": "hp", "die": die, "flat": flat,
                "value": 50 if greater else 25}
    else:
        die = [4, 6] if greater else [2, 6]
        flat = 4 if greater else 2
        nm = "상급 마나 물약" if greater else "마나 물약"
        return {"kind": "potion", "name": nm, "effect": "mp", "die": die, "flat": flat,
                "value": 50 if greater else 25}


STAT_KR = {"atk_bonus": "명중", "dmg_bonus": "추가피해", "ac": "AC", "max_hp": "최대HP",
           "init": "선제", "die": "피해", "mp_regen": "매턴MP", "max_mp": "최대MP",
           "spell_dmg": "주문피해"}


def stats_str(stats):
    parts = []
    for k, v in stats.items():
        if k == "die":
            parts.append(f"피해 {fmt_die(v)}")
        else:
            parts.append(f"{STAT_KR.get(k, k)}{mod_str(v)}")     # 음수는 -N 으로 표시
    return ", ".join(parts)


def weapon_stat_tag(item):
    """무기라면 명중·피해 수정치의 출처 능력치(근력/민첩/지능)를 반환. 무기가 아니면 ''."""
    if not item or item.get("slot") != "weapon":
        return ""
    st = WEAPON_STAT.get(weapon_base_name(item))
    return ABILITY_KR.get(st, "") if st else ""


def item_label(item):
    if item["kind"] == "equip":
        col = RARITY[item["rarity"]][2]
        cat = f" [{item['category']}]" if item.get("category") else ""
        wtag = weapon_stat_tag(item)
        base = f" {C.GRAY}<{wtag} 기반>{C.RESET}" if wtag else ""
        grant = f" {C.CYAN}[패시브:{item['grant']}]{C.RESET}" if item.get("grant") else ""
        return f"{col}{item['name']}{C.RESET}{cat}{base} ({stats_str(item['stats'])}){grant}"
    return f"{item['name']} ({fmt_die(item['die'])}+{item['flat']} {item['effect'].upper()})"


# ─────────────────────────────────────────────────────────────
#  스킬
# ─────────────────────────────────────────────────────────────
# kind: weapon(무기공격), spell(주문공격), heal, multi
SKILLS = {
    "강타":      {"mp": 4, "kind": "weapon", "mult": 2, "desc": "무기 피해 주사위 2배 (명중 굴림)"},
    "방패 가르기": {"mp": 6, "kind": "weapon", "mult": 2, "flat": 4, "adv": True,
                "status": "기절", "status_turns": 1,
                "desc": "이점 강타 + 추가피해 4, 명중 시 1턴 기절"},
    "파이어볼":   {"mp": 5, "kind": "spell", "die": [3, 6], "element": "화염",
                "status": "화상", "status_turns": 3,
                "desc": "3d6 화염, 명중 시 3턴 화상(1d6)"},
    "블리자드":   {"mp": 9, "kind": "spell", "die": [5, 6], "element": "냉기",
                "status": "빙결", "status_turns": 2,
                "desc": "5d6 냉기, 명중 시 2턴 빙결(불리점)"},
    "힐":        {"mp": 6, "kind": "heal", "die": [2, 8], "desc": "2d8 + 지능 수정치 회복"},
    "독칼":      {"mp": 4, "kind": "weapon", "mult": 1, "status": "중독", "status_turns": 3,
                "desc": "명중 시 3턴 중독 (매턴 1d4)"},
    "연속 베기":  {"mp": 7, "kind": "multi", "hits": [2, 3], "desc": "무기로 2~3회 공격 (각각 명중 굴림)"},

    # ── 전직(상위 직업) 전용 스킬 ──
    # 전사 계열
    "광란의 베기": {"mp": 6, "kind": "multi", "hits": [2, 4], "desc": "2~4회 광란의 연타"},
    "피의 갈증":   {"mp": 8, "kind": "multi", "hits": [2, 2], "low_hp_mult": 1.5,
                "desc": "무기로 2회 연타. 자신 HP 50% 미만이면 각 타격 추가 피해 ×1.5 (분노 패시브와 중첩)"},
    "성스러운 일격": {"mp": 7, "kind": "weapon", "mult": 2, "flat": 4, "status": "기절", "status_turns": 1,
                  "desc": "심판의 일격, 명중 시 1턴 기절"},
    "치유의 기도":  {"mp": 8, "kind": "heal", "die": [3, 8], "desc": "3d8 + 지능 수정치 회복"},
    "일섬":       {"mp": 6, "kind": "weapon", "mult": 2, "adv": True, "desc": "이점으로 굴리는 정밀 베기"},
    "연환검":     {"mp": 9, "kind": "multi", "hits": [3, 4], "desc": "3~4회 연환 베기"},
    # 마법사 계열
    "메테오":     {"mp": 12, "kind": "spell", "die": [6, 6], "element": "화염", "status": "화상",
                "status_turns": 3, "desc": "6d6 화염 폭격 + 화상"},
    "마나 폭발":   {"mp": 10, "kind": "spell", "die": [5, 8], "element": "냉기", "desc": "5d8 마력 폭발"},
    "인페르노":    {"mp": 9, "kind": "spell", "die": [4, 6], "element": "화염", "status": "화상",
                "status_turns": 3, "desc": "4d6 화염 + 화상"},
    "빙결 폭풍":   {"mp": 11, "kind": "spell", "die": [4, 8], "element": "냉기", "status": "빙결",
                "status_turns": 2, "desc": "4d8 냉기 + 빙결"},
    "생명 흡수":   {"mp": 8, "kind": "drain", "die": [3, 8], "element": "독", "desc": "3d8, 피해 절반만큼 HP 흡수"},
    "역병":       {"mp": 6, "kind": "spell", "die": [3, 6], "element": "독", "status": "중독",
                "status_turns": 4, "desc": "3d6 + 4턴 강력한 중독"},
    # 도적 계열
    "암살":       {"mp": 8, "kind": "weapon", "mult": 3, "adv": True, "desc": "이점 치명 일격 (무기 3배)"},
    "맹독 도포":   {"mp": 5, "kind": "weapon", "mult": 2, "status": "중독", "status_turns": 5,
                "desc": "무기 2배 + 명중 시 5턴 맹독"},
    "급소 사격":   {"mp": 6, "kind": "weapon", "mult": 2, "flat": 6, "desc": "급소를 노린 추가 피해"},
    "올가미":     {"mp": 5, "kind": "weapon", "mult": 1, "status": "기절", "status_turns": 2,
                "desc": "명중 시 2턴 기절"},
    "그림자 분신": {"mp": 9, "kind": "multi", "hits": [3, 5], "desc": "3~5회 분신 연타"},
    "비수 난무":   {"mp": 7, "kind": "multi", "hits": [2, 4], "desc": "비수 2~4회 난무"},

    # ── 2차 전직(마스터) 궁극기 ──
    "파멸의 일격": {"mp": 12, "kind": "weapon", "mult": 4, "flat": 10, "adv": True,
                "desc": "이점 무기 4배 + 추가피해 10의 파멸"},
    "심판":       {"mp": 12, "kind": "weapon", "mult": 3, "flat": 6, "status": "기절", "status_turns": 2,
                "desc": "심판의 일격, 2턴 기절"},
    "무한검무":   {"mp": 14, "kind": "multi", "hits": [4, 6], "desc": "4~6회 무한의 검무"},
    "절멸":       {"mp": 20, "kind": "spell", "die": [8, 6], "element": "화염", "status": "화상",
                "status_turns": 3, "desc": "8d6 절멸 화염 + 화상"},
    "원소 융합":   {"mp": 18, "kind": "spell", "die": [6, 8], "element": "냉기", "status": "빙결",
                "status_turns": 2, "desc": "6d8 융합 냉기 + 빙결"},
    "죽음의 손길": {"mp": 16, "kind": "drain", "die": [5, 8], "element": "독", "desc": "5d8, 피해 절반 HP 흡수"},
    "그림자 처형": {"mp": 14, "kind": "weapon", "mult": 4, "adv": True, "status": "중독", "status_turns": 4,
                "desc": "이점 4배 처형 + 4턴 중독"},
    "관통 사격":   {"mp": 12, "kind": "weapon", "mult": 3, "flat": 10, "adv": True,
                "desc": "이점 관통 일격(무기 3배 + 추가피해 10)"},
    "환영 난무":   {"mp": 14, "kind": "multi", "hits": [4, 7], "desc": "4~7회 환영 난무"},

    # ── 전직 선택지용 신규 스킬 ──
    # 1차 선택지
    "광폭한 일격": {"mp": 7, "kind": "weapon", "mult": 3, "flat": 2, "desc": "무기 3배 + 추가피해 2"},
    "천벌의 빛":   {"mp": 8, "kind": "weapon", "mult": 2, "flat": 4, "status": "기절", "status_turns": 1,
                "desc": "신성한 일격, 1턴 기절"},
    "발도":       {"mp": 6, "kind": "weapon", "mult": 2, "flat": 3, "adv": True, "desc": "이점 발도술 일격"},
    "비전 작렬":   {"mp": 10, "kind": "spell", "die": [6, 6], "element": "냉기", "desc": "6d6 비전 작렬"},
    "용암 분출":   {"mp": 9, "kind": "spell", "die": [5, 6], "element": "화염", "status": "화상",
                "status_turns": 3, "desc": "5d6 화염 + 화상"},
    "영혼 흡수":   {"mp": 9, "kind": "drain", "die": [4, 8], "element": "독", "desc": "4d8, 피해 절반 HP 흡수"},
    "그림자 베기": {"mp": 8, "kind": "weapon", "mult": 3, "adv": True, "desc": "이점 그림자 베기(3배)"},
    "독화살":     {"mp": 6, "kind": "weapon", "mult": 2, "status": "중독", "status_turns": 3,
                "desc": "명중 시 3턴 중독"},
    "환영검":     {"mp": 8, "kind": "multi", "hits": [3, 4], "desc": "3~4회 환영검"},
    # 2차 선택지
    "학살":       {"mp": 14, "kind": "multi", "hits": [4, 6], "desc": "4~6회 학살의 연타"},
    "신성 폭발":   {"mp": 16, "kind": "spell", "die": [5, 8], "element": "화염", "status": "기절",
                "status_turns": 1, "desc": "5d8 신성 폭발 + 기절"},
    "검기 방출":   {"mp": 13, "kind": "weapon", "mult": 4, "flat": 8, "desc": "무기 4배 + 추가피해 8의 검기"},
    "공허 폭발":   {"mp": 18, "kind": "spell", "die": [7, 6], "element": "냉기", "status": "빙결",
                "status_turns": 2, "desc": "7d6 공허 + 빙결"},
    "대화염 폭풍": {"mp": 18, "kind": "spell", "die": [6, 8], "element": "화염", "status": "화상",
                "status_turns": 3, "desc": "6d8 대화염 + 화상"},
    "생명 갈취":   {"mp": 18, "kind": "drain", "die": [6, 8], "element": "독", "desc": "6d8, 피해 절반 HP 흡수"},
    "암습":       {"mp": 15, "kind": "weapon", "mult": 4, "flat": 5, "adv": True, "desc": "이점 암습(4배 +5)"},
    "폭풍 사격":   {"mp": 13, "kind": "multi", "hits": [3, 6], "desc": "3~6회 폭풍 사격"},
    "천 개의 칼날": {"mp": 16, "kind": "multi", "hits": [5, 7], "desc": "5~7회 천 개의 칼날"},

    # ── 직업별 방어 스킬 ──
    "방패 올리기": {"mp": 5, "kind": "buff", "ac": 4, "reduce": 3, "turns": 3,
                "desc": "3턴 AC+4·받는 피해-3 (탱킹)"},
    "마력 방벽":   {"mp": 8, "kind": "buff", "ac": 2, "reduce": 6, "turns": 3, "cleanse": True,
                "desc": "3턴 피해-6·AC+2, 상태이상 해제(방벽)"},
    "그림자 장막": {"mp": 6, "kind": "buff", "ac": 2, "evade": True, "turns": 2,
                "desc": "2턴 피격 불리점(회피)+AC+2"},

    # ── 추가 직업 계열 스킬 ──
    # 팔라딘(전사) — 회복 없이 깡스텟·방어로 버티는 중장갑 기사
    "응징의 일격": {"mp": 6, "kind": "weapon", "mult": 2, "flat": 4, "desc": "묵직한 응징의 일격(+추가피해 4)"},
    "파쇄 강타":   {"mp": 7, "kind": "weapon", "mult": 2, "flat": 2, "status": "기절", "status_turns": 1,
                "desc": "방패로 내려쳐 1턴 기절"},
    "강철 의지":   {"mp": 6, "kind": "buff", "ac": 3, "reduce": 3, "turns": 3,
                "desc": "3턴 AC+3·받는 피해-3 (불굴의 방어)"},
    "철벽의 심판": {"mp": 13, "kind": "weapon", "mult": 3, "flat": 6, "status": "기절", "status_turns": 2,
                "desc": "무기 3배+추가피해 6, 2턴 기절"},
    "강철 요새":   {"mp": 12, "kind": "buff", "ac": 4, "reduce": 6, "turns": 4,
                "desc": "4턴 AC+4·받는 피해-6 (난공불락)"},
    # 레인저 나이트(도적) — 치명·기교보다 다재다능한 평갑 기사
    "쾌속 연격":   {"mp": 6, "kind": "multi", "hits": [2, 3], "desc": "2~3회 빠른 연격"},
    "정밀 사격":   {"mp": 6, "kind": "weapon", "mult": 2, "flat": 4, "desc": "균형 잡힌 정밀 일격(+추가피해 4)"},
    "포박의 올가미": {"mp": 6, "kind": "weapon", "mult": 1, "status": "기절", "status_turns": 2,
                  "desc": "명중 시 2턴 기절(포박)"},
    "폭풍 난격":   {"mp": 14, "kind": "multi", "hits": [4, 6], "desc": "4~6회 폭풍 난격"},
    "필중의 일격": {"mp": 15, "kind": "weapon", "mult": 3, "flat": 10, "adv": True,
                "desc": "이점 필중 일격(무기 3배+추가피해 10)"},
    # 배틀메이지(마법사) — 주문 위력은 약하나 전사급으로 버티는 평갑 마법사
    "비전 강타":   {"mp": 6, "kind": "spell", "die": [3, 6], "element": "화염", "desc": "3d6 비전 강타"},
    "충격파":     {"mp": 8, "kind": "spell", "die": [4, 6], "element": "냉기", "status": "빙결",
                "status_turns": 2, "desc": "4d6 냉기 충격파 + 빙결"},
    "마력 갑주":   {"mp": 7, "kind": "buff", "ac": 3, "reduce": 3, "turns": 3,
                "desc": "3턴 AC+3·받는 피해-3 (마력 갑주)"},
    "비전 폭렬":   {"mp": 16, "kind": "spell", "die": [6, 6], "element": "화염", "status": "화상",
                "status_turns": 3, "desc": "6d6 비전 폭렬 + 화상"},
    "마력 보루":   {"mp": 14, "kind": "buff", "ac": 4, "reduce": 6, "turns": 4, "cleanse": True,
                "desc": "4턴 피해-6·AC+4, 상태이상 해제"},
}

# ─────────────────────────────────────────────────────────────
#  상태이상
#   dot      : 매턴 피해 주사위 (None=없음)
#   skip     : True면 그 턴 행동 불가
#   self_dis : True면 걸린 본인의 공격에 불리점
#   (공통 규칙) 상태이상 보유 = "취약" → 그 대상을 노리는 공격은 이점
# ─────────────────────────────────────────────────────────────
STATUS_DEFS = {
    "중독": {"dot": [1, 4], "skip": False, "self_dis": False, "icon": "☠", "color": C.GREEN,
            "element": "독",   "save": "con"},
    "화상": {"dot": [1, 6], "skip": False, "self_dis": False, "icon": "🔥", "color": C.RED,
            "element": "화염", "save": "con"},
    "빙결": {"dot": None,   "skip": False, "self_dis": True,  "icon": "❄", "color": C.CYAN,
            "element": "냉기", "save": "str"},
    "기절": {"dot": None,   "skip": True,  "self_dis": False, "icon": "💫", "color": C.YELLOW,
            "element": "물리", "save": "con"},
}

# ─────────────────────────────────────────────────────────────
#  직업
# ─────────────────────────────────────────────────────────────
# abilities: str/dex/con/int,  hit_die,  attack_stat(주 공격 능력),  starter(시작장비)
CLASSES = {
    "전사": {
        "desc": "높은 HP와 근력. d10 히트 다이스, 정면 돌파형.",
        "abilities": {"str": 16, "dex": 12, "con": 15, "int": 8},
        "hit_die": 10, "attack_stat": "str", "mp_base": 24, "mp_grow": 5,
        "skills": {1: "강타", 3: "방패 올리기", 4: "방패 가르기"},
        "starter": [("weapon", "장검", [1, 8]), ("armor", "가죽 갑옷", 2)],
        "color": C.RED,
    },
    "마법사": {
        "desc": "강력한 주문. d6 히트 다이스, 지능 기반 주문 명중.",
        "abilities": {"str": 8, "dex": 13, "con": 12, "int": 16},
        "hit_die": 6, "attack_stat": "int", "mp_base": 28, "mp_grow": 4,
        "skills": {1: "파이어볼", 3: "힐", 4: "마력 방벽", 6: "블리자드"},
        "starter": [("weapon", "마법 지팡이", [1, 6]), ("armor", "마법사 로브", 1)],
        "color": C.BLUE,
    },
    "도적": {
        "desc": "민첩 기반. d8 히트 다이스, 연타와 독.",
        "abilities": {"str": 11, "dex": 16, "con": 13, "int": 12},
        "hit_die": 8, "attack_stat": "dex", "mp_base": 26, "mp_grow": 4,
        "skills": {1: "독칼", 3: "그림자 장막", 4: "연속 베기"},
        "starter": [("weapon", "단검", [1, 4]), ("armor", "가죽 갑옷", 2)],
        "color": C.GREEN,
    },
}
ASI_LEVELS = {4, 8, 12, 16, 19}  # 능력치 상승(Ability Score Improvement) 레벨
ABILITY_KR = {"str": "근력", "dex": "민첩", "con": "건강", "int": "지능"}

# ─────────────────────────────────────────────────────────────
#  상위 직업(전직)  ―  ADVANCE_LEVEL 이상에서 마을에서 전직
#   base     : 전직 가능한 베이스 직업
#   abilities: 전직 시 능력치 보너스   max_hp/max_mp: 즉시 보너스
#   skills   : {레벨: 스킬} (베이스 스킬에 더해 습득)
# ─────────────────────────────────────────────────────────────
ADVANCE_LEVEL = 5
ADVANCED = {
    # auto: 전직 시 자동 습득,  choices: 2개 중 1개 선택
    # ── 전사 계열 ──
    "버서커":   {"base": "전사", "desc": "공격 특화. 광폭한 연타와 폭딜.",
              "abilities": {"str": 2, "con": 1}, "max_hp": 15, "max_mp": 5,
              "auto": "광란의 베기", "choices": ["피의 갈증", "광폭한 일격"],
              "passive": "분노", "color": C.RED},
    "성기사":   {"base": "전사", "desc": "방어와 신성. 기절 일격과 치유.",
              "abilities": {"str": 1, "con": 2}, "max_hp": 25, "max_mp": 10,
              "auto": "성스러운 일격", "choices": ["치유의 기도", "천벌의 빛"],
              "passive": "수호", "color": C.YELLOW},
    "검성":     {"base": "전사", "desc": "정밀과 치명. 이점 일격과 연환검.",
              "abilities": {"str": 2, "dex": 1}, "max_hp": 12, "max_mp": 8,
              "auto": "일섬", "choices": ["연환검", "발도"],
              "passive": "예리함", "color": C.WHITE},
    "팔라딘":   {"base": "전사", "desc": "수비 특화. 압도적 방어와 인내.",
              "abilities": {"con": 3}, "max_hp": 28, "max_mp": 6,
              "auto": "응징의 일격", "choices": ["파쇄 강타", "강철 의지"],
              "passive": "불굴", "passive2": "가시", "color": C.YELLOW},
    # ── 마법사 계열 ──
    "대마법사": {"base": "마법사", "desc": "파괴 주문의 극치.",
              "abilities": {"int": 3}, "max_hp": 8, "max_mp": 16,
              "auto": "메테오", "choices": ["마나 폭발", "비전 작렬"],
              "passive": "주문 강화", "color": C.MAGENTA},
    "원소술사": {"base": "마법사", "desc": "화염과 냉기를 자유자재로.",
              "abilities": {"int": 2, "dex": 1}, "max_hp": 10, "max_mp": 14,
              "auto": "인페르노", "choices": ["빙결 폭풍", "용암 분출"],
              "passive": "원소 지배", "color": C.CYAN},
    "강령술사": {"base": "마법사", "desc": "생명 흡수와 역병.",
              "abilities": {"int": 2, "con": 1}, "max_hp": 14, "max_mp": 14,
              "auto": "생명 흡수", "choices": ["역병", "영혼 흡수"],
              "passive": "흡혈", "color": C.GREEN},
    "배틀메이지": {"base": "마법사", "desc": "주문 위력을 희생한 전사급 생존력.",
              "abilities": {"int": 1, "con": 2}, "max_hp": 26, "max_mp": 10,
              "auto": "비전 강타", "choices": ["충격파", "마력 갑주"],
              "passive": "불굴", "color": C.BLUE},
    # ── 도적 계열 ──
    "암살자":   {"base": "도적", "desc": "치명적 일격과 맹독.",
              "abilities": {"dex": 3}, "max_hp": 10, "max_mp": 12,
              "auto": "암살", "choices": ["맹독 도포", "그림자 베기"],
              "passive": "예리함", "color": C.RED},
    "추적자":   {"base": "도적", "desc": "급소 공격과 함정.",
              "abilities": {"dex": 2, "str": 1}, "max_hp": 14, "max_mp": 10,
              "auto": "급소 사격", "choices": ["올가미", "독화살"],
              "passive": "신속", "color": C.GREEN},
    "그림자무희": {"base": "도적", "desc": "분신과 비수 난무.",
              "abilities": {"dex": 2, "int": 1}, "max_hp": 12, "max_mp": 16,
              "auto": "그림자 분신", "choices": ["비수 난무", "환영검"],
              "passive": "분신술", "color": C.BLUE},
    "레인저 나이트": {"base": "도적", "desc": "기교보다 다재다능함.",
              "abilities": {"dex": 2, "con": 1}, "max_hp": 16, "max_mp": 10,
              "auto": "쾌속 연격", "choices": ["정밀 사격", "포박의 올가미"],
              "passive": "균형", "color": C.GREEN},
}

# ─────────────────────────────────────────────────────────────
#  패시브  ―  type별로 전투 중 자동 발동
# ─────────────────────────────────────────────────────────────
PASSIVES = {
    "분노":    {"type": "low_hp_dmg",  "value": 1.5, "desc": "HP 50% 미만일 때 주는 피해 ×1.5"},
    "광폭":    {"type": "low_hp_dmg",  "value": 2.0, "desc": "HP 50% 미만일 때 주는 피해 ×2"},
    "암살 본능": {"type": "high_hp_dmg", "value": 2.0, "desc": "HP가 가득일 때 주는 피해 ×2"},
    "수호":    {"type": "dmg_reduce",  "value": 3,   "desc": "받는 피해 -3"},
    "불굴":    {"type": "dmg_reduce",  "value": 4,   "desc": "받는 피해 -4 (깡스텟 방어)"},
    "철벽":    {"type": "dmg_reduce",  "value": 6,   "desc": "받는 피해 -6"},
    "균형":    {"type": "weapon_flat", "value": 2,   "desc": "무기 명중·피해 +2 (다재다능)"},
    "완숙":    {"type": "weapon_flat", "value": 4,   "desc": "무기 명중·피해 +4 (완숙의 경지)"},
    "예리함":  {"type": "crit_range",  "value": 19,  "desc": "19~20에 치명타"},
    "필멸":    {"type": "crit_range",  "value": 18,  "desc": "18~20에 치명타"},
    "주문 강화": {"type": "spell_power", "value": 1.5, "desc": "주문 피해 ×1.5"},
    "대주문":  {"type": "spell_power", "value": 2.0, "desc": "주문 피해 ×2"},
    "원소 지배": {"type": "status_dc",  "value": 2,   "desc": "상태이상 내성 DC +2"},
    "흡혈":    {"type": "lifesteal",   "value": 0.2, "desc": "가한 피해의 20%만큼 HP 회복"},
    "대흡혈":  {"type": "lifesteal",   "value": 0.35,"desc": "가한 피해의 35%만큼 HP 회복"},
    "신속":    {"type": "init",        "value": 5,   "desc": "선제 굴림 +5"},
    "재생":    {"type": "regen",       "value": 5,   "desc": "매 턴 시작 시 HP +5"},
    "분신술":  {"type": "multi_extra", "value": 1,   "desc": "다단히트 스킬 공격 +1회"},
    "가시":    {"type": "reflect",     "value": 1,   "desc": "공격을 받을 때마다 적에게 1 피해 반사"},
    "강철 가시": {"type": "reflect",    "value": 2,   "desc": "공격을 받을 때마다 적에게 2 피해 반사"},
    "초월":    {"type": "lifesteal",   "value": 0.5, "desc": "가한 피해의 50%만큼 HP 회복(윤회)"},
}

# ─────────────────────────────────────────────────────────────
#  2차 전직(마스터)  ―  ADVANCE_LEVEL_2 이상 + 1차 전직 완료 시
# ─────────────────────────────────────────────────────────────
ADVANCE_LEVEL_2 = 12
TIER2 = {
    # base = 요구되는 1차 전직,  choices: 2개 중 1개 선택(궁극기)
    "파괴왕":   {"base": "버서커", "desc": "광기의 화신. 폭딜과 흡혈.",
              "abilities": {"str": 3, "con": 2}, "max_hp": 35, "max_mp": 10,
              "choices": ["파멸의 일격", "학살"], "passive": "대흡혈", "color": C.RED},
    "성전사":   {"base": "성기사", "desc": "불굴의 수호자. 철벽과 재생.",
              "abilities": {"str": 2, "con": 3}, "max_hp": 45, "max_mp": 15,
              "choices": ["심판", "신성 폭발"], "passive": "재생", "color": C.YELLOW},
    "검신":     {"base": "검성", "desc": "검의 극의. 모든 일격이 치명적.",
              "abilities": {"str": 3, "dex": 2}, "max_hp": 28, "max_mp": 12,
              "choices": ["무한검무", "검기 방출"], "passive": "필멸", "color": C.WHITE},
    "철벽군주": {"base": "팔라딘", "desc": "난공불락의 강철 군주",
              "abilities": {"str": 1, "con": 4}, "max_hp": 45, "max_mp": 8,
              "choices": ["철벽의 심판", "강철 요새"], "passive": "철벽", "passive2": "강철 가시", "color": C.YELLOW},
    "마도왕":   {"base": "대마법사", "desc": "절대 마법. 압도적 주문 위력.",
              "abilities": {"int": 4, "con": 1}, "max_hp": 20, "max_mp": 26,
              "choices": ["절멸", "공허 폭발"], "passive": "대주문", "color": C.MAGENTA},
    "정령왕":   {"base": "원소술사", "desc": "원소의 군주. 상태이상과 주문.",
              "abilities": {"int": 3, "dex": 2}, "max_hp": 24, "max_mp": 24,
              "choices": ["원소 융합", "대화염 폭풍"], "passive": "주문 강화", "color": C.CYAN},
    "사령왕":   {"base": "강령술사", "desc": "죽음의 지배자. 강력한 흡수.",
              "abilities": {"int": 3, "con": 2}, "max_hp": 30, "max_mp": 20,
              "choices": ["죽음의 손길", "생명 갈취"], "passive": "대흡혈", "color": C.GREEN},
    "마검군주": {"base": "배틀메이지", "desc": "마법과 강철의 융합.",
              "abilities": {"int": 2, "con": 3}, "max_hp": 40, "max_mp": 18,
              "choices": ["비전 폭렬", "마력 보루"], "passive": "재생", "color": C.MAGENTA},
    "그림자 군주": {"base": "암살자", "desc": "어둠의 처형자. 치명적 폭딜.",
              "abilities": {"dex": 4, "str": 1}, "max_hp": 24, "max_mp": 18,
              "choices": ["그림자 처형", "암습"], "passive": "암살 본능", "color": C.RED},
    "신궁":     {"base": "추적자", "desc": "백발백중의 명궁.",
              "abilities": {"dex": 3, "str": 2}, "max_hp": 28, "max_mp": 16,
              "choices": ["관통 사격", "폭풍 사격"], "passive": "필멸", "color": C.GREEN},
    "환영무희": {"base": "그림자무희", "desc": "무수한 분신의 춤.",
              "abilities": {"dex": 3, "int": 2}, "max_hp": 26, "max_mp": 24,
              "choices": ["환영 난무", "천 개의 칼날"], "passive": "신속", "color": C.BLUE},
    "워든":     {"base": "레인저 나이트", "desc": "전장을 지배하는 만능의 기사.",
              "abilities": {"dex": 3, "str": 1, "con": 1}, "max_hp": 30, "max_mp": 14,
              "choices": ["폭풍 난격", "필중의 일격"], "passive": "완숙", "color": C.CYAN},
}


# ─────────────────────────────────────────────────────────────
#  플레이어
# ─────────────────────────────────────────────────────────────
class Player:
    def __init__(self, name, cls):
        info = CLASSES[cls]
        self.name = name
        self.cls = cls
        self.level = 1
        self.xp = 0
        self.abilities = dict(info["abilities"])
        self.max_hp = info["hit_die"] + self.mod("con") + BASE_HP_BONUS
        self.max_mp = info["mp_base"]
        self.hp = self.max_hp
        self.mp = self.max_mp
        self.gold = 40
        self.inventory = [make_potion("hp"), make_potion("hp")]
        self.equipment = {"weapon": None, "armor": None,
                          "accessory": None, "accessory2": None}
        self.skills = []
        self.statuses = {}
        self.buffs = []
        self.regions_seen = []
        self.advanced = None
        self.advanced2 = None
        self.ng = 0
        self.bosses_defeated = []
        self.title = None
        self.titles = []
        self.codex_specials = []      # 획득한 특수 장신구 도감 기록
        self.shop_stock = None        # 상점 재고(리롤 전까지 유지)
        # 시작 장비 장착
        for slot, nm, val in info["starter"]:
            if slot == "weapon":
                self.equip(make_weapon(name=nm, die=val, rarity="일반", spec="평범한"))
            else:
                self.equip(make_armor(name=nm, base_ac=val, rarity="일반", spec="평범한"))
        self._learn_for_level()

    # ── 능력치 ──
    def mod(self, ability):
        return (self.abilities[ability] - 10) // 2

    @property
    def proficiency(self):
        return 2 + (self.level - 1) // 4

    @property
    def attack_stat(self):
        return CLASSES[self.cls]["attack_stat"]

    @property
    def disp_title(self):
        parts = [self.cls]
        if getattr(self, "advanced", None):
            parts.append(self.advanced)
        if getattr(self, "advanced2", None):
            parts.append(self.advanced2)
        return "·".join(parts)

    @property
    def disp_color(self):
        if getattr(self, "advanced2", None):
            return TIER2[self.advanced2]["color"]
        adv = getattr(self, "advanced", None)
        return ADVANCED[adv]["color"] if adv else CLASSES[self.cls]["color"]

    def equip_bonus(self, key):
        return sum(it["stats"].get(key, 0) for it in self.equipment.values() if it)

    @property
    def weapon_die(self):
        w = self.equipment["weapon"]
        return w["stats"]["die"] if w else [1, 2]

    @property
    def weapon_stat(self):
        """장착 무기 종류에 따른 공격 능력치(없거나 미지정이면 직업 기본)."""
        w = self.equipment.get("weapon")
        base = weapon_base_name(w) if w else None
        return WEAPON_STAT.get(base, self.attack_stat)

    @property
    def attack_bonus(self):
        return (self.proficiency + self.mod(self.weapon_stat)
                + self.equip_bonus("atk_bonus") + passive_value(self, "weapon_flat", 0))

    @property
    def spell_attack_bonus(self):
        return self.proficiency + self.mod("int") + self.equip_bonus("atk_bonus")

    @property
    def damage_bonus(self):
        return (self.mod(self.weapon_stat) + self.equip_bonus("dmg_bonus")
                + passive_value(self, "weapon_flat", 0))

    @property
    def armor_proficient(self):
        a = self.equipment.get("armor")
        if not a:
            return True
        cat = a.get("category")
        if cat is None:               # 구버전 세이브 호환: 분류 없으면 숙련 취급
            return True
        return cat in armor_profs(self)

    @property
    def ac(self):
        bonus = self.equip_bonus("ac")
        a = self.equipment.get("armor")
        if a and not self.armor_proficient:                 # 미숙련: 갑옷 AC 절반만
            armor_ac = a["stats"].get("ac", 0)
            bonus -= (armor_ac - armor_ac // 2)
        return 10 + self.mod("dex") + bonus + buff_ac(self)

    @property
    def total_max_hp(self):
        return self.max_hp + self.equip_bonus("max_hp")

    # ── 성장 ──
    def xp_to_next(self):
        return int(40 + 30 * (self.level ** 1.6))

    def learn_skill(self, s):
        if s and s not in self.skills:
            self.skills.append(s)
            cprint(f"  새 스킬: {s} — {SKILLS[s]['desc']}", C.MAGENTA)

    def _learn_for_level(self):
        learned = []
        for lv, sk in CLASSES[self.cls]["skills"].items():   # 베이스 직업 스킬만 레벨로 습득
            if self.level >= lv and sk not in self.skills:
                self.skills.append(sk)
                learned.append(sk)
        return learned

    def gain_xp(self, amount):
        self.xp += amount
        cprint(f"  +{amount} XP", C.GRAY)
        while self.xp >= self.xp_to_next():
            self.xp -= self.xp_to_next()
            self.level_up()

    def level_up(self):
        self.level += 1
        info = CLASSES[self.cls]
        hp_roll = random.randint(1, info["hit_die"])
        if LEVELUP_HP_FLOOR:                      # 너무 낮은 굴림은 히트다이스 절반으로 보정
            hp_roll = max(hp_roll, info["hit_die"] // 2)
        gain = hp_roll + self.mod("con")
        gain = max(1, gain)
        self.max_hp += gain
        self.max_mp += info["mp_grow"]
        line()
        cprint(f"★ 레벨 업! Lv.{self.level}", C.YELLOW, bold=True)
        cprint(f"  최대 HP +{gain} (이제 {self.max_hp}),  최대 MP +{info['mp_grow']}", C.YELLOW)
        if self.level in ASI_LEVELS:
            stat = self.attack_stat
            old_con_mod = self.mod("con")
            self.abilities[stat] += 1
            self.abilities["con"] += 1
            if self.mod("con") > old_con_mod:  # 건강 수정치 상승분 소급 적용
                self.max_hp += self.level
            cprint(f"  능력치 상승! {ABILITY_KR[stat]}+1, 건강+1", C.GREEN)
        if (self.level - 1) // 4 != (self.level - 2) // 4:
            cprint(f"  숙련 보너스 +{self.proficiency}", C.GREEN)
        for s in self._learn_for_level():
            cprint(f"  새 스킬: {s} — {SKILLS[s]['desc']}", C.MAGENTA)
        if self.level == ADVANCE_LEVEL and not getattr(self, "advanced", None):
            cprint("  ✦ 전직 가능! 마을 메뉴에서 상위 직업으로 전직할 수 있다.", C.CYAN)
        if self.level == ADVANCE_LEVEL_2 and getattr(self, "advanced", None) \
                and not getattr(self, "advanced2", None):
            cprint("  ✦✦ 2차 전직 가능! 마을에서 마스터 직업으로 전직할 수 있다.", C.CYAN)
        self.hp = self.total_max_hp
        self.mp = self.max_mp
        line()

    def advance(self, name, chosen=None):
        info = ADVANCED[name]
        self.advanced = name
        for a, v in info["abilities"].items():
            self.abilities[a] += v
        self.max_hp += info["max_hp"]
        self.max_mp += info["max_mp"]
        line()
        cprint(f"★ 전직: {self.cls} → {name}!", info["color"], bold=True)
        ab = ", ".join(f"{ABILITY_KR[a]}+{v}" for a, v in info["abilities"].items())
        cprint(f"  최대 HP +{info['max_hp']}, 최대 MP +{info['max_mp']}, {ab}", info["color"])
        pas = PASSIVES[info["passive"]]
        cprint(f"  패시브 습득: {info['passive']} — {pas['desc']}", C.CYAN)
        if info.get("passive2"):
            _p2 = PASSIVES[info["passive2"]]
            cprint(f"  패시브 습득: {info['passive2']} — {_p2['desc']}", C.CYAN)
        if info.get("auto"):
            self.learn_skill(info["auto"])
        self.learn_skill(chosen)
        self.heal_full()
        line()

    def advance2(self, name, chosen=None):
        info = TIER2[name]
        self.advanced2 = name
        for a, v in info["abilities"].items():
            self.abilities[a] += v
        self.max_hp += info["max_hp"]
        self.max_mp += info["max_mp"]
        line()
        cprint(f"★★ 2차 전직: {self.advanced} → {name}!", info["color"], bold=True)
        ab = ", ".join(f"{ABILITY_KR[a]}+{v}" for a, v in info["abilities"].items())
        cprint(f"  최대 HP +{info['max_hp']}, 최대 MP +{info['max_mp']}, {ab}", info["color"])
        pas = PASSIVES[info["passive"]]
        cprint(f"  패시브 습득: {info['passive']} — {pas['desc']}", C.CYAN)
        if info.get("passive2"):
            _p2 = PASSIVES[info["passive2"]]
            cprint(f"  패시브 습득: {info['passive2']} — {_p2['desc']}", C.CYAN)
        if info.get("auto"):
            self.learn_skill(info["auto"])
        self.learn_skill(chosen)
        self.heal_full()
        line()

    def heal_full(self):
        self.hp = self.total_max_hp
        self.mp = self.max_mp

    def is_alive(self):
        return self.hp > 0

    def equip(self, item, slot=None):
        slot = slot or item["slot"]
        old = self.equipment.get(slot)
        self.equipment[slot] = item
        self.hp = min(self.hp, self.total_max_hp)
        return old

    def to_dict(self):
        return self.__dict__

    @classmethod
    def from_dict(cls, d):
        p = cls.__new__(cls)
        p.__dict__.update(d)
        if not hasattr(p, "advanced"):
            p.advanced = None
        if not hasattr(p, "advanced2"):
            p.advanced2 = None
        if not hasattr(p, "ng"):
            p.ng = 0
        if not hasattr(p, "bosses_defeated"):
            p.bosses_defeated = []
        if not hasattr(p, "title"):
            p.title = None
        if not hasattr(p, "titles"):
            p.titles = []
        if not hasattr(p, "codex_specials"):
            p.codex_specials = []
        if not hasattr(p, "shop_stock"):
            p.shop_stock = None
        if getattr(p, "statuses", None) is None:
            p.statuses = {}
        if not hasattr(p, "buffs") or p.buffs is None:
            p.buffs = []
        if not hasattr(p, "regions_seen") or p.regions_seen is None:
            p.regions_seen = []
        if isinstance(getattr(p, "equipment", None), dict):
            for s in ("weapon", "armor", "accessory", "accessory2"):
                p.equipment.setdefault(s, None)      # 구버전 세이브에 슬롯 보강
        return p


# ─────────────────────────────────────────────────────────────
#  몬스터
# ─────────────────────────────────────────────────────────────
MONSTERS = [  # (이름, 아이콘, HP배수, 공격배수, 취약[], 저항[])
    ("슬라임",   "🟢", 0.8, 0.8, ["화염"], ["독"]),
    ("고블린",   "👺", 1.0, 1.0, [], []),
    ("늑대",     "🐺", 1.0, 1.1, [], []),
    ("스켈레톤", "💀", 1.1, 1.0, ["화염"], ["독", "냉기"]),
    ("오크",     "👹", 1.3, 1.2, [], []),
    ("다크엘프", "🧝", 1.2, 1.3, [], []),
    # ── 속성 특화 몬스터 ──
    ("화염 정령", "🔥", 1.1, 1.2, ["냉기"], ["화염", "독"]),
    ("서리 정령", "❄️", 1.1, 1.1, ["화염"], ["냉기"]),
    ("좀비",     "🧟", 1.5, 0.8, ["화염"], ["독"]),
    ("독거미",   "🕷️", 0.9, 1.3, [], ["독"]),
    ("골렘",     "🗿", 1.8, 1.0, [], ["물리"]),
    # ── 추가 일반 몬스터 ──
    ("박쥐 떼",   "🦇", 0.7, 1.0, [], []),
    ("임프",     "😈", 0.9, 1.2, ["냉기"], ["화염"]),
    ("코볼트",   "🦎", 0.8, 0.9, [], []),
    ("독사",     "🐍", 0.85, 1.2, [], ["독"]),
    ("하피",     "🦅", 1.0, 1.2, [], []),
    ("트롤",     "🧌", 1.6, 1.1, ["화염"], []),
    ("미믹",     "📦", 1.2, 1.4, [], []),
]

# ── 일반 몬스터 특수 행동 ──
MONSTER_ABILITIES = {
    "강타":       {"type": "heavy", "mult": 1.6, "desc": "강타(피해 ↑)"},
    "연속 할퀴기": {"type": "multi", "hits": 2, "desc": "2회 연속 공격"},
    "독니":       {"type": "status", "status": "중독", "turns": 3, "desc": "독 물기"},
    "화염 숨결":   {"type": "status", "status": "화상", "turns": 2, "element": "화염", "desc": "화염 숨결"},
    "냉기 숨결":   {"type": "status", "status": "빙결", "turns": 1, "element": "냉기", "desc": "냉기 숨결"},
    "재생":       {"type": "heal", "pct": 0.15, "desc": "체력 재생"},
    "방어 태세":   {"type": "guard", "ac": 4, "desc": "방어(AC ↑)"},
    "광폭화":     {"type": "enrage", "desc": "저체력 시 광폭화"},   # 패시브성(저체력 1회)
}
# 이름 → (보유 능력, 능력 사용 확률)
MONSTER_KIT = {
    "슬라임":   (["재생"], 0.20),
    "고블린":   (["강타"], 0.25),
    "늑대":     (["연속 할퀴기"], 0.30),
    "스켈레톤": (["방어 태세"], 0.28),
    "오크":     (["강타", "광폭화"], 0.30),
    "다크엘프": (["독니", "연속 할퀴기"], 0.32),
    "화염 정령": (["화염 숨결"], 0.35),
    "서리 정령": (["냉기 숨결"], 0.35),
    "좀비":     (["독니", "재생"], 0.28),
    "독거미":   (["독니", "연속 할퀴기"], 0.38),
    "골렘":     (["강타", "방어 태세"], 0.28),
    "박쥐 떼":   (["연속 할퀴기"], 0.40),
    "임프":     (["화염 숨결"], 0.35),
    "코볼트":   (["강타"], 0.25),
    "독사":     (["독니"], 0.40),
    "하피":     (["연속 할퀴기"], 0.35),
    "트롤":     (["강타", "재생"], 0.32),
    "미믹":     (["강타"], 0.32),
}
BOSSES = [  # (이름, 아이콘, HP배수, 공격배수, 피해주사위, 취약[], 저항[])
    ("고블린 족장", "👑", 2.4, 1.6, [2, 6],  [], []),
    ("리치",       "☠️", 2.8, 1.8, [2, 8],  ["화염"], ["독", "냉기"]),
    ("화염 군주",   "😈", 3.0, 1.9, [2, 8],  ["냉기"], ["화염", "독"]),
    ("고대 드래곤", "🐲", 3.4, 2.0, [2, 10], ["냉기"], ["화염", "물리"]),
    ("미노타우로스", "🐂", 2.6, 1.8, [2, 8], [], []),
    ("듀라한",     "🛡️", 2.6, 1.7, [2, 8], ["화염"], ["독", "냉기"]),
]


def region_tier(region):
    """지역의 난이도 단계(0=가장 얕음). REGIONS 정의 순서를 기준으로 한다."""
    if not region:
        return 0
    try:
        return REGIONS.index(region)
    except ValueError:
        return max(0, (region.get("rec", 1) - 1) // 4)


def dungeon_rarity_bonus(lvl, region):
    """던전 전리품 희귀도 보정 = 레벨 보정 + 지역 단계 보정."""
    return lvl * LEVEL_RARE_STEP + region_tier(region) * REGION_RARE_STEP


def make_monster(floor, boss=False, region=None):
    lvl = max(1, floor)
    tier = region_tier(region)
    diff = 1 + REGION_DIFF_STEP * tier            # 지역 난이도 배율(깊을수록 강함)
    reward = 1 + REGION_REWARD_STEP * tier        # 위험에 비례한 보상 배율
    if boss:
        pool = [BOSS_BY_NAME[n] for n in region["bosses"]] if region else BOSSES
        name, icon, hp_m, atk_m, die, weak, resist = random.choice(pool)
    else:
        pool = [MOB_BY_NAME[n] for n in region["mobs"]] if region else MONSTERS
        name, icon, hp_m, atk_m, weak, resist = random.choice(pool)
        die = [1, 6] if lvl < 4 else [1, 8]
    hp = int((8 + lvl * 6) * hp_m * NG_MULT * diff)
    ac = 11 + lvl // 3 + (2 if boss else 0) + tier // 2
    atk_bonus = int(((2 + lvl // 2) * atk_m + (NG_MULT - 1) * 4) * diff)
    dmg_bonus = int((lvl // 3 + (2 if boss else 0) + (NG_MULT - 1) * 4) * diff)
    xp = int((16 + lvl * 8) * (2.4 if boss else 1.0) * NG_MULT * reward)
    gold = int((10 + lvl * 5) * (2.5 if boss else 1.0) * NG_MULT * reward)
    abilities, act_rate = ([], 0.0) if boss else MONSTER_KIT.get(name, ([], 0.0))
    return {"name": name, "icon": icon, "level": lvl, "boss": boss,
            "hp": hp, "max_hp": hp, "ac": ac, "atk_bonus": atk_bonus,
            "dmg_die": die, "dmg_bonus": dmg_bonus,
            "xp": xp, "gold": gold, "statuses": {},
            "weak": list(weak), "resist": list(resist),
            "abilities": list(abilities), "act_rate": act_rate,
            "rarity_bonus": dungeon_rarity_bonus(lvl, region),
            "save": 1 + lvl // 3 + (2 if boss else 0) + tier // 2}   # 상태이상 내성 굴림 보너스


# ── 특수(희귀) 몬스터 ── 낮은 확률로 등장, 처치 시 특수 장신구 확정 드랍
SPECIAL_MONSTERS = [
    # (이름, 아이콘, HP배수, 공격배수, 취약, 저항, 확정드랍 장신구)
    ("그림자 추적자", "🌑", 1.5, 1.7, ["화염"], [],            "처형자의 인장"),
    ("핏빛 사신",     "🩸", 1.7, 1.8, [],       ["독"],         "사신의 인장"),
    ("흡혈 군주",     "🦇", 1.8, 1.5, ["화염"], ["독"],         "흡혈귀의 송곳니"),
    ("황금 골렘",     "🥇", 2.4, 1.1, [],       ["물리", "독"], "불굴의 핵"),
    ("수정 비룡",     "💎", 1.9, 1.6, [],       ["냉기"],       "질풍신의 깃털"),
    ("심연의 망령",   "👻", 1.6, 1.7, ["화염"], ["독", "냉기"], "현자의 눈동자"),
    ("폭풍의 정령왕", "🌪️", 1.8, 1.7, [],       ["냉기"],       "뇌신의 룬"),
    ("불멸의 기사",   "⚜️", 2.2, 1.4, [],       ["물리"],       "수호자의 문장"),
]
SPECIAL_RATE = 0.08   # 일반 조우에서 특수 몬스터 등장 확률


# ── 보스 전용 기술 ──
BOSS_SKILLS = {
    "지옥의 강타": {"type": "attack", "die": [2, 10], "flat": 4, "element": "화염", "desc": "화염 강타"},
    "다단 발톱":   {"type": "multi", "hits": [2, 3], "die": [1, 10], "flat": 2, "desc": "연속 발톱"},
    "공포의 포효": {"type": "status", "status": "기절", "turns": 1, "die": [1, 8], "desc": "포효(기절)"},
    "흡혈 강타":   {"type": "drain", "die": [2, 8], "flat": 4, "desc": "흡혈 강타"},
    "충전":       {"type": "charge", "desc": "강력한 공격 준비"},
    "냉기 브레스": {"type": "status", "status": "빙결", "turns": 2, "die": [2, 8], "flat": 3,
                "element": "냉기", "desc": "냉기 브레스(빙결)"},
    "꼬리 강타":   {"type": "attack", "die": [3, 8], "flat": 5, "desc": "꼬리 강타"},
    "날갯짓":     {"type": "multi", "hits": [2, 4], "die": [1, 8], "flat": 1, "desc": "날갯짓 연타"},
    "공허 폭발":   {"type": "attack", "die": [3, 8], "flat": 6, "element": "냉기", "desc": "공허 폭발"},
    "정신 붕괴":   {"type": "status", "status": "기절", "turns": 2, "die": [1, 8], "desc": "정신 붕괴(기절)"},
    "생명 흡수":   {"type": "drain", "die": [2, 10], "flat": 4, "element": "독", "desc": "생명 흡수"},
    "차원 베기":   {"type": "multi", "hits": [2, 3], "die": [2, 6], "flat": 2, "desc": "차원 베기"},
}

# ── 최종 보스(2차 전직 이후에만 등장, 랜덤) ──
FINAL_BOSS_TEMPLATES = [
    {"name": "마왕 아바돈", "icon": "😈", "intro": "침식이 낳은 첫째 재앙왕 ― 불길의 아바돈이 길을 막는다.",
     "hp_mult": 1.0, "weak": ["냉기"], "resist": ["화염", "독"], "immune": ["중독"],
     "skills": ["지옥의 강타", "다단 발톱", "공포의 포효", "흡혈 강타", "충전"],
     "ult": {"name": "파멸의 운석", "die": [5, 10], "flat": 12, "element": "화염"},
     "loot": {"prefix": "마왕의", "grant": "대흡혈", "weapon_die": [2, 10], "power": 6},
     "phases": [{"at": 0.66, "kind": "enrage", "value": 3, "msg": "아바돈이 분노로 타오른다! (공격 강화)"},
                {"at": 0.33, "kind": "heal", "value": 0.20, "msg": "아바돈이 지옥불을 흡수해 회복한다!"}]},
    {"name": "고룡 바하무트", "icon": "🐉", "intro": "침식에 잠식된 고룡 바하무트가 세계수의 가지 위에서 포효한다.",
     "hp_mult": 1.2, "weak": ["화염"], "resist": ["냉기", "물리"], "immune": ["기절"],
     "skills": ["냉기 브레스", "꼬리 강타", "날갯짓", "충전"],
     "ult": {"name": "절대영도", "die": [4, 12], "flat": 14, "element": "냉기"},
     "loot": {"prefix": "용왕의", "grant": "철벽", "weapon_die": [1, 12], "power": 7},
     "phases": [{"at": 0.6, "kind": "add_skill", "value": ["공포의 포효"], "msg": "바하무트가 비상하며 포효한다!"},
                {"at": 0.3, "kind": "enrage", "value": 4, "msg": "바하무트가 최후의 힘을 끌어올린다!"}]},
    {"name": "공허의 군주", "icon": "🌌", "intro": "세계수의 상처에서 기어 나온 공허의 군주가 현실을 삼킨다.",
     "hp_mult": 0.9, "weak": ["화염"], "resist": ["독", "냉기"], "immune": ["빙결", "중독"],
     "skills": ["공허 폭발", "정신 붕괴", "생명 흡수", "차원 베기", "충전"],
     "ult": {"name": "종말의 빛", "die": [6, 8], "flat": 16, "element": "물리"},
     "loot": {"prefix": "공허의", "grant": "필멸", "weapon_die": [2, 8], "power": 6},
     "phases": [{"at": 0.66, "kind": "heal", "value": 0.15, "msg": "공허가 일렁이며 군주를 치유한다!"},
                {"at": 0.33, "kind": "immune", "value": ["기절"], "msg": "군주가 모든 속박에서 벗어난다!"}]},
]


def make_boss_loot(loot, base_class, ng=0):
    """보스 특성(loot) × 처치 직업(유형) 조합의 전설 장비."""
    pw = loot["power"] + ng * 2          # 회차마다 강화
    grant = loot["grant"]
    pre = loot["prefix"]
    common = {"kind": "equip", "rarity": "전설", "value": 500 + ng * 100,
              "grant": grant, "special": True, "boss_loot": True}
    if base_class == "전사":              # 무기형
        return {**common, "slot": "weapon", "name": f"★{pre} 대검", "base": "대검",
                "stats": {"die": list(loot["weapon_die"]), "atk_bonus": 3 + ng, "dmg_bonus": pw}}
    elif base_class == "마법사":          # 방어구형(로브)
        return {**common, "slot": "armor", "category": "로브", "name": f"★{pre} 법의",
                "stats": {"ac": 3 + pw // 2, "max_hp": pw * 2, "atk_bonus": 3 + ng}}
    else:                                # 도적 → 장신구형
        return {**common, "slot": "accessory", "name": f"★{pre} 비표",
                "stats": {"init": 4 + ng, "dmg_bonus": pw, "atk_bonus": 3 + ng}}


def make_final_boss(level):
    t = random.choice(FINAL_BOSS_TEMPLATES)
    lvl = max(12, level)
    hp = int((150 + lvl * 10) * t["hp_mult"] * NG_MULT)
    return {"name": t["name"], "icon": t["icon"], "intro": t["intro"], "level": lvl,
            "boss": True, "final": True, "hp": hp, "max_hp": hp,
            "ac": 13 + lvl // 4, "atk_bonus": int(4 + lvl // 3 + (NG_MULT - 1) * 5),
            "dmg_die": [2, 8], "dmg_bonus": int(3 + lvl // 5 + (NG_MULT - 1) * 5),
            "xp": int(lvl * 60 * NG_MULT), "gold": int(lvl * 40 * NG_MULT),
            "statuses": {}, "weak": list(t["weak"]), "resist": list(t["resist"]),
            "immune": list(t["immune"]), "save": 4 + lvl // 3, "init": 5 + lvl // 4,
            "skills": list(t["skills"]), "ult": dict(t["ult"]), "loot": dict(t["loot"]),
            "phases": [{**ph, "done": False} for ph in t["phases"]]}


# ── 숨겨진 4번째 보스(전 칭호 달성 시 해금) ──
SECRET_BOSS = {
    "name": "종언의 군주 아브락사스", "icon": "🌑",
    "intro": "침식의 진짜 근원 ― 세계수의 심장에 똬리 튼 종언, 아브락사스.",
    "hp_mult": 1.6, "weak": [], "resist": ["화염", "냉기"],
    "immune": ["기절", "빙결"],                          # 하드 CC만 면역(화상·중독 DoT은 허용)
    "skills": ["지옥의 강타", "냉기 브레스", "공허 폭발", "다단 발톱", "생명 흡수", "충전"],
    "ult": {"name": "종언", "die": [6, 10], "flat": 14, "element": "물리"},
    "loot": {"prefix": "종언의", "grant": "필멸", "weapon_die": [3, 8], "power": 10},
    "phases": [{"at": 0.75, "kind": "enrage", "value": 3, "msg": "아브락사스가 진노한다! (공격 강화)"},
               {"at": 0.50, "kind": "heal", "value": 0.12, "msg": "아브락사스가 차원을 흡수해 재생한다!"},
               {"at": 0.25, "kind": "add_skill", "value": ["꼬리 강타", "차원 베기"],
                "msg": "아브락사스가 모든 권능을 해방한다!"}],
}


def make_secret_boss(level, ng=0):
    t = SECRET_BOSS
    lvl = max(15, level)
    hp = int((200 + lvl * 14) * t["hp_mult"] * NG_MULT)
    # 기본 3페이즈 (회차에 따라 수치 강화)
    phases = [
        {"at": 0.75, "kind": "enrage", "value": 3 + ng, "msg": "아브락사스가 진노한다! (공격 강화)"},
        {"at": 0.50, "kind": "heal", "value": min(0.20, 0.12 + 0.02 * ng),
         "msg": "아브락사스가 차원을 흡수해 재생한다!"},
        {"at": 0.25, "kind": "add_skill", "value": ["꼬리 강타", "차원 베기"],
         "msg": "아브락사스가 모든 권능을 해방한다!"},
    ]
    # ── NG 전용 강화 페이즈 ──
    if ng >= 1:
        phases.append({"at": 0.40, "kind": "enrage", "value": 3 + ng,
                       "msg": "[강화] 멸망의 분노가 폭발한다!"})
    if ng >= 2:
        phases.append({"at": 0.15, "kind": "double", "value": 1,
                       "msg": "[폭주] 종언의 군주가 연속으로 행동하기 시작한다!"})
    if ng >= 3:
        phases.append({"at": 0.30, "kind": "heal", "value": 0.15,
                       "msg": "[재림] 차원이 다시 그를 채운다!"})
    if ng >= 4:
        phases.append({"at": 0.10, "kind": "enrage", "value": 5 + ng,
                       "msg": "[종언] 마지막 권능이 해방된다!"})
    phases.sort(key=lambda ph: -ph["at"])
    for ph in phases:
        ph["done"] = False
    return {"name": t["name"], "icon": t["icon"], "intro": t["intro"], "level": lvl,
            "boss": True, "final": True, "secret": True, "hp": hp, "max_hp": hp,
            "ac": 15 + lvl // 4, "atk_bonus": int(6 + lvl // 3 + (NG_MULT - 1) * 5),
            "dmg_die": [2, 10], "dmg_bonus": int(3 + lvl // 5 + (NG_MULT - 1) * 5),
            "xp": int(lvl * 100 * NG_MULT), "gold": int(lvl * 70 * NG_MULT),
            "statuses": {}, "weak": list(t["weak"]), "resist": list(t["resist"]),
            "immune": list(t["immune"]), "save": 6 + lvl // 3, "init": 7 + lvl // 4,
            "skills": list(t["skills"]), "ult": dict(t["ult"]), "loot": dict(t["loot"]),
            "phases": phases}


def make_special_monster(floor, region=None):
    lvl = max(1, floor)
    tier = region_tier(region)
    diff = 1 + REGION_DIFF_STEP * tier
    reward = 1 + REGION_REWARD_STEP * tier
    pool = [SPECIAL_BY_NAME[n] for n in region["specials"]] if region and region.get("specials") else SPECIAL_MONSTERS
    name, icon, hp_m, atk_m, weak, resist, drop = random.choice(pool)
    hp = int((10 + lvl * 7) * hp_m * NG_MULT * diff)
    ac = 12 + lvl // 3 + tier // 2
    atk_bonus = int(((3 + lvl // 2) * atk_m + (NG_MULT - 1) * 4) * diff)
    dmg_bonus = int((lvl // 3 + 1 + (NG_MULT - 1) * 4) * diff)
    die = [1, 8] if lvl < 4 else [1, 10]
    xp = int((16 + lvl * 8) * 2.0 * NG_MULT * reward)
    gold = int((10 + lvl * 5) * 2.0 * NG_MULT * reward)
    return {"name": name, "icon": icon, "level": lvl, "boss": False, "special": True,
            "hp": hp, "max_hp": hp, "ac": ac, "atk_bonus": atk_bonus,
            "dmg_die": die, "dmg_bonus": dmg_bonus, "xp": xp, "gold": gold, "statuses": {},
            "weak": list(weak), "resist": list(resist), "save": 2 + lvl // 3 + tier // 2,
            "init": 3 + lvl // 4, "special_drop": drop}


# 이름 → 원본 데이터 튜플 (지역별 풀 구성용)
MOB_BY_NAME = {m[0]: m for m in MONSTERS}
BOSS_BY_NAME = {b[0]: b for b in BOSSES}
SPECIAL_BY_NAME = {s[0]: s for s in SPECIAL_MONSTERS}

# ── 지역(테마) ── 각 지역은 전용 몬스터/중간보스/특수몹/도입부/속성 분위기를 가진다
REGIONS = [
    {"name": "고블린 숲", "icon": "🌲", "color": C.GREEN, "rec": 1,
     "desc": "야생 짐승과 고블린이 우글거리는 울창한 숲.",
     "lore": "침식은 이 숲의 가장자리부터 시작되었다. 짐승들이 미쳐 날뛴다.",
     "mobs": ["슬라임", "고블린", "늑대", "박쥐 떼", "코볼트", "하피", "독사", "독거미"],
     "bosses": ["고블린 족장"], "specials": ["그림자 추적자", "흡혈 군주"],
     "intros": ["나뭇잎 사이로 짐승의 눈빛이 번뜩인다.",
                "축축한 흙냄새와 함께 으르렁 소리가 들린다.",
                "덩굴이 우거진 오솔길이 깊은 숲으로 이어진다."]},
    {"name": "잊혀진 묘지", "icon": "💀", "color": C.MAGENTA, "rec": 5,
     "desc": "죽은 자가 잠들지 못하는 안개 낀 묘역.",
     "lore": "죽음의 땅마저 물들어, 잠든 자들이 다시 일어선다.",
     "mobs": ["스켈레톤", "좀비", "다크엘프", "미믹", "임프", "박쥐 떼"],
     "bosses": ["리치", "듀라한"], "specials": ["핏빛 사신", "심연의 망령"],
     "intros": ["부서진 비석 사이로 푸른 도깨비불이 떠다닌다.",
                "발밑에서 마른 뼈가 바스러진다.",
                "차가운 안개가 목덜미를 휘감는다."]},
    {"name": "불의 협곡", "icon": "🌋", "color": C.RED, "rec": 9,
     "desc": "용암이 흐르는 작열하는 화산 협곡.",
     "lore": "대지의 핏줄이 끓어오른다 ― 침식이 잠든 불을 깨웠다.",
     "mobs": ["오크", "화염 정령", "임프", "트롤", "골렘", "코볼트"],
     "bosses": ["화염 군주", "미노타우로스"], "specials": ["핏빛 사신", "폭풍의 정령왕"],
     "intros": ["발갛게 달아오른 바위에서 열기가 솟구친다.",
                "멀리서 용암이 끓는 소리가 울린다.",
                "재가 섞인 뜨거운 바람이 얼굴을 때린다."]},
    {"name": "서리 동굴", "icon": "❄️", "color": C.CYAN, "rec": 12,
     "desc": "끝없는 한기가 스미는 푸른 빙벽의 동굴.",
     "lore": "세계의 숨결이 얼어붙는다. 침식의 한기가 뼛속까지 스민다.",
     "mobs": ["서리 정령", "늑대", "골렘", "트롤", "하피", "스켈레톤"],
     "bosses": ["고대 드래곤", "듀라한"], "specials": ["수정 비룡", "불멸의 기사"],
     "intros": ["입김이 하얗게 얼어붙어 흩날린다.",
                "얼음 벽 너머에서 무언가 움직인다.",
                "발밑의 살얼음이 위태롭게 갈라진다."]},
    {"name": "심연의 미궁", "icon": "🌌", "color": C.BLUE, "rec": 16,
     "desc": "온갖 강대한 존재가 도사린 차원의 끝 미궁.",
     "lore": "여기서부터는 세계가 아니다. 세계수의 상처, 그 안쪽이다.",
     "mobs": ["다크엘프", "골렘", "좀비", "미믹", "트롤", "독거미"],
     "bosses": ["미노타우로스", "듀라한", "고대 드래곤"],
     "specials": ["그림자 추적자", "황금 골렘", "심연의 망령", "불멸의 기사"],
     "intros": ["공간이 일그러지며 별빛이 소용돌이친다.",
                "발소리가 끝없는 어둠 속으로 빨려든다.",
                "보이지 않는 시선이 사방에서 느껴진다."]},
]


# ─────────────────────────────────────────────────────────────
#  전투 핵심 (d20)
# ─────────────────────────────────────────────────────────────
def _statuses(e):
    """플레이어(객체)·몬스터(딕셔너리) 공통으로 상태이상 딕셔너리 반환."""
    if isinstance(e, dict):
        return e.setdefault("statuses", {})
    if getattr(e, "statuses", None) is None:
        e.statuses = {}
    return e.statuses


def _name(e):
    return e["name"] if isinstance(e, dict) else e.name


def _set_hp(e, delta):
    if isinstance(e, dict):
        e["hp"] += delta
    else:
        e.hp += delta


def _elem_lists(e):
    if isinstance(e, dict):
        return e.get("weak", []), e.get("resist", [])
    return getattr(e, "weak", []), getattr(e, "resist", [])


def deal_damage(target, amount, element="물리"):
    """속성 취약(2배)/저항(절반)을 반영해 피해를 적용하고 (최종피해, 표기) 반환."""
    weak, resist = _elem_lists(target)
    note = ""
    if element in weak:
        amount = max(1, amount * 2)
        note = f"  {C.YELLOW}({element} 취약! 2배){C.RESET}"
    elif element in resist:
        amount = max(1, amount // 2)
        note = f"  {C.CYAN}({element} 저항! 절반){C.RESET}"
    _set_hp(target, -amount)
    return amount, note


def _save_bonus(e, ability):
    return e.get("save", 0) if isinstance(e, dict) else e.mod(ability)


def apply_status(e, name, turns, dc=12):
    if isinstance(e, dict) and name in e.get("immune", []):   # 보스 면역
        cprint(f"  {_name(e)}(은)는 {name}에 면역!", C.GRAY)
        return
    st = _statuses(e)
    cur = st.get(name)
    st[name] = {"turns": max(turns, cur["turns"] if cur else 0),
                "dc": max(dc, cur["dc"] if cur else 0)}
    d = STATUS_DEFS[name]
    cprint(f"  {d['icon']} {_name(e)} → {name}! ({turns}턴, 내성 DC{dc})", d['color'])


def is_vulnerable(e):
    return len(_statuses(e)) > 0                 # 상태이상 보유 = 취약(공격자 이점)


def is_impaired(e):
    return any(STATUS_DEFS[n]['self_dis'] for n in _statuses(e))


def _buffs(p):
    if not hasattr(p, "buffs") or p.buffs is None:
        p.buffs = []
    return p.buffs


def buff_ac(p):
    return sum(b.get("ac", 0) for b in _buffs(p))


def buff_reduce(p):
    return sum(b.get("reduce", 0) for b in _buffs(p))


def buff_evade(p):
    return any(b.get("evade") for b in _buffs(p))


def incoming_reduce(p):
    """피격 시 총 피해 감소량 = 패시브(수호/철벽) + 방어 버프."""
    return passive_value(p, "dmg_reduce", 0) + buff_reduce(p)


def apply_buff(p, name, turns, ac=0, reduce=0, evade=False):
    bs = _buffs(p)
    for b in bs:
        if b["name"] == name:          # 재시전 시 갱신
            b.update({"turns": turns, "ac": ac, "reduce": reduce, "evade": evade})
            return
    bs.append({"name": name, "turns": turns, "ac": ac, "reduce": reduce, "evade": evade})


def tick_buffs(p):
    bs = _buffs(p)
    expired = []
    for b in bs:
        b["turns"] -= 1
        if b["turns"] <= 0:
            expired.append(b)
    for b in expired:
        bs.remove(b)
        cprint(f"  ⟪{b['name']}⟫ 효과가 사라졌다.", C.GRAY)


def buff_tags(p):
    bs = _buffs(p)
    if not bs:
        return ""
    parts = []
    for b in bs:
        eff = []
        if b.get("ac"): eff.append(f"AC+{b['ac']}")
        if b.get("reduce"): eff.append(f"감소{b['reduce']}")
        if b.get("evade"): eff.append("회피")
        parts.append(f"{b['name']}({'·'.join(eff)},{b['turns']}T)")
    return "  ".join(parts)


def tick_statuses(e):
    """턴 시작 시: DoT 적용(속성 반영). 행동 불가(기절)면 True 반환. (지속시간 감소는 내성 굴림에서)"""
    st = _statuses(e)
    if not st:
        return False
    skip = False
    for name in list(st.keys()):
        d = STATUS_DEFS[name]
        if d['dot']:
            base, _r = roll(d['dot'])
            final, note = deal_damage(e, base, d['element'])
            cprint(f"  {d['icon']} {_name(e)}: {name} {fmt_die(d['dot'])} → {final} 피해{note}", d['color'])
        if d['skip']:
            skip = True
    return skip


def saving_throws(e):
    """턴 종료 시: 각 상태이상에 내성 굴림(1d20+능력). 성공 시 즉시 해제, 실패 시 1턴 감소."""
    st = _statuses(e)
    for name in list(st.keys()):
        info = st[name]
        d = STATUS_DEFS[name]
        bonus = _save_bonus(e, d['save'])
        nat = d20(); total = nat + bonus
        if total >= info['dc']:
            del st[name]
            cprint(f"  {d['icon']} {_name(e)} 내성 1d20({nat}){mod_str(bonus)}={total} ≥ DC{info['dc']} "
                   f"→ {name} 저항 성공!", C.GREEN)
        else:
            info['turns'] -= 1
            if info['turns'] <= 0:
                del st[name]
                cprint(f"    ({_name(e)}의 {name} 만료)", C.GRAY)




def status_tags(e):
    st = _statuses(e)
    if not st:
        return ""
    return "  " + " ".join(f"{STATUS_DEFS[n]['color']}{STATUS_DEFS[n]['icon']}{n}{st[n]['turns']}{C.RESET}"
                           for n in st)


def equip_grants(p):
    return [it["grant"] for it in p.equipment.values() if it and it.get("grant")]


def _better_passive(a, b):
    """같은 타입의 두 패시브 이름 중 더 우수한 쪽을 반환(타입이 다르면 None)."""
    pa, pb = PASSIVES[a], PASSIVES[b]
    if pa["type"] != pb["type"]:
        return None
    if pa["type"] == "crit_range":
        return a if pa["value"] <= pb["value"] else b     # 치명범위는 낮을수록 우수
    return a if pa["value"] >= pb["value"] else b          # 그 외는 높을수록 우수


def advancement_passives(p):
    """전직(1차·2차)으로 얻는 패시브 이름.
    같은 계열의 상위호환을 얻으면 하위 패시브는 제거(전직 사슬 한정).
    예) 검성 예리함 → 검신 필멸이면 예리함 제거. 아이템/칭호 패시브는 별도로 중첩."""
    names = []
    a1 = getattr(p, "advanced", None)
    a2 = getattr(p, "advanced2", None)
    for entry in ((ADVANCED.get(a1) if a1 else None), (TIER2.get(a2) if a2 else None)):
        if not entry:
            continue
        for key in ("passive", "passive2"):
            n = entry.get(key)
            if n:
                names.append(n)
    # 같은 계열은 상위(또는 더 큰 값)만 남김 — 전직 사슬 한정(아이템/칭호는 별도 중첩)
    best = {}
    order = []
    for n in names:
        t = PASSIVES[n]["type"]
        if t not in best:
            best[t] = n
            order.append(t)
        else:
            best[t] = _better_passive(best[t], n) or best[t]
    return [best[t] for t in order]


def active_passives(p):
    out = [PASSIVES[n] for n in advancement_passives(p)]
    for n in equip_grants(p):                 # 장비가 부여한 패시브(중첩)
        out.append(PASSIVES[n])
    tn = title_passive_name(p)                # 칭호 보상 패시브(영구)
    if tn:
        out.append(PASSIVES[tn])
    return out


def passive_value(p, ptype, default=0):
    vals = [pv["value"] for pv in active_passives(p) if pv["type"] == ptype]
    if not vals:
        return default
    if ptype == "crit_range":
        # 각 치명범위 패시브가 기준 20에서 깎는 양(20-value)을 합산 → 중첩
        # 예) 직업 예리함(-1) + 처형자의 인장 예리함(-1) = 치명타 18~20
        reduction = sum(20 - v for v in vals)
        return max(15, 20 - reduction)            # 최소 15까지만 내려감
    if ptype == "lifesteal":
        # 흡혈/대흡혈/초월 + 아이템 부여 흡혈을 합산 → 중첩 (최대 100%)
        return min(1.0, sum(vals))
    return max(vals)


def passive_names(p):
    names = list(advancement_passives(p))     # 전직 패시브(상위호환 제거 반영)
    names += equip_grants(p)                  # 장비 부여 패시브도 표시(중첩)
    tn = title_passive_name(p)
    if tn:
        names.append(tn)
    return names


def player_crit_min(p):
    return passive_value(p, "crit_range", 20)


def plog(passive, msg):
    """패시브 발동 로그 ― 또렷하게 표시."""
    cprint(f"   ⟪패시브·{passive}⟫ {msg}", C.CYAN, bold=True)


def passive_name_of(p, ptype):
    if ptype in ("crit_range", "lifesteal"):
        contrib = [n for n in passive_names(p) if PASSIVES[n]["type"] == ptype]
        return "+".join(dict.fromkeys(contrib)) if contrib else None
    target = passive_value(p, ptype, None)
    for n in passive_names(p):
        pv = PASSIVES[n]
        if pv["type"] == ptype and pv["value"] == target:
            return n
    return None


def apply_passive_damage(p, dmg, spell=False, skill=None, skill_name=None):
    """저체력/만체력 피해 증가·주문 강화 패시브와 스킬 자체 증폭을 적용하고 로그 출력."""
    low = p.hp < p.total_max_hp * 0.5
    full = p.hp >= p.total_max_hp
    # 저체력 피해 증가 패시브(분노·광폭 등)
    lh = passive_value(p, "low_hp_dmg", 1.0)
    if lh > 1.0 and low:
        dmg = int(dmg * lh)
        plog(passive_name_of(p, "low_hp_dmg"), f"저체력 → 피해 ×{lh}")
    # 스킬 자체 저체력 증폭(피의 갈증 등) ― 분노 등 패시브와 곱연산으로 중첩
    if skill and low:
        sm = skill.get("low_hp_mult", 1.0)
        if sm > 1.0:
            dmg = int(dmg * sm)
            plog(skill_name or "스킬", f"저체력 추가 증폭 → 피해 ×{sm}")
    # 만체력 피해 증가 패시브(암살 본능 등)
    hh = passive_value(p, "high_hp_dmg", 1.0)
    if hh > 1.0 and full:
        dmg = int(dmg * hh)
        plog(passive_name_of(p, "high_hp_dmg"), f"만체력 → 피해 ×{hh}")
    if spell:
        sd = p.equip_bonus("spell_dmg")        # 보주 등 장비의 주문 피해 보너스
        if sd:
            dmg += sd
            plog("주문 무기", f"주문 피해 +{sd}")
        sp = passive_value(p, "spell_power", 1.0)
        if sp > 1.0:
            dmg = int(dmg * sp)
            plog(passive_name_of(p, "spell_power"), f"주문 피해 ×{sp}")
    return max(1, dmg)


def passive_crit_log(p, r):
    """패시브 치명범위로 치명타가 떴을 때(자연 20이 아닌데 치명타) 로그."""
    if r["crit"] and r["nat"] < 20:
        plog(passive_name_of(p, "crit_range"), f"치명타 발동! (자연 {r['nat']})")


def player_lifesteal(p, dmg):
    ls = passive_value(p, "lifesteal", 0)
    if ls <= 0 or dmg <= 0:
        return 0
    before = p.hp
    p.hp = min(p.total_max_hp, p.hp + max(1, int(dmg * ls)))
    healed = p.hp - before
    if healed:
        plog(passive_name_of(p, "lifesteal"), f"HP +{healed} 회복")
    return healed


def attack_roll(bonus, target_ac, advantage=False, disadvantage=False, crit_min=20):
    nat = d20(advantage, disadvantage)
    crit = nat >= crit_min          # 패시브로 치명타 범위 확장 가능(예: 19~20)
    fumble = nat == 1
    total = nat + bonus
    hit = crit or (not fumble and total >= target_ac)
    return {"hit": hit, "crit": crit, "fumble": fumble, "nat": nat, "total": total}


def roll_damage(die, flat, crit=False, mult=1):
    n, s = die
    count = n * mult * (2 if crit else 1)
    rolls = [random.randint(1, s) for _ in range(count)]
    return max(1, sum(rolls) + flat), rolls


def roll_line(label, r, target_ac):
    tag = ""
    if r["crit"]:
        tag = C.YELLOW + "  ★치명타!" + C.RESET
    elif r["fumble"]:
        tag = C.RED + "  ☠빗나감(자연1)" + C.RESET
    elif not r["hit"]:
        tag = C.GRAY + "  빗나감" + C.RESET
    col = C.YELLOW if r["crit"] else (C.WHITE if r["hit"] else C.GRAY)
    cprint(f"  {label}: 1d20 ({r['nat']}) +보너스 = {r['total']}  vs AC {target_ac}{tag}", col)


def hp_bar(cur, mx, width=18, color=C.GREEN):
    cur = max(0, cur)
    filled = int(width * cur / mx) if mx > 0 else 0
    return f"{color}{'█'*filled}{'░'*(width-filled)}{C.RESET} {cur}/{mx}"


def show_combat(p, m):
    line()
    cprint(f"{m['icon']} {m['name']} Lv.{m['level']}"
           + ("  [BOSS]" if m['boss'] else ("  [특수]" if m.get('special') else ""))
           + f"   AC {m['ac']}", C.RED, bold=m['boss'] or m.get('special', False))
    print("  HP " + hp_bar(m['hp'], m['max_hp'], color=C.RED) + status_tags(m))
    wk, rs = m.get("weak", []), m.get("resist", [])
    im = m.get("immune", [])
    if wk or rs or im:
        seg = []
        if wk: seg.append(f"{C.YELLOW}취약 {'·'.join(wk)}{C.RESET}")
        if rs: seg.append(f"{C.CYAN}저항 {'·'.join(rs)}{C.RESET}")
        if im: seg.append(f"{C.MAGENTA}면역 {'·'.join(im)}{C.RESET}")
        print("  " + "  ".join(seg))
    print()
    cprint(f"{p.name} ({p.disp_title}) Lv.{p.level}   AC {p.ac}", p.disp_color)
    print("  HP " + hp_bar(p.hp, p.total_max_hp, color=C.GREEN) + status_tags(p))
    print("  MP " + hp_bar(p.mp, p.max_mp, color=C.BLUE))
    _bt = buff_tags(p)
    if _bt:
        cprint("  🛡 " + _bt, C.CYAN)
    cprint(f"  명중 {mod_str(p.attack_bonus)}  무기 {fmt_die(p.weapon_die)}{mod_str(p.damage_bonus)}",
           C.GRAY)
    line()


def player_weapon_strike(p, m, mult=1, flat=0, adv=False, skill=None, skill_name=None):
    advantage = adv or is_vulnerable(m)
    disadvantage = is_impaired(p) or not p.armor_proficient
    if advantage and not disadvantage and is_vulnerable(m):
        cprint(f"  ({m['name']} 취약 → 이점)", C.GRAY)
    elif disadvantage and not advantage:
        reason = "갑옷 미숙련" if not p.armor_proficient else "약화"
        cprint(f"  ({p.name} {reason} → 불리점)", C.GRAY)
    r = attack_roll(p.attack_bonus, m['ac'], advantage=advantage, disadvantage=disadvantage,
                    crit_min=player_crit_min(p))
    roll_line(f"{p.name} 공격", r, m['ac'])
    if not r['hit']:
        return r
    passive_crit_log(p, r)
    dmg, rolls = roll_damage(p.weapon_die, p.damage_bonus + flat, crit=r['crit'], mult=mult)
    dmg = apply_passive_damage(p, dmg, spell=False, skill=skill, skill_name=skill_name)
    final, note = deal_damage(m, dmg, "물리")
    cprint(f"    → {fmt_die(p.weapon_die)} 굴림 {rolls} {mod_str(p.damage_bonus + flat)} = {final} 피해{note}",
           C.YELLOW if r['crit'] else C.WHITE)
    player_lifesteal(p, final)
    return r


def roll_initiative(p, m):
    init_b = p.mod("dex") + passive_value(p, "init", 0) + p.equip_bonus("init")
    if passive_value(p, "init", 0) > 0:
        plog(passive_name_of(p, "init"), f"선제 굴림 +{passive_value(p, 'init', 0)}")
    p_nat = d20(); p_total = p_nat + init_b
    m_bonus = m.get("init", 1 + m["level"] // 4)
    m_nat = d20(); m_total = m_nat + m_bonus
    line()
    cprint("  ⚔ 선제 굴림 (이니셔티브)", C.CYAN, bold=True)
    cprint(f"    {p.name}: 1d20({p_nat}) {mod_str(init_b)} = {p_total}", C.WHITE)
    cprint(f"    {m['name']}: 1d20({m_nat}) {mod_str(m_bonus)} = {m_total}", C.WHITE)
    monster_first = m_total > p_total          # 동점은 플레이어 선공
    cprint("    → " + (f"{m['name']}(이)가 먼저 움직인다!" if monster_first
                       else f"{p.name}(이)가 선공!"),
           C.RED if monster_first else C.GREEN)
    return monster_first


def monster_basic_attack(p, m):
    advantage = is_vulnerable(p)
    disadvantage = is_impaired(m) or buff_evade(p)
    if advantage and not disadvantage:
        cprint(f"  ({p.name} 취약 → 적이 이점)", C.GRAY)
    elif disadvantage and not advantage:
        why = "회피" if buff_evade(p) and not is_impaired(m) else "약화"
        cprint(f"  ({m['name']} {why} → 불리점)", C.GRAY)
    r = attack_roll(m['atk_bonus'], p.ac, advantage=advantage, disadvantage=disadvantage)
    roll_line(f"{m['name']} 공격", r, p.ac)
    if r['hit']:
        dmg, rolls = roll_damage(m['dmg_die'], m['dmg_bonus'], crit=r['crit'])
        red = incoming_reduce(p)
        if red:
            dmg = max(1, dmg - red)
        final, note = deal_damage(p, dmg, "물리")
        cprint(f"    → {final} 피해를 입었다!{note}", C.RED)
        if red:
            cprint(f"    (피해 감소 -{red})", C.CYAN)
        reflect_to_monster(p, m)
        if m['boss'] and not m.get("final") and (r['crit'] or random.random() < 0.4):
            apply_status(p, random.choice(["화상", "빙결", "기절"]), 2, dc=10 + m['level'] // 2)


def monster_ability(p, m, name):
    ab = MONSTER_ABILITIES[name]
    t = ab["type"]
    adv = is_vulnerable(p)
    dis = is_impaired(m) or buff_evade(p)
    if t == "heal":
        amt = max(1, int(m["max_hp"] * ab["pct"]))
        m["hp"] = min(m["max_hp"], m["hp"] + amt)
        cprint(f"  {m['name']}의 {name}! HP +{amt} 회복", C.GREEN)
        return
    if t == "guard":
        m["ac"] += ab["ac"]; m["_guard"] = ab["ac"]
        cprint(f"  🛡 {m['name']}(이)가 {name}를 취했다! (AC ↑, 다음 공격을 회피하기 쉬움)", C.CYAN)
        return
    if t == "multi":
        cprint(f"  {m['name']}의 {name}!", C.RED, bold=True)
        for _ in range(ab["hits"]):
            r = attack_roll(m["atk_bonus"], p.ac, advantage=adv, disadvantage=dis)
            if r["hit"]:
                d, _ = roll_damage(m["dmg_die"], m["dmg_bonus"], crit=r["crit"])
                final, note = hit_player(p, d, "물리", m)
                cprint(f"    명중! {final} 피해{note}", C.RED)
            else:
                cprint("    빗나감", C.GRAY)
            if not p.is_alive():
                break
        return
    # heavy / status: 단일 강타
    cprint(f"  {m['name']}의 {name}!", C.RED, bold=True)
    r = attack_roll(m["atk_bonus"], p.ac, advantage=adv, disadvantage=dis)
    roll_line(f"  {name}", r, p.ac)
    if not r["hit"]:
        return
    d, _ = roll_damage(m["dmg_die"], m["dmg_bonus"], crit=r["crit"])
    d = int(d * ab.get("mult", 1.0))
    final, note = hit_player(p, d, ab.get("element", "물리"), m)
    cprint(f"    → {final} 피해!{note}", C.RED)
    if ab.get("status"):
        apply_status(p, ab["status"], ab["turns"], dc=10 + m["level"] // 2)


def is_elite(m):
    """일반 몬스터 제외 ― 특수 몬스터 또는 중간보스(최종/숨겨진 보스는 별도 처리)."""
    return bool(m.get("special")) or (m.get("boss") and not m.get("final"))


ELITE_TELLS = [
    "온몸에 힘을 끌어모으기 시작한다",
    "낮게 자세를 잡으며 노려본다",
    "기운을 응축하며 으르렁거린다",
    "큰 동작을 준비하는 듯 숨을 고른다",
]


def elite_heavy_strike(p, m):
    cprint(f"  💥 {m['name']}(이)가 모은 힘을 폭발시킨다 — 응축된 일격!", C.RED, bold=True)
    adv = is_vulnerable(p)
    r = attack_roll(m["atk_bonus"] + 2, p.ac, advantage=True, disadvantage=buff_evade(p))
    roll_line("강공격", r, p.ac)
    if r["hit"]:
        d, _ = roll_damage(m["dmg_die"], m["dmg_bonus"], crit=r["crit"])
        d = int(d * 1.9)
        final, note = hit_player(p, d, "물리", m)
        cprint(f"    → {final} 피해!{note}", C.RED, bold=True)
        if random.random() < 0.5:
            apply_status(p, random.choice(["화상", "빙결"]), 2, dc=11 + m["level"] // 2)
    else:
        cprint("    회피했다!", C.GREEN)


def monster_attack(p, m):
    time.sleep(0.15)
    if m.get("_guard"):                       # 지난 턴 방어 태세 해제
        m["ac"] -= m.pop("_guard")
    # 엘리트(특수·중간보스) 강공격 텔레그래프: 모은 힘 방출 → 다음 턴 강타
    if m.get("winding"):
        m["winding"] = False
        elite_heavy_strike(p, m)
        return
    if is_elite(m) and not is_impaired(m) and random.random() < 0.33:
        m["winding"] = True
        cprint(f"  ⚠ {m['name']}(이)가 {random.choice(ELITE_TELLS)}... (다음 턴 강공격 주의!)",
               C.YELLOW, bold=True)
        return
    # 저체력 광폭화(1회)
    if "광폭화" in m.get("abilities", []) and not m.get("_enraged") \
            and m["hp"] < m["max_hp"] * 0.5:
        m["_enraged"] = True
        m["atk_bonus"] += 2; m["dmg_bonus"] += 2
        cprint(f"  💢 {m['name']}(이)가 광폭화한다! (공격 강화)", C.RED, bold=True)
    # 특수 행동 사용 결정
    abils = [a for a in m.get("abilities", []) if a != "광폭화"]
    if abils and random.random() < m.get("act_rate", 0):
        monster_ability(p, m, random.choice(abils))
        return
    monster_basic_attack(p, m)


def reflect_to_monster(p, m):
    """가시 계열 패시브: 플레이어가 공격을 적중당할 때마다 적에게 고정 피해 반사."""
    refl = passive_value(p, "reflect", 0)
    if refl > 0 and m and m.get("hp", 0) > 0:
        m["hp"] -= refl
        plog(passive_name_of(p, "reflect"), f"가시 반사 → {m['name']}에게 {refl} 피해")


def hit_player(p, dmg, element="물리", m=None):
    """플레이어 피격 ― 피해감소(패시브+버프) + 속성 반영. m이 주어지면 가시 반사."""
    red = incoming_reduce(p)
    if red:
        dmg = max(1, dmg - red)
    final, note = deal_damage(p, dmg, element)
    if red:
        note += f"  {C.CYAN}(감소 -{red}){C.RESET}"
    if m is not None:
        reflect_to_monster(p, m)
    return final, note


def boss_skill(p, m, name):
    sk = BOSS_SKILLS[name]
    t = sk["type"]
    adv = is_vulnerable(p)
    eva = buff_evade(p)
    if t == "charge":
        m["charging"] = m["ult"]
        cprint(f"  ⚠ {m['name']}(이)가 거대한 힘을 모으고 있다 — [{m['ult']['name']}] 준비!",
               C.YELLOW, bold=True)
        cprint("     (다음 턴 강력한 일격이 날아온다. 방어·회복 준비!)", C.YELLOW)
        return
    # 강공격/특수패턴 사전 자세 힌트
    tell = {"multi": "연격 자세를 잡는다", "status": "위험한 기운을 머금는다",
            "drain": "생기를 빨아들일 듯 다가온다", "attack": "묵직하게 자세를 낮춘다"}.get(t)
    if tell:
        cprint(f"  〔{m['name']}(이)가 {tell}...〕", C.GRAY)
    cprint(f"  {m['name']}의 {name}!", C.RED, bold=True)
    if t == "multi":
        hits = random.randint(*sk["hits"])
        for _ in range(hits):
            r = attack_roll(m["atk_bonus"], p.ac, advantage=adv, disadvantage=eva)
            if r["hit"]:
                d, _ = roll_damage(sk["die"], sk.get("flat", 0) + m["dmg_bonus"], crit=r["crit"])
                final, note = hit_player(p, d, sk.get("element", "물리"), m)
                cprint(f"    명중! {final} 피해{note}", C.RED)
            else:
                cprint("    빗나감", C.GRAY)
            if not p.is_alive():
                break
        return
    r = attack_roll(m["atk_bonus"], p.ac, advantage=adv, disadvantage=eva)
    roll_line(f"  {name}", r, p.ac)
    if not r["hit"]:
        return
    d, _ = roll_damage(sk["die"], sk.get("flat", 0) + m["dmg_bonus"], crit=r["crit"])
    final, note = hit_player(p, d, sk.get("element", "물리"), m)
    cprint(f"    → {final} 피해!{note}", C.RED)
    if sk.get("status"):
        apply_status(p, sk["status"], sk["turns"], dc=12 + m["level"] // 2)
    if t == "drain":
        heal = max(1, final // 2)
        m["hp"] = min(m["max_hp"], m["hp"] + heal)
        cprint(f"    {m['name']}(이)가 {heal} 흡수", C.GREEN)


def boss_release(p, m):
    ult = m.pop("charging")
    cprint(f"  ☄☄ {m['name']}의 {ult['name']}!! ☄☄", C.RED, bold=True)
    r = attack_roll(m["atk_bonus"] + 4, p.ac, disadvantage=buff_evade(p))   # 강력 ― 회피해도 명중 보너스 큼
    d, _ = roll_damage(ult["die"], ult.get("flat", 0) + m["dmg_bonus"], crit=r["crit"])
    if not r["hit"]:
        d = d // 2                                    # 빗나가도 절반은 적중(파괴적)
    final, note = hit_player(p, d, ult.get("element", "물리"), m)
    cprint(f"    → {final} 피해!!{note}", C.RED, bold=True)


def boss_check_phase(p, m):
    pct = m["hp"] / m["max_hp"]
    for ph in m["phases"]:
        if ph["done"] or pct > ph["at"]:
            continue
        ph["done"] = True
        line()
        cprint(f"  ◆ 페이즈 전환! {ph['msg']}", C.MAGENTA, bold=True)
        kind = ph["kind"]
        if kind == "enrage":
            m["atk_bonus"] += ph["value"]; m["dmg_bonus"] += ph["value"]
        elif kind == "heal":
            amt = int(m["max_hp"] * ph["value"])
            m["hp"] = min(m["max_hp"], m["hp"] + amt)
            cprint(f"    {m['name']} HP +{amt} 회복!", C.GREEN)
        elif kind == "resist":
            m["resist"] = list(set(m["resist"]) | set(ph["value"]))
            cprint(f"    저항 강화: {'·'.join(ph['value'])}", C.CYAN)
        elif kind == "add_skill":
            m["skills"] = m["skills"] + ph["value"]
            cprint(f"    새로운 기술 습득!", C.RED)
        elif kind == "immune":
            m["immune"] = list(set(m.get("immune", [])) | set(ph["value"]))
            cprint(f"    {'·'.join(ph['value'])} 면역!", C.CYAN)
        elif kind == "double":
            m["double_act"] = True
            cprint("    ▶▶ 이후 매 턴 연속으로 행동한다!", C.RED, bold=True)
        line()


def boss_act(p, m):
    time.sleep(0.15)
    if m.get("charging"):
        boss_release(p, m)
        return
    acts = 2 if m.get("double_act") else 1
    for i in range(acts):
        if i > 0:
            if not p.is_alive() or m["hp"] <= 0:
                break
            cprint("  ▶ 연속 행동!", C.MAGENTA, bold=True)
        boss_skill(p, m, random.choice(m["skills"]))
        if m.get("charging"):          # 충전을 골랐다면 연속행동 중단(다음 턴 해방)
            break


def combat(p, m):
    if m.get("special"):
        line("═")
        cprint(f"  ✦✦ 희귀 몬스터 출현! {m['icon']} {m['name']} ✦✦", C.YELLOW, bold=True)
        cprint("  처치 시 특수 장신구를 확정 획득한다!", C.YELLOW)
        line("═")
    slow(f"\n{m['icon']} {m['name']}(이)가 나타났다!", C.RED)
    _statuses(p).clear()                       # 새 전투는 상태이상 없이 시작
    _buffs(p).clear()                          # 방어 버프도 전투마다 초기화
    if roll_initiative(p, m):                  # 적이 이니셔티브 승리 → 선공
        monster_attack(p, m)
        if not p.is_alive():
            return "dead"
        # (웹) 전투 로그를 다음 전투 화면에 함께 표시하기 위해 턴 사이 pause 제거
    while p.is_alive() and m['hp'] > 0:
        show_combat(p, m)

        # ── 플레이어 턴 ──
        p_skip = tick_statuses(p)              # DoT + 지속시간 감소 (턴당 1회)
        tick_buffs(p)                          # 방어 버프 지속시간 감소
        if not p.is_alive():
            break
        regen = passive_value(p, "regen", 0)   # 재생 패시브
        if regen and p.hp < p.total_max_hp:
            before = p.hp
            p.hp = min(p.total_max_hp, p.hp + regen)
            plog(passive_name_of(p, "regen"), f"HP +{p.hp - before} 회복")
        mp_regen = p.equip_bonus("mp_regen")   # 장비(책)의 턴당 MP 회복
        if mp_regen and p.mp < p.max_mp:
            before = p.mp
            p.mp = min(p.max_mp, p.mp + mp_regen)
            if p.mp > before:
                cprint(f"  📖 마도서 — MP +{p.mp - before}", C.BLUE)
        if p_skip:
            cprint(f"  {p.name}(은)는 기절해 움직일 수 없다!", C.YELLOW)
        else:
            while True:                        # 유효 행동 전까지 메뉴 반복(턴 유지)
                cprint("1) 공격  2) 스킬  3) 아이템  4) 도망", C.CYAN)
                ch = ask("> ")
                if ch == "1":
                    player_weapon_strike(p, m); break
                elif ch == "2":
                    if use_skill(p, m): break
                elif ch == "3":
                    if use_item_combat(p, m): break
                elif ch == "4":
                    if m['boss']:
                        cprint("  보스에게서는 도망칠 수 없다!", C.RED); break
                    elif random.random() < 0.5:
                        cprint("  무사히 도망쳤다.", C.GRAY)
                        _statuses(p).clear()
                        return "flee"
                    else:
                        cprint("  도망 실패!", C.RED); break
                else:
                    cprint("  잘못된 입력.", C.GRAY)

        saving_throws(p)                       # 플레이어 턴 종료: 상태이상 내성 굴림
        if m['hp'] <= 0:
            break

        # ── 몬스터 턴 ──
        m_skip = tick_statuses(m)
        if m['hp'] <= 0:
            break
        if m.get("final"):
            boss_check_phase(p, m)
        if m_skip:
            cprint(f"  {m['name']}(은)는 기절해 움직이지 못한다!", C.YELLOW)
        elif m.get("final"):
            boss_act(p, m)
        else:
            monster_attack(p, m)
        saving_throws(m)                       # 몬스터 턴 종료: 상태이상 내성 굴림
        if not p.is_alive():
            break
        # (웹) 턴 사이 pause 제거 — 로그가 다음 전투 화면에 함께 보이도록

    _statuses(p).clear()
    if not p.is_alive():
        return "dead"

    line()
    slow(f"  {m['name']}(을)를 쓰러뜨렸다!", C.GREEN)
    p.gold += m['gold']
    cprint(f"  +{m['gold']} 골드", C.YELLOW)
    p.gain_xp(m['xp'])
    drop(p, m)
    pause()
    return "win"


def use_skill(p, m):
    show_combat(p, m)                          # (웹) 선택 화면에도 전투 상태바 유지
    if not p.skills:
        cprint("  배운 스킬이 없다.", C.GRAY)
        return False
    print()
    for i, s in enumerate(p.skills, 1):
        sk = SKILLS[s]
        lack = "" if p.mp >= sk['mp'] else C.RED + " (MP 부족)" + C.RESET
        cprint(f"  {i}) {s}  [MP {sk['mp']}]  {sk['desc']}{lack}", C.MAGENTA)
    cprint("  0) 취소", C.GRAY)
    sel = ask("  스킬 > ")
    if sel == "0" or not sel.isdigit() or not (1 <= int(sel) <= len(p.skills)):
        return False
    name = p.skills[int(sel) - 1]
    sk = SKILLS[name]
    if p.mp < sk['mp']:
        cprint("  MP가 부족하다.", C.RED)
        return False
    p.mp -= sk['mp']
    kind = sk['kind']

    if kind == "weapon":
        r = player_weapon_strike(p, m, mult=sk.get('mult', 1),
                                 flat=sk.get('flat', 0), adv=sk.get('adv', False),
                                 skill=sk, skill_name=name)
        if r['hit'] and 'status' in sk:
            apply_status(m, sk['status'], sk['status_turns'],
                         dc=8 + p.proficiency + p.mod(p.weapon_stat) + passive_value(p, "status_dc", 0))
    elif kind == "spell":
        r = attack_roll(p.spell_attack_bonus, m['ac'], advantage=is_vulnerable(m),
                        disadvantage=not p.armor_proficient, crit_min=player_crit_min(p))
        roll_line(f"{name} 주문", r, m['ac'])
        if r['hit']:
            passive_crit_log(p, r)
            dmg, rolls = roll_damage(sk['die'], p.mod('int'), crit=r['crit'])
            dmg = apply_passive_damage(p, dmg, spell=True, skill=sk, skill_name=name)
            final, note = deal_damage(m, dmg, sk.get('element', '물리'))
            cprint(f"    → {fmt_die(sk['die'])} {rolls} {mod_str(p.mod('int'))} = {final} 피해{note}",
                   C.MAGENTA)
            player_lifesteal(p, final)
            if 'status' in sk:
                apply_status(m, sk['status'], sk['status_turns'],
                             dc=8 + p.proficiency + p.mod('int') + passive_value(p, "status_dc", 0))
    elif kind == "multi":
        extra = passive_value(p, "multi_extra", 0)
        hits = random.randint(*sk['hits']) + extra
        if extra:
            plog(passive_name_of(p, "multi_extra"), f"추가 타격 +{extra}")
        cprint(f"  {name}! {hits}회 연속 공격", C.MAGENTA)
        for _ in range(hits):
            player_weapon_strike(p, m, skill=sk, skill_name=name)  # 각 타격에 패시브·스킬 증폭 적용
            if m['hp'] <= 0:
                break
    elif kind == "heal":
        amt, rolls = roll(sk['die'])
        amt += p.mod('int')
        before = p.hp
        p.hp = min(p.total_max_hp, p.hp + amt)
        cprint(f"  {name}! {fmt_die(sk['die'])} {rolls} {mod_str(p.mod('int'))} → HP {p.hp - before} 회복",
               C.GREEN)
    elif kind == "buff":
        apply_buff(p, name, sk["turns"], ac=sk.get("ac", 0),
                   reduce=sk.get("reduce", 0), evade=sk.get("evade", False))
        eff = []
        if sk.get("ac"): eff.append(f"AC +{sk['ac']}")
        if sk.get("reduce"): eff.append(f"받는 피해 -{sk['reduce']}")
        if sk.get("evade"): eff.append("피격 불리점(회피)")
        cprint(f"  🛡 {name}! {', '.join(eff)} ({sk['turns']}턴)", C.CYAN, bold=True)
        if sk.get("heal"):
            amt, rolls = roll(sk["heal"]); amt += p.mod('int')
            before = p.hp; p.hp = min(p.total_max_hp, p.hp + amt)
            cprint(f"    HP {p.hp - before} 회복", C.GREEN)
        if sk.get("cleanse") and _statuses(p):
            _statuses(p).clear()
            cprint("    모든 상태이상을 해제했다!", C.GREEN)
    elif kind == "drain":
        r = attack_roll(p.spell_attack_bonus, m['ac'], advantage=is_vulnerable(m),
                        disadvantage=not p.armor_proficient, crit_min=player_crit_min(p))
        roll_line(f"{name} 주문", r, m['ac'])
        if r['hit']:
            passive_crit_log(p, r)
            dmg, rolls = roll_damage(sk['die'], p.mod('int'), crit=r['crit'])
            dmg = apply_passive_damage(p, dmg, spell=True, skill=sk, skill_name=name)
            final, note = deal_damage(m, dmg, sk.get('element', '물리'))
            heal = max(1, final // 2)
            before = p.hp
            p.hp = min(p.total_max_hp, p.hp + heal)
            cprint(f"    → {fmt_die(sk['die'])} {rolls} = {final} 피해{note}, HP {p.hp - before} 흡수",
                   C.MAGENTA)
    return True


def use_item_combat(p, m):
    show_combat(p, m)                          # (웹) 선택 화면에도 전투 상태바 유지
    potions = [it for it in p.inventory if it['kind'] == "potion"]
    if not potions:
        cprint("  쓸 물약이 없다.", C.GRAY)
        return False
    print()
    for i, it in enumerate(potions, 1):
        cprint(f"  {i}) {it['name']} ({fmt_die(it['die'])}+{it['flat']} {it['effect'].upper()})", C.GREEN)
    cprint("  0) 취소", C.GRAY)
    sel = ask("  > ")
    if sel == "0" or not sel.isdigit() or not (1 <= int(sel) <= len(potions)):
        return False
    it = potions[int(sel) - 1]
    apply_potion(p, it)
    p.inventory.remove(it)
    return True


def apply_potion(p, it):
    amt, rolls = roll(it['die'])
    amt += it['flat']
    if it['effect'] == "hp":
        before = p.hp
        p.hp = min(p.total_max_hp, p.hp + amt)
        cprint(f"  {it['name']}! {rolls}+{it['flat']} → HP {p.hp - before} 회복", C.GREEN)
    else:
        before = p.mp
        p.mp = min(p.max_mp, p.mp + amt)
        cprint(f"  {it['name']}! {rolls}+{it['flat']} → MP {p.mp - before} 회복", C.BLUE)


def drop(p, m):
    if m.get("final"):                        # 최종 보스: 직업별 전용 장비 확정
        gear = make_boss_loot(m["loot"], p.cls, getattr(p, "ng", 0))
        p.inventory.append(gear)
        cprint(f"  ★★★ 최종 보스 장비 획득! {item_label(gear)}", C.YELLOW, bold=True)
        return
    if m.get("special_drop"):                 # 특수 몬스터: 특수 장신구 확정
        acc = make_special_accessory(m["special_drop"])
        p.inventory.append(acc)
        if not hasattr(p, "codex_specials"):
            p.codex_specials = []
        if m["special_drop"] not in p.codex_specials:
            p.codex_specials.append(m["special_drop"])    # 도감 해금
        cprint(f"  ✦✦ 특수 전리품! {item_label(acc)}", C.YELLOW, bold=True)
    if m['boss'] or random.random() < 0.45:
        rb = m.get("rarity_bonus", 0.0)
        it = make_equipment(m['level'], rarity="희귀" if m['boss'] else None, rarity_bonus=rb)
        p.inventory.append(it)
        cprint(f"  전리품: {item_label(it)}", C.WHITE)
    if random.random() < 0.35:
        pot = make_potion(random.choice(["hp", "mp"]), greater=(m['level'] >= 5))
        p.inventory.append(pot)
        cprint(f"  전리품: {pot['name']}", C.WHITE)


# ─────────────────────────────────────────────────────────────
#  마을 메뉴
# ─────────────────────────────────────────────────────────────
def show_status(p):
    line("═")
    ngtag = f"  ★{p.ng}회차" if getattr(p, "ng", 0) else ""
    tt = title_str(p)
    tt = (tt + " ") if tt else ""
    cprint(f"  {tt}{p.name}  ―  {p.disp_color}{p.disp_title}{C.RESET}  Lv.{p.level}{ngtag}",
           C.WHITE, bold=True)
    line()
    print("  HP " + hp_bar(p.hp, p.total_max_hp, color=C.GREEN))
    print("  MP " + hp_bar(p.mp, p.max_mp, color=C.BLUE))
    nxt = p.xp_to_next()
    print("  XP " + hp_bar(p.xp, nxt, color=C.MAGENTA) + f"  (다음까지 {nxt - p.xp})")
    line()
    cprint("  [능력치]", C.CYAN)
    abil = "  ".join(f"{ABILITY_KR[a]} {p.abilities[a]}({mod_str(p.mod(a))})"
                     for a in ["str", "dex", "con", "int"])
    print("    " + abil)
    cprint("  [전투]", C.CYAN)
    print(f"    AC {p.ac}   명중보너스 {mod_str(p.attack_bonus)}   "
          f"무기피해 {fmt_die(p.weapon_die)}{mod_str(p.damage_bonus)}   숙련 +{p.proficiency}")
    cprint("  [장비]", C.CYAN)
    kr = {"weapon": "무기", "armor": "방어구", "accessory": "장신구1", "accessory2": "장신구2"}
    for slot, it in p.equipment.items():
        if it:
            warn = ""
            if slot == "armor" and not p.armor_proficient:
                warn = C.RED + "  ⚠미숙련(AC절반·불리점)" + C.RESET
            print(f"    {kr.get(slot, slot)}: " + item_label(it) + warn)
        else:
            print(f"    {kr.get(slot, slot)}: " + C.GRAY + "(없음)" + C.RESET)
    prof = "·".join(sorted(armor_profs(p)))
    cprint(f"  갑옷 숙련: {prof}", C.GRAY)
    cprint(f"  배운 스킬: {', '.join(p.skills) if p.skills else '없음'}", C.GRAY)
    pn = passive_names(p)
    if pn:
        cprint("  [패시브]", C.CYAN)
        for n in pn:
            cprint(f"    • {n} — {PASSIVES[n]['desc']}", C.CYAN)
    cm = player_crit_min(p)
    if cm < 20:
        cprint(f"  ⚔ 치명타 범위: {cm}~20", C.YELLOW)
    line("═")
    pause()


PAGE_SIZE = 8

def _page_nav(page, total):
    """페이지 이동 옵션 문자열(앞쪽에 붙임). 단일 글자 키 p)/n) 사용."""
    parts = []
    if page > 0:
        parts.append("p) ◀ 이전")
    if page < total - 1:
        parts.append("n) 다음 ▶")
    return ("   ".join(parts) + "   ") if parts else ""


def inventory_menu(p):
    page = 0
    while True:
        line("═")
        cprint("  [인벤토리]", C.WHITE, bold=True)
        line()
        if not p.inventory:
            cprint("  비어 있음", C.GRAY)
            pause(); return
        total = (len(p.inventory) + PAGE_SIZE - 1) // PAGE_SIZE
        page = max(0, min(page, total - 1))
        start = page * PAGE_SIZE
        endi = min(start + PAGE_SIZE, len(p.inventory))
        for j in range(start, endi):
            it = p.inventory[j]
            tag = C.GRAY + " [장착]" + C.RESET if it['kind'] == "equip" else C.GRAY + " [사용]" + C.RESET
            print(f"  {j - start + 1}) {item_label(it)}{tag}")
        if total > 1:
            cprint(f"  ── 페이지 {page + 1}/{total} ──", C.GRAY)
        nav = _page_nav(page, total)
        cprint(f"  {nav}번호=장착/사용,  0) 나가기", C.CYAN)
        sel = ask("  > ").strip().lower()
        if sel == "0":
            return
        if sel == "n" and page < total - 1:
            page += 1; continue
        if sel == "p" and page > 0:
            page -= 1; continue
        if not sel.isdigit() or not (1 <= int(sel) <= endi - start):
            continue
        it = p.inventory[start + int(sel) - 1]
        if it['kind'] == "equip":
            target = None
            if it["slot"] == "accessory":
                slots = ["accessory", "accessory2"]
                empty = [s for s in slots if not p.equipment.get(s)]
                if empty:
                    target = empty[0]
                else:
                    cprint("  장신구 슬롯이 가득 찼습니다. 교체할 슬롯 선택:", C.CYAN)
                    for i2, s in enumerate(slots, 1):
                        print(f"    {i2}) 장신구{i2}: {item_label(p.equipment[s])}")
                    cprint("    0) 취소", C.GRAY)
                    ssel = ask("  > ")
                    if ssel == "0" or not ssel.isdigit() or not (1 <= int(ssel) <= len(slots)):
                        continue
                    target = slots[int(ssel) - 1]
            old = p.equip(it, target)
            p.inventory.remove(it)
            if old:
                p.inventory.append(old)
            cprint(f"  장착: {it['name']}", C.GREEN)
            if it["slot"] == "armor" and not p.armor_proficient:
                cprint("  ⚠ 직업 미숙련 갑옷: AC 절반·공격 불리점이 적용됩니다.", C.RED)
        else:
            apply_potion(p, it)
            p.inventory.remove(it)
        pause()


def reroll_cost(p):
    # 레벨에 따라 스케일링되는 리롤 비용
    return 20 + p.level * 10


def gen_shop_stock(p):
    greater = p.level >= 5
    cats = armor_profs(p)                     # 상점 갑옷은 숙련 분류만 (전리품은 무작위)
    rb = p.level * SHOP_LEVEL_RARE_STEP       # 레벨이 오를수록 상점 물건의 희귀도↑
    return [make_potion("hp", greater), make_potion("mp", greater),
            make_equipment(p.level, armor_cats=cats, rarity_bonus=rb),
            make_equipment(p.level, armor_cats=cats, rarity_bonus=rb)]


def shop_preview(p, it):
    """상점용: 이 캐릭터가 장착했을 때의 실효 수치(능력 수정치 반영) 미리보기."""
    if it.get("kind") != "equip":
        return ""
    st = it.get("stats", {})
    if it.get("slot") == "weapon":
        stat = WEAPON_STAT.get(weapon_base_name(it), p.attack_stat)
        hit = p.proficiency + p.mod(stat) + st.get("atk_bonus", 0)
        flat = p.mod(stat) + st.get("dmg_bonus", 0)
        die = st.get("die", [1, 2])
        dmin = max(1, die[0] * 1 + flat)         # 주사위 최소 + 수정치
        dmax = max(dmin, die[0] * die[1] + flat)  # 주사위 최대 + 수정치
        extra = []
        if st.get("init"):
            extra.append(f"선제{mod_str(st['init'])}")
        if st.get("ac"):
            extra.append(f"AC{mod_str(st['ac'])}")
        if st.get("mp_regen"):
            extra.append(f"매턴MP+{st['mp_regen']}")
        if st.get("spell_dmg"):
            extra.append(f"주문피해+{st['spell_dmg']}")
        ex = ("  " + " ".join(extra)) if extra else ""
        return (f"{C.GRAY}{ABILITY_KR[stat]} 적용 → 피해 {dmin}~{dmax}  명중 {mod_str(hit)}"
                f"  ({fmt_die(die)}{mod_str(flat)}){ex}{C.RESET}")
    if it.get("slot") == "armor":
        prof = it.get("category") in armor_profs(p)
        bits = [f"AC+{st.get('ac', 0)}"]
        if st.get("max_hp"):
            bits.append(f"최대HP{mod_str(st['max_hp'])}")
        if st.get("init"):
            bits.append(f"선제{mod_str(st['init'])}")
        if st.get("atk_bonus"):
            bits.append(f"명중{mod_str(st['atk_bonus'])}")
        warn = "" if prof else f"  {C.RED}⚠미숙련(AC절반·불리점){C.RESET}"
        return f"{C.GRAY}착용 시 {', '.join(bits)}{C.RESET}{warn}"
    return ""


def shop(p):
    if getattr(p, "shop_stock", None) is None:
        p.shop_stock = gen_shop_stock(p)      # 첫 방문 시 1회 생성, 이후 리롤 전까지 유지
    page = 0
    while True:
        stock = p.shop_stock
        cost = reroll_cost(p)
        line("═")
        cprint("  ⚒  상점", C.YELLOW, bold=True)
        cprint(f"  골드: {p.gold}", C.YELLOW)
        line()
        total = max(1, (len(stock) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(0, min(page, total - 1))
        start = page * PAGE_SIZE
        endi = min(start + PAGE_SIZE, len(stock))
        for j in range(start, endi):
            it = stock[j]
            tag = f" {C.GRAY}(재입고){C.RESET}" if it.get("kind") == "potion" else ""
            print(f"  {j - start + 1}) {item_label(it)}  {C.YELLOW}{it['value']}G{C.RESET}{tag}")
            prev = shop_preview(p, it)
            if prev:
                print(f"        {prev}")
        if total > 1:
            cprint(f"  ── 페이지 {page + 1}/{total} ──", C.GRAY)
        nav = _page_nav(page, total)
        if nav:
            cprint(f"  {nav}", C.CYAN)
        cprint(f"  r) 🎲 리롤 ({cost}G)   s) 판매   0) 나가기", C.CYAN)
        sel = ask("  > ").strip().lower()
        if sel == "0":
            return
        if sel == "n" and page < total - 1:
            page += 1; continue
        if sel == "p" and page > 0:
            page -= 1; continue
        if sel == "r":
            if p.gold < cost:
                cprint("  골드 부족 — 리롤할 수 없다.", C.RED)
            else:
                p.gold -= cost
                p.shop_stock = gen_shop_stock(p)
                page = 0
                cprint(f"  🎲 새 물건을 들여왔다! (-{cost}G)", C.GREEN)
            pause(); continue
        if sel == "s":
            sell(p); continue
        if not sel.isdigit() or not (1 <= int(sel) <= endi - start):
            continue
        it = stock[start + int(sel) - 1]
        if p.gold < it['value']:
            cprint("  골드 부족.", C.RED)
        else:
            p.gold -= it['value']
            if it.get("kind") == "potion":
                p.inventory.append(dict(it))       # 물약은 품절되지 않고 계속 구매 가능
            else:
                p.inventory.append(it)
                stock.remove(it)                   # 장비는 구매 시 품절
            cprint(f"  구매: {it['name']}", C.GREEN)
        pause()


def sell(p):
    first = True
    page = 0
    while True:
        if not p.inventory:
            if first:
                cprint("  팔 물건이 없다.", C.GRAY); pause()
            return                              # 다 팔았으면 조용히 상점으로
        first = False
        line()
        cprint("  [판매]  ", C.WHITE, bold=True)
        total = (len(p.inventory) + PAGE_SIZE - 1) // PAGE_SIZE
        page = max(0, min(page, total - 1))
        start = page * PAGE_SIZE
        endi = min(start + PAGE_SIZE, len(p.inventory))
        for j in range(start, endi):
            it = p.inventory[j]
            price = max(1, it.get('value', 10) // 2)
            print(f"  {j - start + 1}) {item_label(it)}  {C.YELLOW}판매 {price}G{C.RESET}")
        cprint(f"  골드: {p.gold}", C.YELLOW)
        if total > 1:
            cprint(f"  ── 페이지 {page + 1}/{total} ──", C.GRAY)
        nav = _page_nav(page, total)
        cprint(f"  {nav}0) 취소 — 상점으로 돌아가기", C.GRAY)
        sel = ask("  판매 번호 > ").strip().lower()
        if sel == "0":
            return                              # 취소할 때만 상점창으로 복귀
        if sel == "n" and page < total - 1:
            page += 1; continue
        if sel == "p" and page > 0:
            page -= 1; continue
        if not sel.isdigit() or not (1 <= int(sel) <= endi - start):
            continue
        it = p.inventory[start + int(sel) - 1]
        price = max(1, it.get('value', 10) // 2)
        p.gold += price
        p.inventory.remove(it)
        cprint(f"  {it['name']} → {price}G 판매", C.GREEN)


def inn(p):
    cost = p.level * 10
    line("═")
    cprint(f"  🛏  여관 (휴식 {cost}G — HP/MP 완전 회복)", C.CYAN)
    if ask("  쉬시겠습니까? (y/n) > ").lower() == "y":
        if p.gold >= cost:
            p.gold -= cost
            p.heal_full()
            slow("  ...개운하게 회복했다.", C.GREEN)
        else:
            cprint("  골드 부족.", C.RED)
    pause()


def choose_skill(pool):
    line()
    cprint("  습득할 스킬을 선택하세요 (2개 중 1개):", C.CYAN)
    for i, s in enumerate(pool, 1):
        cprint(f"  {i}) {s} — {SKILLS[s]['desc']}", C.MAGENTA)
    while True:
        sel = ask("  스킬 선택 > ")
        if sel.isdigit() and 1 <= int(sel) <= len(pool):
            return pool[int(sel) - 1]


def show_armor_change(p, name):
    """전직 'name'으로 갑옷 숙련이 어떻게 바뀌는지 표시."""
    before = armor_profs(p)
    mod = ADV_ARMOR_MODS.get(name, {})
    after = (before | mod.get("add", set())) - mod.get("remove", set())
    lost, gained = before - after, after - before
    if lost:
        cprint(f"  ⚠ 갑옷 숙련 상실: {'·'.join(sorted(lost))} (미숙련 시 AC 절반·공격 불리점)",
               C.RED, bold=True)
    if gained:
        cprint(f"  ✦ 갑옷 숙련 획득: {'·'.join(sorted(gained))}", C.GREEN, bold=True)
    cprint(f"  전직 후 갑옷 숙련: {'·'.join(sorted(after)) or '없음'}", C.GRAY)


def do_advance(p):
    # 2차 전직 우선: 1차 전직을 했고 레벨 충족 + 아직 2차 미전직
    if getattr(p, "advanced", None) and not getattr(p, "advanced2", None) \
            and p.level >= ADVANCE_LEVEL_2:
        opts = [n for n, info in TIER2.items() if info["base"] == p.advanced]
        while True:                       # 확인에서 취소하면 전직창으로 되돌아옴
            line("═")
            cprint(f"  ✦✦ 2차 전직 — {p.advanced}의 마스터 직업", C.CYAN, bold=True)
            line()
            for i, name in enumerate(opts, 1):
                info = TIER2[name]
                ab = ", ".join(f"{ABILITY_KR[a]}+{v}" for a, v in info["abilities"].items())
                ch = " / ".join(info["choices"])
                pas = info["passive"]
                cprint(f"  {i}) {info['color']}{name}{C.RESET} — {info['desc']}", C.WHITE)
                print(f"       보너스: HP+{info['max_hp']} MP+{info['max_mp']}, {ab}")
                print(f"       궁극기(택1): {ch}   패시브: {pas}({PASSIVES[pas]['desc']})" + (f" + {info['passive2']}({PASSIVES[info['passive2']]['desc']})" if info.get("passive2") else ""))
            cprint("  0) 취소 — 마을로 돌아가기", C.GRAY)
            sel = ask("  2차 전직 선택 > ")
            if sel == "0" or not sel.isdigit() or not (1 <= int(sel) <= len(opts)):
                return
            name = opts[int(sel) - 1]
            npas = TIER2[name].get("passive")
            if npas:
                msg = f"  획득 패시브 ▶ {npas} — {PASSIVES[npas]['desc']}"
                a1 = getattr(p, "advanced", None)
                old = ADVANCED[a1].get("passive") if a1 else None
                if old and old != npas and _better_passive(old, npas) == npas:
                    msg += f"  ({C.GRAY}기존 '{old}' 대체{C.RESET}{C.CYAN})"
                cprint(msg, C.CYAN, bold=True)
            show_armor_change(p, name)
            if ask(f"  {name}(으)로 2차 전직하시겠습니까? (y/n) > ").lower() != "y":
                cprint("  전직을 취소하고 전직창으로 돌아갑니다.", C.GRAY)
                continue                  # 마을이 아니라 전직 선택창으로 복귀
            chosen = choose_skill(TIER2[name]["choices"])
            p.advance2(name, chosen)
            slow(f"  {p.name}(은)는 {name}(으)로 초월했다!", TIER2[name]["color"])
            pause()
            return

    # 1차 전직
    if getattr(p, "advanced", None):
        if p.level < ADVANCE_LEVEL_2:
            cprint(f"  2차 전직은 {ADVANCE_LEVEL_2}레벨부터 가능합니다. (현재 Lv.{p.level})", C.GRAY)
        else:
            cprint("  더 이상 전직할 수 없습니다.", C.GRAY)
        pause(); return
    if p.level < ADVANCE_LEVEL:
        cprint(f"  전직은 {ADVANCE_LEVEL}레벨부터 가능합니다. (현재 Lv.{p.level})", C.GRAY); pause(); return
    opts = [n for n, info in ADVANCED.items() if info["base"] == p.cls]
    while True:                           # 확인에서 취소하면 전직창으로 되돌아옴
        line("═")
        cprint(f"  ✦ 전직 — {p.cls}의 상위 직업", C.CYAN, bold=True)
        line()
        for i, name in enumerate(opts, 1):
            info = ADVANCED[name]
            ab = ", ".join(f"{ABILITY_KR[a]}+{v}" for a, v in info["abilities"].items())
            ch = " / ".join(info["choices"])
            pas = info["passive"]
            cprint(f"  {i}) {info['color']}{name}{C.RESET} — {info['desc']}", C.WHITE)
            print(f"       보너스: HP+{info['max_hp']} MP+{info['max_mp']}, {ab}")
            print(f"       자동: {info['auto']}   선택(택1): {ch}   패시브: {pas}" + (f" + {info['passive2']}" if info.get("passive2") else ""))
        cprint("  0) 취소 — 마을로 돌아가기", C.GRAY)
        sel = ask("  전직 선택 > ")
        if sel == "0" or not sel.isdigit() or not (1 <= int(sel) <= len(opts)):
            return
        name = opts[int(sel) - 1]
        npas = ADVANCED[name].get("passive")
        if npas:
            cprint(f"  획득 패시브 ▶ {npas} — {PASSIVES[npas]['desc']}", C.CYAN, bold=True)
        show_armor_change(p, name)
        if ask(f"  {name}(으)로 전직하시겠습니까? 되돌릴 수 없습니다. (y/n) > ").lower() != "y":
            cprint("  전직을 취소하고 전직창으로 돌아갑니다.", C.GRAY)
            continue                      # 마을이 아니라 전직 선택창으로 복귀
        chosen = choose_skill(ADVANCED[name]["choices"])
        p.advance(name, chosen)
        slow(f"  {p.name}(은)는 {name}(으)로 각성했다!", ADVANCED[name]["color"])
        pause()
        return


ENDINGS = {
    # id: 칭호, 색, 보상 패시브(영구), 최초 달성 골드, 조건설명, 서사({n}=이름,{ng}=회차)
    "mythic":    {"title": "신화의 종결자", "color": C.YELLOW, "passive": "필멸", "gold": 5000,
                  "cond": "보스 3종 전부 처치 + 2회차 이상",
                  "lines": ["세 군주를 모두 쓰러뜨리고 무수한 차원을 정복한 끝에,",
                            "{n}의 이름은 신화가 되어 영원히 전해진다."]},
    "conqueror": {"title": "차원의 정복자", "color": C.MAGENTA, "passive": "광폭", "gold": 3000,
                  "cond": "2회차 이상 도달",
                  "lines": ["{ng}번의 회귀 속에서도 굴하지 않은 정복자.",
                            "더 강한 적을 찾아 끝없는 전장으로 사라졌다."]},
    "guardian":  {"title": "세계의 수호자", "color": C.CYAN, "passive": "철벽", "gold": 2000,
                  "cond": "최종 보스 3종 전부 처치",
                  "lines": ["세 위협을 모두 물리치자 세계에 평화가 찾아왔다.",
                            "{n}은(는) 모두의 영웅으로 기억된다."]},
    # 직업(2차 전직) 엔딩
    "파괴왕":   {"title": "파괴의 화신", "color": C.RED, "passive": "분노", "gold": 1000,
              "cond": "파괴왕으로 최종 보스 처치", "lines": ["분노로 모든 것을 부순 {n}.", "맞설 자는 없었다."]},
    "성전사":   {"title": "불멸의 수호기사", "color": C.YELLOW, "passive": "재생", "gold": 1000,
              "cond": "성전사로 최종 보스 처치", "lines": ["{n}의 방패는 끝내 부서지지 않았다.", "세계는 그 그늘에서 평화를 얻었다."]},
    "검신":     {"title": "검의 신", "color": C.WHITE, "passive": "예리함", "gold": 1000,
              "cond": "검신으로 최종 보스 처치", "lines": ["검의 극의에 다다른 {n}.", "그 일섬은 전설이 되었다."]},
    "마도왕":   {"title": "만마의 지배자", "color": C.MAGENTA, "passive": "주문 강화", "gold": 1000,
              "cond": "마도왕으로 최종 보스 처치", "lines": ["모든 마법을 손에 넣은 {n}.", "세계의 법칙마저 무릎 꿇었다."]},
    "정령왕":   {"title": "원소의 군주", "color": C.CYAN, "passive": "원소 지배", "gold": 1000,
              "cond": "정령왕으로 최종 보스 처치", "lines": ["불과 얼음을 다스린 {n}.", "자연이 새 군주를 받아들였다."]},
    "사령왕":   {"title": "죽음의 왕", "color": C.GREEN, "passive": "흡혈", "gold": 1000,
              "cond": "사령왕으로 최종 보스 처치", "lines": ["삶과 죽음의 경계를 넘은 {n}.", "그 군세는 스러지지 않는다."]},
    "그림자 군주": {"title": "어둠의 제왕", "color": C.RED, "passive": "신속", "gold": 1000,
              "cond": "그림자 군주로 최종 보스 처치", "lines": ["빛이 닿지 않는 곳을 지배한 {n}.", "그림자 속에서 세계를 굽어본다."]},
    "신궁":     {"title": "백발백중의 신궁", "color": C.GREEN, "passive": "예리함", "gold": 1000,
              "cond": "신궁으로 최종 보스 처치", "lines": ["빗나감을 모르는 {n}.", "그 화살은 운명마저 꿰뚫었다."]},
    "환영무희": {"title": "천 그림자의 무희", "color": C.BLUE, "passive": "분신술", "gold": 1000,
              "cond": "환영무희로 최종 보스 처치", "lines": ["무수한 분신으로 적을 압도한 {n}.", "그 춤은 누구도 따라잡지 못했다."]},
    "철벽군주": {"title": "강철의 군주", "color": C.YELLOW, "passive": "불굴", "gold": 1000,
              "cond": "철벽군주로 최종 보스 처치", "lines": ["그 어떤 공격도 {n}의 벽을 넘지 못했다.", "강철의 군주 앞에 세계는 안식을 얻었다."]},
    "워든":     {"title": "만능의 기사", "color": C.CYAN, "passive": "균형", "gold": 1000,
              "cond": "워든으로 최종 보스 처치", "lines": ["모든 무예에 통달한 {n}.", "어떤 전장도 그를 당해내지 못했다."]},
    "마검군주": {"title": "마검의 군주", "color": C.MAGENTA, "passive": "수호", "gold": 1000,
              "cond": "마검군주로 최종 보스 처치", "lines": ["마법과 강철을 하나로 벼린 {n}.", "쓰러지지 않는 마검 앞에 모두가 무릎 꿇었다."]},
    "transcend": {"title": "윤회의 초월자", "color": C.YELLOW, "passive": "초월", "gold": 10000, "secret": True,
              "cond": "달성 가능한 모든 칭호 수집 후, 숨겨진 보스 [종언의 군주] 처치",
              "lines": ["종언의 근원을 베어내자, 시들어가던 세계수가 다시 숨을 쉰다.",
                        "{n}은(는) 침식의 순환을 끊고, 세계를 넘어선 존재가 되었다."]},
}
CLASS_ENDING_IDS = set(TIER2.keys())          # 직업(2차전직) 엔딩 — 캐릭터마다 1개만 달성 가능
MILESTONE_IDS = ["mythic", "conqueror", "guardian"]
SECRET_ID = "transcend"


def earnable_endings(p):
    """이 캐릭터가 달성 가능한 칭호(밀스톤 3 + 본인 직업 엔딩)."""
    s = set(MILESTONE_IDS)
    a2 = getattr(p, "advanced2", None)
    if a2 in ENDINGS:
        s.add(a2)
    return s


def secret_unlocked(p):
    return earnable_endings(p) <= set(getattr(p, "titles", []))


def determine_ending(p):
    defeated = set(getattr(p, "bosses_defeated", []))
    ng = getattr(p, "ng", 0)
    allb = {t["name"] for t in FINAL_BOSS_TEMPLATES}
    if defeated >= allb and ng >= 2:
        return "mythic"
    if ng >= 2:
        return "conqueror"
    if defeated >= allb:
        return "guardian"
    a2 = getattr(p, "advanced2", None)
    return a2 if a2 in ENDINGS else "guardian"


def title_passive_name(p):
    tid = getattr(p, "title", None)
    if tid and tid in ENDINGS and ENDINGS[tid].get("passive"):
        return ENDINGS[tid]["passive"]
    return None


def title_str(p):
    tid = getattr(p, "title", None)
    if tid and tid in ENDINGS:
        e = ENDINGS[tid]
        return f"{e['color']}〈{e['title']}〉{C.RESET}"
    return ""


def show_ending(p, eid=None):
    eid = eid or determine_ending(p)
    e = ENDINGS[eid]
    col = e["color"]
    ng = getattr(p, "ng", 0)
    defeated = set(getattr(p, "bosses_defeated", []))
    allb = {t["name"] for t in FINAL_BOSS_TEMPLATES}
    print()
    cprint("  ╔══════════════════════════════════════╗", col)
    cprint("  ║              E N D I N G             ║", col, bold=True)
    cprint("  ╚══════════════════════════════════════╝", col)
    print()
    cprint(f"        〈 {e['title']} 〉", col, bold=True)
    print()
    for s in e["lines"]:
        if s:
            slow("    " + s.replace("{n}", p.name).replace("{ng}", str(ng)), C.WHITE)
    print()
    # 칭호/보상 지급
    newly = eid not in getattr(p, "titles", [])
    if newly:
        p.titles = list(getattr(p, "titles", [])) + [eid]
        p.gold += e["gold"]
        line()
        cprint(f"  🏅 새 칭호 획득: 〈{e['title']}〉", col, bold=True)
        cprint(f"     보상: +{e['gold']} 골드"
               + (f", 영구 패시브 [{e['passive']}] ({PASSIVES[e['passive']]['desc']})"
                  if e["passive"] else ""), col)
        if not getattr(p, "title", None):
            p.title = eid
            cprint(f"     칭호 〈{e['title']}〉(을)를 장착했다. (도감에서 변경 가능)", C.GRAY)
    else:
        cprint(f"  (이미 획득한 칭호: 〈{e['title']}〉)", col)
    line()
    cprint(f"  {p.name} · {p.disp_title} · Lv.{p.level}" + (f" · ★{ng}회차" if ng else ""), C.GRAY)
    cprint(f"  처치한 최종 보스: {len(defeated)}/{len(allb)}"
           + (f" ({'·'.join(sorted(defeated))})" if defeated else ""), C.GRAY)
    cprint(f"  획득 칭호: {len(getattr(p,'titles',[]))}/{len(ENDINGS)}", C.GRAY)
    line()
    if newly and eid != SECRET_ID and secret_unlocked(p) and SECRET_ID not in p.titles:
        print()
        cprint("  ✦✦✦  달성 가능한 모든 칭호를 모았다!  ✦✦✦", C.YELLOW, bold=True)
        cprint("  마을에 [🌑 숨겨진 결전 — 종언의 군주]이(가) 해금되었다.", C.MAGENTA, bold=True)
        line()
    print()
    cprint("              T H E   E N D" if eid != SECRET_ID else "          ★ T R U E   E N D ★",
           col, bold=True)
    print()
    pause()


def secret_boss_fight(p):
    global NG_MULT
    NG_MULT = 1 + NG_STEP * getattr(p, "ng", 0)
    m = make_secret_boss(p.level, getattr(p, "ng", 0))
    line("═")
    cprint("  🌑🌑🌑  숨 겨 진  결 전  🌑🌑🌑", C.MAGENTA, bold=True)
    cprint(f"  {m['icon']} {m['name']}  (Lv.{m['level']}, HP {m['max_hp']})", C.RED, bold=True)
    cprint(f"  {m['intro']}", C.GRAY)
    cprint("  ※ 하드 CC(기절·빙결) 면역 · 다단 페이즈 · 종언 궁극기에 주의하라!", C.YELLOW)
    if getattr(p, "ng", 0) >= 1:
        cprint(f"  ⚠ {p.ng}회차 강화 페이즈 활성 — 추가 페이즈와 폭주(연속 행동)에 대비하라!", C.MAGENTA, bold=True)
    line("═")
    pause()
    res = combat(p, m)
    if res == "dead":
        return "dead"
    line("═")
    slow("  종언의 군주마저 쓰러뜨렸다... 모든 것의 끝에서, 당신은 초월했다.", C.YELLOW)
    p.gold += 3000
    p.bosses_defeated = sorted(set(getattr(p, "bosses_defeated", [])) | {m["name"]})
    p.heal_full()
    line("═")
    pause()
    show_ending(p, eid=SECRET_ID)
    return "ending"


def _trait_text(d):
    return stats_str(d) if d else "특이 효과 없음"


def weapons_codex(p):
    note = {
        "단검": "최고의 명중·선제. 정밀과 속공(상태이상 적중에 유리).",
        "레이피어": "민첩형 검. 선제와 약간의 방어.",
        "장검": "안정적인 표준 검. 명중이 높음.",
        "전투 도끼": "주사위와 별개의 고정 추가피해.",
        "대검": "2d6의 큰 주사위 + 고정피해. 안정적이고 치명타에 강함.",
        "워해머": "최대 피해. 대신 맞히기 어려움.",
        "활": "1d12 저격 무기. 큰 주사위·치명타에 강하나 명중이 낮음.",
        "방패": "공격을 포기하고 AC를 챙기는 방어 무기(탱커용).",
        "마법 지팡이": "주문 특화. 근접 무기 피해는 약함.",
        "책": "턴당 MP +2 회복(지속 시전형). 근접은 약하지만 마나가 마르지 않음.",
        "보주": "주문 피해 +3, 대신 주문 명중 -2. 세게 터지지만 자주 빗나가는 유리대포.",
    }
    line("═"); cprint("  🗡 무기 도감", C.WHITE, bold=True)
    cprint("  명중·피해 수정치는 '무기 종류가 정한 능력치'에서 나옵니다.", C.GRAY)
    line()
    for nm, die in WEAPONS:
        stat = ABILITY_KR[WEAPON_STAT.get(nm, "str")]
        cprint(f"  ● {nm}  ({fmt_die(die)})", C.YELLOW, bold=True)
        print(f"      사용 능력치: {stat} 수정치 → 명중·피해에 적용")
        print(f"      고유 특성: {_trait_text(WEAPON_TRAITS.get(nm, {}))}")
        print(f"      {C.GRAY}{note.get(nm, '')}{C.RESET}")
    line("═"); pause()


def armors_codex(p):
    note = {
        "로브": "가장 가벼움. 명중을 보강(주문가 친화).",
        "경갑": "기동형. 선제권 보너스.",
        "중갑": "튼튼함. 체력 보너스(평갑).",
        "중장갑": "최고 방어·체력. 대신 선제권 패널티(중갑).",
    }
    line("═"); cprint("  🛡 방어구 도감", C.WHITE, bold=True)
    cprint("  미숙련 분류를 착용하면 AC 절반·공격 불리점.", C.GRAY)
    line()
    for nm, ac, cat in ARMORS:
        prof = [c for c, s in ARMOR_PROF.items() if cat in s]
        cprint(f"  ● {nm}  [{cat}]  기본 AC+{ac}", C.YELLOW, bold=True)
        print(f"      고유 특성: {_trait_text(ARMOR_TRAITS.get(cat, {}))}")
        print(f"      기본 숙련 직업: {'·'.join(prof) if prof else '없음'} {C.GRAY}(전직으로 변동){C.RESET}")
        print(f"      {C.GRAY}{note.get(cat, '')}{C.RESET}")
    line("═"); pause()


def accessories_codex(p):
    seen = getattr(p, "codex_specials", [])
    line("═"); cprint("  💍 장신구 도감", C.WHITE, bold=True); line()
    cprint("  [일반 장신구]", C.CYAN)
    for nm, key in ACCESSORIES:
        print(f"  ● {nm} — {STAT_KR.get(key, key)} 보너스")
    line()
    cprint("  [특수 장신구]  (특수 몬스터 처치로 획득 — 패시브 부여)", C.CYAN)
    for nm, info in SPECIAL_ACCESSORIES.items():
        if nm in seen:
            cprint(f"  ★ {nm} — 패시브[{info['grant']}], {stats_str(info['stats'])}", C.YELLOW)
            print(f"      {C.GRAY}{info['desc']}{C.RESET}")
        else:
            print(f"  {C.GRAY}🔒 ??? (미해금 — 특수 몬스터를 처치해 획득){C.RESET}")
    line("═"); pause()


def passives_codex(p):
    # 직업(전직) 패시브만 — 보유 직업 + 다른 출처/중첩 여부 서술
    order = []
    for src in (ADVANCED, TIER2):
        for info in src.values():
            pn = info.get("passive")
            if pn and pn not in order:
                order.append(pn)
    line("═"); cprint("  ✦ 패시브 도감 (직업 패시브)", C.WHITE, bold=True); line()
    for pn in order:
        pv = PASSIVES[pn]
        cls_src = [c for c, i in ADVANCED.items() if i.get("passive") == pn] \
            + [c for c, i in TIER2.items() if i.get("passive") == pn]
        item_src = [a for a, i in SPECIAL_ACCESSORIES.items() if i.get("grant") == pn]
        title_src = [e for e in ENDINGS if ENDINGS[e].get("passive") == pn]
        stackable = pv["type"] in ("crit_range", "lifesteal")
        multi = (len(cls_src) > 1) or item_src or title_src
        cprint(f"  ● {pn} — {pv['desc']}", C.CYAN, bold=True)
        print(f"      보유 직업: {'·'.join(cls_src) if cls_src else '없음'}")
        others = []
        if item_src: others.append("특수 장신구")
        if title_src: others.append("칭호")
        if others:
            print(f"      그 외 출처: {', '.join(others)}")
        if stackable:
            print(f"      중첩: {C.GREEN}가능{C.RESET} — 서로 다른 출처(직업+아이템/칭호)의 효과가 합산")
        elif multi:
            print(f"      중첩: {C.GRAY}불가{C.RESET} — 여러 출처가 있어도 가장 높은 효과만 적용")
        else:
            print(f"      중첩: {C.GRAY}해당 없음{C.RESET}")
    cprint("  ※ 같은 직업 라인의 상위호환(예: 예리함→필멸)은 하위 패시브를 대체합니다.", C.GRAY)
    line("═"); pause()


def items_codex(p):
    while True:
        line("═")
        cprint("  📚 아이템 · 패시브 도감", C.WHITE, bold=True)
        line()
        cprint("  1) 🗡 무기   2) 🛡 방어구   3) 💍 장신구   4) ✦ 패시브", C.CYAN)
        cprint("  0) 뒤로", C.GRAY)
        sel = ask("  > ")
        if sel == "0":
            return
        elif sel == "1":
            weapons_codex(p)
        elif sel == "2":
            armors_codex(p)
        elif sel == "3":
            accessories_codex(p)
        elif sel == "4":
            passives_codex(p)


def codex_menu(p):
    while True:
        line("═")
        cprint("  📖 도감", C.WHITE, bold=True)
        line()
        cprint("  1) 🏅 칭호 · 업적", C.CYAN)
        cprint("  2) 📚 아이템 · 패시브", C.CYAN)
        cprint("  0) 나가기", C.GRAY)
        sel = ask("  > ")
        if sel == "0":
            return
        elif sel == "1":
            titles_codex(p)
        elif sel == "2":
            items_codex(p)


def titles_codex(p):
    ids = list(ENDINGS)
    while True:
        owned = getattr(p, "titles", [])
        cur = getattr(p, "title", None)
        a2 = getattr(p, "advanced2", None)
        earn = earnable_endings(p)
        line("═")
        cprint("  🏅 칭호 · 엔딩 도감", C.WHITE, bold=True)
        got_earn = len(earn & set(owned))
        cprint(f"  달성 {len(owned)}/{len(ENDINGS)}   "
               f"이 직업 달성 가능분 {got_earn}/{len(earn)}   "
               + ("🌑 숨겨진 결전 해금!" if secret_unlocked(p) and SECRET_ID not in owned else ""), C.GRAY)
        cprint(f"  현재 칭호: " + (ENDINGS[cur]["title"] if cur else "없음"), C.GRAY)
        line()
        for i, eid in enumerate(ids, 1):
            e = ENDINGS[eid]
            got = eid in owned
            secret = e.get("secret")
            other_cls = eid in CLASS_ENDING_IDS and eid != a2     # 타 직업 전용
            if secret and not got and not secret_unlocked(p):
                # 숨겨진 엔딩: 해금 전 완전 비공개
                print(f"  {i:2}) {C.GRAY}🔒 ??? (숨겨진 엔딩){C.RESET}")
                print(f"        {C.GRAY}조건: 달성 가능한 모든 칭호를 수집하라{C.RESET}")
                continue
            mark = f"{e['color']}✔{C.RESET}" if got else (f"{C.GRAY}🔒{C.RESET}" if other_cls
                                                          else f"{C.GRAY}✘{C.RESET}")
            if got:
                name = f"{e['color']}{e['title']}{C.RESET}"
            elif other_cls:
                name = f"{C.GRAY}{e['title']} (타 직업){C.RESET}"
            else:
                name = f"{C.GRAY}??? (미달성){C.RESET}"
            star = f"{C.YELLOW} ◀ 장착중{C.RESET}" if eid == cur else ""
            pas = f"  보상:[{e['passive']}]" if e["passive"] else "  보상:[-]"
            print(f"  {i:2}) {mark} {name}{star}")
            print(f"        {C.GRAY}조건: {e['cond']}{pas}{C.RESET}")
        cprint("  번호=칭호 장착(달성한 것만)   u=해제   0=나가기", C.CYAN)
        sel = ask("  > ")
        if sel == "0":
            return
        if sel.lower() == "u":
            p.title = None
            cprint("  칭호를 해제했다.", C.GRAY); pause(); continue
        if sel.isdigit() and 1 <= int(sel) <= len(ids):
            eid = ids[int(sel) - 1]
            if eid in owned:
                p.title = eid
                e = ENDINGS[eid]
                cprint(f"  칭호 〈{e['title']}〉 장착!"
                       + (f"  (영구 패시브 {e['passive']} 적용)" if e["passive"] else ""), e["color"])
            else:
                cprint("  아직 달성하지 못한 엔딩입니다.", C.GRAY)
            pause()


def final_boss_fight(p):
    global NG_MULT
    NG_MULT = 1 + NG_STEP * getattr(p, "ng", 0)
    m = make_final_boss(p.level)
    line("═")
    cprint("  ⚔⚔⚔  최 종 결 전  ⚔⚔⚔", C.RED, bold=True)
    ngtag = f"  [{p.ng + 1}회차]" if getattr(p, "ng", 0) else ""
    cprint(f"  {m['icon']} {m['name']}  (Lv.{m['level']}, HP {m['max_hp']}){ngtag}", C.RED, bold=True)
    cprint(f"  {m['intro']}", C.GRAY)
    cprint("  ※ 페이즈 전환·궁극기 충전·상태이상 면역을 주의하라!", C.YELLOW)
    line("═")
    pause()
    res = combat(p, m)
    if res == "dead":
        return "dead"
    line("═")
    slow(f"  {m['name']}(을)를 토벌했다! 세계를 구원했다!", C.YELLOW)
    cprint("  ★★★  당신은 전설이 되었다  ★★★", C.YELLOW, bold=True)
    p.gold += 1000
    p.bosses_defeated = sorted(set(getattr(p, "bosses_defeated", [])) | {m["name"]})
    p.heal_full()
    line("═")
    cprint("  여정의 갈림길에 섰다.", C.CYAN)
    cprint("  1) 더 강한 적에 도전 (다음 회차)", C.CYAN)
    cprint("  2) 여정을 마무리한다 (엔딩)", C.CYAN)
    ch = ask("  > ")
    if ch == "2":
        show_ending(p)
        return "ending"
    p.ng = getattr(p, "ng", 0) + 1
    cprint(f"  ◆ {p.ng}회차 돌입! 모든 적이 강해지지만 더 강력한 보스 장비가 기다린다.", C.MAGENTA, bold=True)
    pause()
    return "ng"


# ─────────────────────────────────────────────────────────────
#  층 도입부 · 랜덤 이벤트 (얕은 서사)
# ─────────────────────────────────────────────────────────────
FLOOR_INTROS = [
    "축축한 돌벽을 따라 횃불이 일렁인다.",
    "어디선가 낮은 울음소리가 메아리쳐 온다.",
    "공기에서 쇠와 피 냄새가 옅게 풍긴다.",
    "발밑에서 오래된 뼛조각이 바스러진다.",
    "차가운 바람이 통로 깊은 곳에서 불어온다.",
    "벽에 새겨진 경고문이 희미하게 빛난다.",
    "멀리서 무언가가 끌리는 소리가 들린다.",
    "천장에서 정체 모를 물방울이 떨어진다.",
]

# 이벤트: 각 선택지는 (라벨, 효과). 효과 형식:
#   ("none",) / ("heal",pct) / ("mp",pct) / ("healmp",pct) / ("gold",mult) /
#   ("hurt",pct) / ("potion",greater) / ("weapon",) / ("armor",) / ("xp",mult) /
#   ("gamble",mult) / ("buy_potion",kind,cost) / ("random",[(w,eff)..]) / [eff, eff..]
DUNGEON_EVENTS = [
    {"intro": "이끼 낀 낡은 제단이 희미하게 빛난다.",
     "choices": [("기도를 올린다", ("heal", 0.4)),
                 ("마나를 명상한다", ("mp", 0.5)),
                 ("그냥 지나간다", ("none",))]},
    {"intro": "먼지 쌓인 보물상자가 길을 막고 있다.",
     "choices": [("힘껏 연다", ("random", [(0.55, ("gold", 20)), (0.45, ("hurt", 0.18))])),
                 ("조심스레 연다", ("gold", 9)),
                 ("건드리지 않는다", ("none",))]},
    {"intro": "후드를 쓴 떠돌이 상인이 좌판을 펼친다.",
     "choices": [("치유 물약을 산다", ("buy_potion", "hp", 20)),
                 ("마나 물약을 산다", ("buy_potion", "mp", 20)),
                 ("거절한다", ("none",))]},
    {"intro": "바위틈에서 맑은 샘물이 솟아난다.",
     "choices": [("벌컥 들이켠다", ("random", [(0.78, ("healmp", 0.3)), (0.22, ("hurt", 0.12))])),
                 ("물병에 담아둔다", ("potion", False)),
                 ("지나간다", ("none",))]},
    {"intro": "부서진 무기고에 낡은 장비가 나뒹군다.",
     "choices": [("무기를 뒤진다", ("random", [(0.6, ("weapon",)), (0.4, ("none",))])),
                 ("방어구를 뒤진다", ("random", [(0.6, ("armor",)), (0.4, ("none",))])),
                 ("지나간다", ("none",))]},
    {"intro": "고대 문자가 새겨진 비석이 우뚝 서 있다.",
     "choices": [("문자를 해독한다", ("xp", 14)),
                 ("제물을 바친다", [("gold", -8), ("xp", 26)]),
                 ("지나간다", ("none",))]},
    {"intro": "수상한 도박꾼이 동전을 튕기며 내기를 건다.",
     "choices": [("판돈을 건다", ("gamble", 16)),
                 ("거절한다", ("none",))]},
    {"intro": "지친 모험가가 길가에 주저앉아 도움을 청한다.",
     "choices": [("도와준다", [("xp", 10), ("gold", 12)]),
                 ("길을 묻는다", ("none",)),
                 ("무시한다", ("none",))]},
    {"intro": "음산한 우상이 당신을 가만히 노려본다.",
     "choices": [("부숴버린다", ("random", [(0.6, ("gold", 22)), (0.4, ("ambush",))])),
                 ("기도를 올린다", ("random", [(0.6, ("heal", 0.3)), (0.4, ("hurt", 0.12))])),
                 ("외면한다", ("none",))]},
    {"intro": "동전이 가득 잠긴 행운의 분수가 반짝인다.",
     "choices": [("동전을 던진다", ("random", [(0.5, ("gold", 14)), (0.3, ("potion", False)), (0.2, ("none",))])),
                 ("한 움큼 줍는다", ("random", [(0.5, ("gold", 22)), (0.5, ("ambush",))])),
                 ("지나간다", ("none",))]},
    {"intro": "좁은 길목에서 서늘한 살기가 느껴진다...",
     "choices": [("정면 돌파한다", ("ambush",)),
                 ("조심히 우회한다", ("random", [(0.75, ("none",)), (0.25, ("hurt", 0.1))]))]},
    {"intro": "무너진 서가에 마법서가 흩어져 있다.",
     "choices": [("책을 읽는다", ("xp", 14)),
                 ("마법서를 챙긴다", ("random", [(0.6, ("mp", 0.6)), (0.4, ("none",))])),
                 ("지나간다", ("none",))]},
    {"intro": "녹슨 갑주를 입은 기사의 유해가 벽에 기대 있다.",
     "choices": [("갑옷을 챙긴다", ("armor",)),
                 ("검을 뽑는다", ("weapon",)),
                 ("명복을 빈다", ("heal", 0.25))]},
    {"intro": "두 갈래 길이 어둠 속으로 갈라진다.",
     "choices": [("어두운 왼쪽 길", ("random", [(0.5, ("gold", 24)), (0.5, ("ambush",))])),
                 ("희미한 오른쪽 길", ("random", [(0.6, ("potion", False)), (0.4, ("none",))]))]},
    {"intro": "굳게 봉인된 보물고가 길을 막고 있다.",
     "choices": [("자물쇠를 딴다", ("random", [(0.55, ("gold", 28)), (0.45, ("hurt", 0.15))])),
                 ("부숴서 연다", [("hurt", 0.1), ("gold", 18)]),
                 ("포기한다", ("none",))]},
    {"intro": "맑고 따뜻한 기운이 감도는 치유의 성소다.",
     "choices": [("몸을 맡긴다", ("random", [(0.35, ("full_heal",)), (0.65, ("healmp", 0.4))])),
                 ("기도만 올린다", ("heal", 0.3)),
                 ("지나간다", ("none",))]},
]


def _resolve_effect(p, eff, floor, region=None):
    """이벤트 효과를 적용하고 (메시지, 색) 리스트를 반환."""
    if isinstance(eff, list):
        out = []
        for e in eff:
            out += _resolve_effect(p, e, floor, region)
        return out
    kind = eff[0]
    if kind == "none":
        return [("...아무 일도 일어나지 않았다.", C.GRAY)]
    if kind == "random":
        r = random.random(); acc = 0
        for w, sub in eff[1]:
            acc += w
            if r <= acc:
                return _resolve_effect(p, sub, floor, region)
        return _resolve_effect(p, eff[1][-1][1], floor, region)
    if kind == "heal":
        amt = max(1, int(p.total_max_hp * eff[1])); before = p.hp
        p.hp = min(p.total_max_hp, p.hp + amt)
        return [(f"💚 상처가 아문다. HP +{p.hp - before}", C.GREEN)]
    if kind == "mp":
        amt = max(1, int(p.max_mp * eff[1])); before = p.mp
        p.mp = min(p.max_mp, p.mp + amt)
        return [(f"💙 정신이 맑아진다. MP +{p.mp - before}", C.BLUE)]
    if kind == "healmp":
        h = max(1, int(p.total_max_hp * eff[1])); mm = max(1, int(p.max_mp * eff[1]))
        p.hp = min(p.total_max_hp, p.hp + h); p.mp = min(p.max_mp, p.mp + mm)
        return [(f"✨ 활력이 차오른다. HP +{h}, MP +{mm}", C.GREEN)]
    if kind == "gold":
        amt = int(floor * eff[1])
        if amt >= 0:
            p.gold += amt
            return [(f"💰 골드 +{amt}", C.YELLOW)]
        amt = min(p.gold, -amt); p.gold -= amt
        return [(f"💸 골드 -{amt}", C.YELLOW)]
    if kind == "hurt":
        dmg = max(1, int(p.total_max_hp * eff[1]))
        p.hp = max(1, p.hp - dmg)            # 이벤트로는 죽지 않음(최소 1)
        return [(f"🩸 함정! HP -{dmg}", C.RED)]
    if kind == "potion":
        pot = make_potion("hp", greater=eff[1]); p.inventory.append(pot)
        return [(f"🧪 {pot['name']}을(를) 얻었다!", C.GREEN)]
    if kind == "weapon":
        g = make_weapon(level=floor, rarity_bonus=dungeon_rarity_bonus(floor, region))
        p.inventory.append(g)
        return [(f"🗡 {item_label(g)}을(를) 발견했다!", C.CYAN)]
    if kind == "armor":
        g = make_armor(level=floor, rarity_bonus=dungeon_rarity_bonus(floor, region))
        p.inventory.append(g)
        return [(f"🛡 {item_label(g)}을(를) 발견했다!", C.CYAN)]
    if kind == "xp":
        amt = int(floor * eff[1])
        cprint(f"  📖 깨달음을 얻었다. (경험치 +{amt})", C.MAGENTA)
        p.gain_xp(amt)
        return []
    if kind == "gamble":
        stake = int(floor * eff[1])
        if random.random() < 0.5:
            p.gold += stake
            return [(f"🎲 승리! 골드 +{stake}", C.GREEN)]
        lost = min(p.gold, stake); p.gold -= lost
        return [(f"🎲 패배... 골드 -{lost}", C.RED)]
    if kind == "buy_potion":
        _, pk, cost = eff
        if p.gold < cost:
            return [(f"골드가 부족하다. (필요 {cost}G)", C.GRAY)]
        p.gold -= cost; pot = make_potion(pk); p.inventory.append(pot)
        return [(f"🧪 {pot['name']} 구매! (-{cost}G)", C.GREEN)]
    if kind == "full_heal":
        p.hp = p.total_max_hp; p.mp = p.max_mp
        return [("✨ 신비한 빛이 당신을 완전히 회복시킨다! (HP·MP 전회복)", C.GREEN)]
    if kind == "ambush":
        return [("__ambush__", None)]               # run_event에서 전투 처리
    return [("...", C.GRAY)]


def run_event(p, floor, region=None):
    ev = random.choice(DUNGEON_EVENTS)
    line("·")
    cprint(f"  ❖ {ev['intro']}", C.WHITE, bold=True)
    for i, (label, _) in enumerate(ev["choices"], 1):
        cprint(f"    {i}) {label}", C.CYAN)
    sel = ask("  선택 > ")
    idx = int(sel) - 1 if sel.isdigit() else len(ev["choices"]) - 1
    if not (0 <= idx < len(ev["choices"])):
        idx = len(ev["choices"]) - 1
    eff = ev["choices"][idx][1]
    ambush = False
    for msg, col in _resolve_effect(p, eff, floor, region):
        if msg == "__ambush__":
            ambush = True
            continue
        cprint("  " + msg, col)
    if ambush:
        cprint("  ⚔ 매복! 숨어 있던 적이 덮쳐온다!", C.RED, bold=True)
        time.sleep(0.2)
        if combat(p, make_monster(floor, region=region)) == "dead":
            return "dead"
    else:
        pause()
    return None


def choose_region(p):
    line("═")
    cprint("  🗺  어느 지역을 탐험할까?", C.WHITE, bold=True)
    cprint("  (기본 난이도는 레벨에 맞춰지되, 깊은 지역일수록 더 강하고 더 좋은 전리품이 나옵니다)", C.GRAY)
    line()
    for i, r in enumerate(REGIONS, 1):
        gate = "" if p.level >= r["rec"] else f"  {C.GRAY}(권장 Lv.{r['rec']}+){C.RESET}"
        tier = region_tier(r)
        risk = "★" * (tier + 1)
        cprint(f"  {i}) {r['color']}{r['icon']} {r['name']}{C.RESET} — {r['desc']}{gate}", C.WHITE)
        cprint(f"       {C.GRAY}난이도/전리품 {risk}"
               f"  (적 강화 +{int(REGION_DIFF_STEP*tier*100)}%, 보상 +{int(REGION_REWARD_STEP*tier*100)}%){C.RESET}",
               C.GRAY)
    cprint("  0) 돌아가기", C.GRAY)
    sel = ask("  > ")
    if sel.isdigit() and 1 <= int(sel) <= len(REGIONS):
        return REGIONS[int(sel) - 1]
    return None


def special_encounter_choice(p, floor, region=None):
    """특수 몬스터 조우 ― 도전(특수) 또는 회피(일반) 선택. 선택된 몬스터를 반환."""
    sm = make_special_monster(floor, region)
    line("·")
    cprint(f"  ❗ 강력한 기운이 느껴진다 — {sm['icon']} {sm['name']}이(가) 도사리고 있다!",
           C.MAGENTA, bold=True)
    cprint("    1) 도전한다  (강하지만 특수 장신구 확정 드랍)", C.YELLOW)
    cprint("    2) 피해 간다 (평범한 적과 싸운다)", C.CYAN)
    sel = ask("  선택 > ")
    if sel.strip() == "1":
        cprint("  당신은 위험을 무릅쓰고 맞선다!", C.RED)
        return sm
    cprint("  조용히 우회해 평범한 적을 상대한다.", C.GRAY)
    return make_monster(floor, region=region)


def dungeon(p):
    global NG_MULT
    NG_MULT = 1 + NG_STEP * getattr(p, "ng", 0)
    region = choose_region(p)
    if region is None:
        return True                       # 취소 → 마을로(피해 없음)
    floor = p.level
    cprint(f"\n  {region['icon']} {region['name']}에 들어선다...", region["color"])
    if region["name"] not in getattr(p, "regions_seen", []):
        p.regions_seen = list(getattr(p, "regions_seen", [])) + [region["name"]]
        slow(f"  ❖ {region.get('lore', '')}", region["color"])
    cprint(f"  〔{region['name']} · 지하 {floor}층〕 {random.choice(region['intros'])}", region["color"])
    pause()
    encounters = random.randint(2, 4)
    events_left = 2                       # 던전당 이벤트 최대 2회
    for n in range(1, encounters + 1):
        if events_left > 0 and random.random() < 0.30:
            events_left -= 1
            if run_event(p, floor, region) == "dead":
                return False
        boss = (n == encounters) and random.random() < 0.5
        if not boss and random.random() < SPECIAL_RATE:
            m = special_encounter_choice(p, floor, region)   # 특수 조우 → 도전/회피 선택
        else:
            m = make_monster(floor, boss=boss, region=region)
        if combat(p, m) == "dead":
            return False
        if n < encounters:
            cprint(f"  더 깊이... ({n}/{encounters})", C.GRAY)
            time.sleep(0.25)
    slow("  던전을 정복하고 귀환했다!", C.GREEN)
    bonus = 12 * floor
    p.gold += bonus
    cprint(f"  탐험 보너스 +{bonus}G", C.YELLOW)
    pause()
    return True


# ─────────────────────────────────────────────────────────────
#  저장/불러오기
# ─────────────────────────────────────────────────────────────
def save_game(p):
    try:
        with open(SAVE_FILE, "w", encoding="utf-8") as f:
            json.dump(p.to_dict(), f, ensure_ascii=False, indent=2)
        cprint("  저장 완료.", C.GREEN)
    except Exception as e:
        cprint(f"  저장 실패: {e}", C.RED)
    pause()


def load_game():
    if not os.path.exists(SAVE_FILE):
        return None
    try:
        with open(SAVE_FILE, "r", encoding="utf-8") as f:
            return Player.from_dict(json.load(f))
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
#  생성 / 메인
# ─────────────────────────────────────────────────────────────
HELP_TOPICS = [
    ("기본 조작 · 전투", [
        "• 모든 판정은 d20(20면 주사위) 기반입니다. 공격 시 1d20+명중 ≥ 적 AC면 명중.",
        "• 자연 20 = 치명타(피해 주사위 2배), 자연 1 = 자동 빗나감.",
        "• 전투 메뉴: 1)공격  2)스킬  3)아이템  4)도망.",
        "• 선제(이니셔티브)가 높으면 먼저 행동합니다. 민첩·선제 장비가 영향을 줍니다.",
        "• MP는 전투·던전 사이에 자동 회복되지 않습니다. 여관 휴식·물약·레벨업으로만 회복.",
        "• 평타로 MP를 아끼고 결정적일 때 스킬을 쓰는 운영이 핵심입니다.",
    ]),
    ("상태이상 · 속성", [
        "• 상태이상: 중독·화상(매턴 피해), 빙결(불리점), 기절(행동 불가).",
        "• 매 턴 종료 시 내성 굴림(1d20+내성)으로 해제를 시도합니다.",
        "• 속성: 물리/화염/냉기/독. 적의 취약 속성=피해 2배, 저항 속성=피해 절반.",
        "• 적이 강공격을 준비하면 '힘을 모은다/자세를 잡는다' 신호가 먼저 뜹니다 — 방어·회복으로 대비하세요.",
    ]),
    ("성장 · 전직 · 방어 스킬", [
        "• 경험치로 레벨업하면 능력치·HP·MP가 오르고 새 스킬을 배웁니다.",
        "• 1차 전직: Lv.5+ (마을 t). 2차 전직: Lv.12+ & 1차 완료.",
        "• 전직 시 직업별 상위 직업과 스킬·궁극기를 '선택'합니다(분기).",
        "• 직업마다 방어 스킬 보유: 전사 방패 올리기, 마법사 마력 방벽, 도적 그림자 장막.",
    ]),
    ("던전 · 지역 · 이벤트", [
        "• 던전 진입 시 5개 지역 중 선택(숲·묘지·협곡·빙굴·심연). 지역마다 적과 분위기가 다릅니다.",
        "• 난이도는 당신 레벨에 맞춰 스케일되니 권장 레벨은 참고용입니다.",
        "• 던전 중 선택형 이벤트가 등장(제단·상자·매복·갈림길 등). 선택에 따라 보상/위험이 갈립니다.",
        "• 특수 몬스터 조우 시 '도전(특수 장신구 확정)/회피(일반 적)'를 고를 수 있습니다.",
    ]),
    ("장비 · 장신구 · 패시브", [
        "• 슬롯: 무기·방어구·장신구 2개. 장신구는 빈 슬롯 자동 장착, 가득 차면 교체 선택.",
        "• 갑옷은 직업 숙련이 필요 — 미숙련 착용 시 AC 절반·공격 불리점.",
        "• 무기/방어구엔 스펙 접두(예리한·연타의·견고한 등)가 붙어 효과가 추가됩니다.",
        "• 특수 몬스터는 강력한 패시브를 주는 전용 장신구를 확정 드랍합니다.",
    ]),
    ("보스 · 회차(NG+) · 엔딩", [
        "• 2차 전직 후 마을에서 최종 보스(b)에 도전. 페이즈 전환·궁극기 충전에 주의.",
        "• 보스 처치 후 2회차(NG+)로 더 강한 적·보상에 도전하거나 엔딩을 볼 수 있습니다.",
        "• 칭호·도감(c)에서 달성/미달성 엔딩과 칭호를 확인·장착할 수 있습니다.",
        "• 달성 가능한 칭호를 모두 모으면 숨겨진 결전(x)과 진엔딩이 열립니다.",
    ]),
]


def help_menu():
    while True:
        line("═")
        cprint("  ❓ 도움말 — 보고 싶은 주제를 고르세요", C.WHITE, bold=True)
        line()
        for i, (t, _) in enumerate(HELP_TOPICS, 1):
            cprint(f"  {i}) {t}", C.CYAN)
        cprint("  0) 닫기", C.GRAY)
        sel = ask("  > ")
        if sel == "0" or not sel.isdigit() or not (1 <= int(sel) <= len(HELP_TOPICS)):
            return
        title, lines = HELP_TOPICS[int(sel) - 1]
        line("─")
        cprint(f"  【{title}】", C.YELLOW, bold=True)
        for ln in lines:
            cprint("  " + ln, C.WHITE)
        line("─")
        pause()


def quick_start_hint():
    line("═")
    cprint("  ▶ 빠른 시작 안내", C.CYAN, bold=True)
    cprint("  • 마을에서 1) 던전 탐험으로 모험을 시작하세요.", C.WHITE)
    cprint("  • HP/MP는 저절로 회복되지 않습니다 — 3) 여관에서 휴식(골드)하거나 물약을 쓰세요.", C.WHITE)
    cprint("  • Lv.5에 전직(t)이 열립니다.", C.WHITE)
    cprint("  • 장비(무기/방어구) 상세 정보는 c) 📖 도감에서 확인할 수 있습니다.", C.WHITE)
    cprint("  • 자세한 설명은 언제든 h) 도움말에서 확인하세요.", C.WHITE)
    line("═")
    pause()


def show_tutorial():
    """신규 모험가용 가이드 튜토리얼 (선택 시에만 표시)."""
    pages = [
        ("① 목표", [
            "세계수를 좀먹는 '종언의 침식'을 끊는 것이 당신의 목표입니다.",
            "마을을 거점 삼아 던전을 공략하며 강해지고, 끝내 최종 보스에 도전하세요.",
        ]),
        ("② 전투 (d20)", [
            "공격은 1d20 + 명중 ≥ 적 AC면 명중합니다.",
            "자연 20 = 치명타(피해 2배), 자연 1 = 자동 빗나감.",
            "전투 메뉴는 1)공격 2)스킬 3)아이템 4)도망. 보스에게선 도망칠 수 없습니다.",
            "평타로 MP를 아끼고, 결정적인 순간에 스킬을 쓰는 운영이 핵심입니다.",
        ]),
        ("③ 자원 관리 (중요!)", [
            "HP/MP는 전투·던전 사이에 자동 회복되지 않습니다.",
            "회복 수단은 여관 휴식(골드), 물약, 레벨업뿐입니다.",
            "무리하지 말고 적절히 후퇴해 회복하는 판단이 생존을 좌우합니다.",
        ]),
        ("④ 성장 · 전직", [
            "적을 처치하면 경험치를 얻고, 레벨업으로 능력치·HP·MP·스킬이 늘어납니다.",
            "Lv.5+에 1차 전직(마을 t), Lv.12+ & 1차 완료 시 2차 전직이 열립니다.",
            "전직 때 상위 직업과 스킬·궁극기를 직접 '선택'합니다.",
        ]),
        ("⑤ 던전 · 장비", [
            "던전 진입 시 5개 지역 중 선택. 난이도는 당신 레벨에 맞춰집니다.",
            "갑옷은 직업 숙련이 필요 — 미숙련 착용 시 AC가 절반이 되고 공격에 불리점.",
            "특수 몬스터는 강력한 패시브를 주는 전용 장신구를 확정 드랍합니다.",
        ]),
    ]
    line("═")
    cprint("  📖 튜토리얼 — 모험을 시작하기 전에", C.WHITE, bold=True)
    line()
    for title, body in pages:
        cprint(f"  【{title}】", C.YELLOW, bold=True)
        for ln in body:
            cprint("  • " + ln, C.WHITE)
        print()
    cprint("  ※ 더 자세한 내용은 마을 메뉴의 h) ❓ 도움말에서 언제든 볼 수 있습니다.", C.CYAN)
    line("═")
    pause()


def create_character(name=None, skip_intro=False):
    line("═")
    cprint("  새 모험가 생성", C.WHITE, bold=True)
    line()
    if name is None:
        name = ask("  이름: ") or "용사"
    else:
        cprint(f"  이름: {name} (이전 모험가에서 이어받음)", C.GRAY)
    print()
    keys = list(CLASSES)
    for i, k in enumerate(keys, 1):
        info = CLASSES[k]
        cprint(f"  {i}) {info['color']}{k}{C.RESET} — {info['desc']}", C.WHITE)
        ab = info['abilities']
        print(f"       근력 {ab['str']} 민첩 {ab['dex']} 건강 {ab['con']} 지능 {ab['int']}  "
              f"히트다이스 d{info['hit_die']}  주공격 {ABILITY_KR[info['attack_stat']]}")
    while True:
        sel = ask("  직업 선택 > ")
        if sel.isdigit() and 1 <= int(sel) <= len(keys):
            cls = keys[int(sel) - 1]; break
    p = Player(name, cls)
    slow(f"\n  {name}, {cls}의 여정이 시작된다!", CLASSES[cls]['color'])
    pause()
    if not skip_intro:                        # 다시 시작 시에는 프롤로그·튜토리얼 생략
        show_prologue(p)
        line("═")
        if ask("  📖 튜토리얼(게임 방법)을 보시겠습니까? (y/n) > ").lower() == "y":
            show_tutorial()
        else:
            quick_start_hint()
    return p


PROLOGUE = [
    "세계의 뿌리, 거대한 세계수 위그드라가 병들었다.",
    "그 뿌리에서 '종언의 침식'이 스며 나와 땅을 하나씩 집어삼킨다.",
    "숲이, 묘지가, 불과 얼음의 땅이 차례로 검게 물들어 간다.",
    "침식의 근원을 끊을 수 있는 건, 더 깊이 내려가 그 심장에 닿는 자뿐.",
]


def show_prologue(p):
    line("═")
    cprint("  ― 프 롤 로 그 ―", C.YELLOW, bold=True)
    print()
    for s in PROLOGUE:
        slow("    " + s, C.WHITE)
    slow(f"    {p.name}, 당신은 세계수의 심장을 향해 첫걸음을 내디딘다.", C.CYAN)
    line("═")
    pause()


def title():
    print()
    cprint("  ╔══════════════════════════════════════╗", C.YELLOW)
    cprint("  ║     검과 마법의 텍스트 RPG  (d20)     ║", C.YELLOW, bold=True)
    cprint("  ╚══════════════════════════════════════╝", C.YELLOW)
    print()


def _death_flow(p):
    line("═")
    cprint("  당신은 쓰러졌다...", C.RED, bold=True)
    cprint(f"  최종 레벨: {p.level}", C.RED)
    if os.path.exists(SAVE_FILE):
        # 저장 기록이 있으면: 저장 지점에서 부활
        if ask("  저장 지점에서 부활할까요? (y/n) > ").lower() == "y":
            loaded = load_game()
            if loaded:
                loaded.heal_full()
                cprint("  부활했다!", C.GREEN); pause()
                return loaded
    else:
        # 저장 기록이 없으면: 처음부터 다시 시작
        if ask("  처음부터 다시 시작하시겠습니까? (y/n) > ").lower() == "y":
            cprint("  새로운 모험을 시작합니다...", C.GREEN); pause()
            return create_character(name=p.name, skip_intro=True)
    cprint("\n  GAME OVER", C.RED, bold=True)
    return None


def main():
    title()
    p = None
    if os.path.exists(SAVE_FILE):
        if ask("  저장된 게임이 있습니다. 불러올까요? (y/n) > ").lower() == "y":
            p = load_game()
            if p:
                cprint(f"  {p.name} (Lv.{p.level}) 불러오기 완료!", C.GREEN); pause()
    if p is None:
        p = create_character()

    while True:
        if not p.is_alive():
            p.hp = 1
        line("═")
        ng = getattr(p, "ng", 0)
        ngtag = f"  ★{ng}회차" if ng else ""
        tt = title_str(p)
        tt = (tt + " ") if tt else ""
        cprint(f"  🏰 마을  ―  {tt}{p.name} ({p.disp_title}) Lv.{p.level}  골드 {p.gold}{ngtag}",
               C.WHITE, bold=True)
        line()
        cprint("  1) 던전 탐험   2) 상점   3) 여관", C.CYAN)
        cprint("  4) 상태        5) 인벤토리", C.CYAN)
        cprint("  6) 저장        7) 종료", C.CYAN)
        cprint("  c) 🏅 칭호·도감   h) ❓ 도움말", C.CYAN)
        if not getattr(p, "advanced", None) and p.level >= ADVANCE_LEVEL:
            cprint("  t) ✦ 전직 가능!", C.YELLOW, bold=True)
        elif getattr(p, "advanced", None) and not getattr(p, "advanced2", None) \
                and p.level >= ADVANCE_LEVEL_2:
            cprint("  t) ✦✦ 2차 전직 가능!", C.YELLOW, bold=True)
        if getattr(p, "advanced2", None):
            cprint("  b) 🔥 최종 보스 도전", C.RED, bold=True)
        if secret_unlocked(p):
            cprint("  x) 🌑 숨겨진 결전 — 종언의 군주", C.MAGENTA, bold=True)
        if getattr(p, "bosses_defeated", []):
            cprint("  e) 🎬 여정 마무리 (엔딩)", C.YELLOW)
        ch = ask("  > ")

        if ch == "1":
            if not dungeon(p):
                revived = _death_flow(p)
                if revived:
                    p = revived; continue
                break
        elif ch.lower() == "b" and getattr(p, "advanced2", None):
            res = final_boss_fight(p)
            if res == "dead":
                revived = _death_flow(p)
                if revived:
                    p = revived; continue
                break
            elif res == "ending":
                if ask("  계속 플레이하시겠습니까? (y/n) > ").lower() != "y":
                    save_game(p)
                    cprint("  모험을 마칩니다. 안녕히!", C.YELLOW)
                    break
        elif ch.lower() == "x" and secret_unlocked(p):
            res = secret_boss_fight(p)
            if res == "dead":
                revived = _death_flow(p)
                if revived:
                    p = revived; continue
                break
            elif res == "ending":
                if ask("  계속 플레이하시겠습니까? (y/n) > ").lower() != "y":
                    save_game(p)
                    cprint("  모험을 마칩니다. 안녕히!", C.YELLOW)
                    break
        elif ch.lower() == "e" and getattr(p, "bosses_defeated", []):
            show_ending(p)
            if ask("  계속 플레이하시겠습니까? (y/n) > ").lower() != "y":
                save_game(p)
                cprint("  모험을 마칩니다. 안녕히!", C.YELLOW)
                break
        elif ch.lower() == "c":
            codex_menu(p)
        elif ch.lower() == "h":
            help_menu()
        elif ch == "2":
            shop(p)
        elif ch == "3":
            inn(p)
        elif ch == "4":
            show_status(p)
        elif ch == "5":
            inventory_menu(p)
        elif ch == "6":
            save_game(p)
        elif ch.lower() == "t":
            do_advance(p)
        elif ch == "7" or ch == "quit":
            if ask("  저장하고 종료할까요? (y/n) > ").lower() == "y":
                save_game(p)
            cprint("  모험을 마칩니다. 안녕히!", C.YELLOW)
            break
        else:
            cprint("  잘못된 입력.", C.GRAY)


if __name__ == "__main__":
    main()
