"""
core/state.py ── Pulse 开源版
三轴状态系统 (energy / mood / social_need)
三阶段关系 (陌生 / 熟悉 / 亲密)
作息时间从 character.yaml 动态读取
"""

import json
import math
import os
import random
from datetime import datetime
from typing import List, Dict, Optional


# ── 配置加载器 ────────────────────────────────────────────────────────────
class ConfigLoader:
    _cache = {}

    @classmethod
    def load(cls, path: str) -> dict:
        if path not in cls._cache:
            try:
                with open(path, encoding="utf-8") as f:
                    cls._cache[path] = json.load(f)
            except:
                cls._cache[path] = {}
        return cls._cache[path]

    @classmethod
    def reload(cls, path: str) -> dict:
        if path in cls._cache:
            del cls._cache[path]
        return cls.load(path)


def get_char_config() -> dict:
    return ConfigLoader.load(os.path.join("data", "character", "character_config.json"))

def get_behavior_config() -> dict:
    return ConfigLoader.load(os.path.join("data", "character", "behavior_config.json"))

def get_prompts() -> dict:
    return ConfigLoader.load(os.path.join("data", "character", "prompts.json"))


# ── 标签查询 ─────────────────────────────────────────────────────────────
def get_mood_label(value: float) -> str:
    labels = get_char_config().get("mood_labels", {})
    for label, rule in labels.items():
        if rule.get("min", 0) <= value < rule.get("max", 1):
            return label
    return "平静"

def get_energy_label(value: float) -> str:
    labels = get_char_config().get("energy_labels", {})
    for label, rule in labels.items():
        if rule.get("min", 0) <= value < rule.get("max", 1):
            return label
    return "正常"

def get_axis_label(axis_name: str, value: float) -> str:
    cfg = get_char_config().get("axis_labels", {}).get(axis_name, {})
    sorted_labels = sorted(cfg.items(), key=lambda x: x[1], reverse=True)
    for label, threshold in sorted_labels:
        if value >= threshold:
            return label
    return list(cfg.keys())[-1] if cfg else "未知"


# ── 活动库管理 ───────────────────────────────────────────────────────────
class ActivityPoolManager:

    DEFAULT_POOLS_PATH = os.path.join("data", "character", "activity_pools.json")
    CUSTOM_POOLS_PATH  = os.path.join("data", "character", "custom_activities.json")

    def __init__(self):
        self.pools  = self._load(self.DEFAULT_POOLS_PATH)
        self.custom = self._load(self.CUSTOM_POOLS_PATH)

    def _load(self, path: str) -> dict:
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except:
            return {}

    def _save_custom(self):
        os.makedirs(os.path.dirname(self.CUSTOM_POOLS_PATH), exist_ok=True)
        with open(self.CUSTOM_POOLS_PATH, "w", encoding="utf-8") as f:
            json.dump(self.custom, f, ensure_ascii=False, indent=2)

    def reload(self):
        self.pools  = self._load(self.DEFAULT_POOLS_PATH)
        self.custom = self._load(self.CUSTOM_POOLS_PATH)

    def get_pool(self, phase: str) -> List[Dict]:
        base   = self.pools.get(phase, [])
        custom = self.custom.get(phase, [])
        return base + custom

    def check_trigger(self, entry: Dict, axes: Dict) -> bool:
        condition = entry.get("trigger_condition", {})
        for axis_name, rule in condition.items():
            val = axes.get(axis_name, {}).get("value", 50)
            if isinstance(rule, (int, float)):
                rule = {"min": rule}
            if not isinstance(rule, dict):
                continue
            if "min" in rule and val < rule["min"]:
                return False
            if "max" in rule and val > rule["max"]:
                return False
        return True

    def calculate_weight(self, entry: Dict, axes: Dict,
                         boost_keywords: List[str] = None) -> float:
        weight = float(entry.get("base_weight", 2))
        condition = entry.get("trigger_condition", {})
        for axis_name, rule in condition.items():
            val = axes.get(axis_name, {}).get("value", 50)
            if isinstance(rule, (int, float)):
                rule = {"min": rule}
            if not isinstance(rule, dict):
                continue
            if "min" in rule and val >= rule["min"]:
                excess = (val - rule["min"]) / (100 - rule["min"] + 1)
                weight *= (1.0 + excess * 0.5)
        boost_keywords = [k.lower() for k in (boost_keywords or [])]
        if boost_keywords:
            for tag in entry.get("tags", []):
                if any(kw in tag.lower() or tag.lower() in kw for kw in boost_keywords):
                    weight *= 2.0
                    break
        return max(0.1, weight)

    def weighted_choice(self, phase: str, axes: Dict,
                        boost_keywords: List[str] = None) -> Dict:
        pool = self.get_pool(phase)
        if not pool:
            return {"text": "在发呆", "tags": [], "axis_effects": {}}

        candidates, weights = [], []
        for entry in pool:
            if self.check_trigger(entry, axes):
                candidates.append(entry)
                weights.append(self.calculate_weight(entry, axes, boost_keywords))

        if not candidates:
            for entry in pool:
                if not entry.get("trigger_condition"):
                    candidates.append(entry)
                    weights.append(float(entry.get("base_weight", 1)))

        if not candidates:
            return {"text": "在发呆", "tags": [], "axis_effects": {}}

        return random.choices(candidates, weights=weights, k=1)[0]

    def add_custom_activity(self, phase: str, text: str, tags: List[str] = None,
                            axis_effects: Dict = None):
        if phase not in self.custom:
            self.custom[phase] = []
        entry = {
            "text": text, "base_weight": 2,
            "tags": tags or [], "trigger_condition": {},
            "axis_effects": axis_effects or {}, "outcome_variants": {},
        }
        if not any(e["text"] == text for e in self.custom[phase]):
            self.custom[phase].append(entry)
            self._save_custom()
            print(f"[活动库✓] 新增到 {phase}: {text}")

    def boost_activity_weight(self, keyword: str):
        changed = False
        for phase, entries in self.custom.items():
            for entry in entries:
                if keyword.lower() in [t.lower() for t in entry.get("tags", [])]:
                    entry["base_weight"] = min(entry["base_weight"] + 1, 6)
                    changed = True
        if changed:
            self._save_custom()


# ── 作息时段解析 ─────────────────────────────────────────────────────────
def parse_schedule(config: dict) -> tuple:
    """从 character.yaml 的 schedule 解析起床/睡觉时间，返回 (wake_hour, sleep_hour)"""
    schedule = config.get("schedule", {})
    try:
        wake_str  = schedule.get("wake_up", "08:00")
        sleep_str = schedule.get("sleep", "00:00")
        wake_hour  = int(wake_str.split(":")[0])
        sleep_hour = int(sleep_str.split(":")[0])
        return wake_hour, sleep_hour
    except:
        return 8, 0


def get_phase_for_hour(hour: int, wake_hour: int, sleep_hour: int) -> tuple:
    """根据当前小时和作息，返回 (phase_name, sleep_state)"""
    # 计算活跃时段长度（处理跨午夜）
    if sleep_hour <= wake_hour:
        # 跨午夜：如 sleep=2, wake=10 → 睡眠时段 2-10
        is_sleeping = sleep_hour <= hour < wake_hour
    else:
        # 不跨午夜：如 sleep=23, wake=7 → 睡眠时段 23-7
        is_sleeping = hour >= sleep_hour or hour < wake_hour

    if is_sleeping:
        # 接近起床时间的1小时 = waking
        wake_minus_1 = (wake_hour - 1) % 24
        if hour == wake_minus_1:
            return "waking", "waking_up"
        return "sleep", "sleeping"

    # 计算活跃时段内的位置
    if sleep_hour <= wake_hour:
        active_hours = list(range(0, sleep_hour)) + list(range(wake_hour, 24))
    else:
        active_hours = list(range(wake_hour, sleep_hour))

    if hour not in active_hours:
        return "sleep", "sleeping"

    idx = active_hours.index(hour)
    total = len(active_hours)
    ratio = idx / max(1, total - 1)

    if ratio < 0.1:
        return "waking", "waking_up"
    elif ratio < 0.3:
        return "early_active", "awake"
    elif ratio < 0.6:
        return "mid_active", "awake"
    elif ratio < 0.8:
        return "late_active", "awake"
    elif ratio < 0.95:
        return "winding_down", "awake"
    else:
        return "pre_sleep", "awake"


# ── CharacterState ────────────────────────────────────────────────────────
class CharacterState:

    def __init__(self, data_dir="./data", character_config=None):
        self.data_dir = data_dir
        self.config   = character_config or {}

        # 从 character.yaml 解析作息
        self.wake_hour, self.sleep_hour = parse_schedule(self.config)

        self.state_file      = os.path.join(data_dir, "character", "state_current.json")
        self.state_core_file = os.path.join(data_dir, "character", "state_core.json")
        os.makedirs(os.path.dirname(self.state_file), exist_ok=True)

        self.pool_manager = ActivityPoolManager()
        self.silent       = True
        self.state        = self._load_or_init_state()
        self.state_core   = self._load_or_init_core()
        self._context_keywords: List[str] = []

    # ── 加载 ─────────────────────────────────────────────────────────────
    def _load_or_init_state(self) -> Dict:
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, encoding="utf-8") as f:
                    s = json.load(f)
                print(f"[状态] 已加载 (心情:{get_mood_label(s['mood'])}, "
                      f"精力:{get_energy_label(s['energy'])})")
                return s
            except:
                pass
        return {
            "mood": 0.6, "energy": 0.7,
            "current_activity": "休息中",
            "current_activity_entry": {},
            "last_update": datetime.now().isoformat(),
            "sleep_state": "awake",
            "low_mood_active": False,
        }

    def _load_or_init_core(self) -> Dict:
        if os.path.exists(self.state_core_file):
            try:
                with open(self.state_core_file, encoding="utf-8") as f:
                    return json.load(f)
            except:
                pass
        return {
            "三轴状态": {
                "energy":      {"value": 70, "label": "正常"},
                "mood":        {"value": 60, "label": "平静"},
                "social_need": {"value": 50, "label": "随缘"},
            },
            "关系层": {
                "closeness": 0, "trust": 0,
                "当前阶段": 0, "阶段名称": "陌生",
                "last_updated": "",
            },
            "衰减记录": {
                "上次用户联系时间": "",
                "连续未联系小时数": 0,
            }
        }

    # ── 保存 ─────────────────────────────────────────────────────────────
    def save(self):
        self.state["last_update"] = datetime.now().isoformat()
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2, ensure_ascii=False)

    def save_core(self):
        self.state_core["关系层"]["last_updated"] = datetime.now().isoformat()
        with open(self.state_core_file, "w", encoding="utf-8") as f:
            json.dump(self.state_core, f, indent=2, ensure_ascii=False)

    # ── 三轴效果应用 ─────────────────────────────────────────────────────
    def apply_axis_effects(self, effects: Dict, scale: float = 1.0):
        axes = self.state_core["三轴状态"]
        for axis_name, delta in effects.items():
            if axis_name not in axes:
                continue
            old_val = axes[axis_name]["value"]
            new_val = old_val + delta * scale
            axes[axis_name]["value"] = round(max(0.0, min(100.0, new_val)), 1)
        self._update_axis_labels()
        self._sync_state_from_axes()

    def _update_axis_labels(self):
        axes = self.state_core["三轴状态"]
        for axis_name, axis in axes.items():
            axis["label"] = get_axis_label(axis_name, axis["value"])

    def _sync_state_from_axes(self):
        axes = self.state_core["三轴状态"]
        self.state["energy"] = round(axes["energy"]["value"] / 100.0, 2)
        self.state["mood"] = round(
            self.state["mood"] * 0.7 + (axes["mood"]["value"] / 100.0) * 0.3, 2
        )

    # ── 阶段自动判断（三阶段）───────────────────────────────────────────
    def calculate_stage(self) -> int:
        r        = self.state_core["关系层"]
        trust    = r["trust"]
        close    = r["closeness"]
        char_cfg = get_char_config()
        thresholds = char_cfg.get("stage_thresholds", {})

        stage = 0
        if close >= thresholds.get("2", {}).get("closeness_min", 70) and \
           trust >= thresholds.get("2", {}).get("trust_min", 50):
            stage = 2
        elif close >= thresholds.get("1", {}).get("closeness_min", 30) and \
             trust >= thresholds.get("1", {}).get("trust_min", 20):
            stage = 1

        stage_name = thresholds.get(str(stage), {}).get("name", "陌生")
        r["当前阶段"] = stage
        r["阶段名称"] = stage_name
        return stage

    # ── 关系层事件响应 ───────────────────────────────────────────────────
    def apply_relation_event(self, event_type: str, intensity: float = 1.0):
        r       = self.state_core["关系层"]
        char_cfg = get_char_config()
        rules   = char_cfg.get("relation_events", {})
        deltas  = rules.get(event_type, {})

        for key, delta in deltas.items():
            if key in r:
                new_val = r[key] + delta * intensity
                new_val = max(-100, min(100, new_val))
                r[key] = round(new_val, 1)

        self.calculate_stage()
        self.save_core()
        if not self.silent:
            print(f"[关系层] 事件：{event_type}（×{intensity:.1f}），阶段：{r['阶段名称']}")

    # ── 衰减检查 ─────────────────────────────────────────────────────────
    def check_relation_decay(self, last_message_time: str = ""):
        if not last_message_time:
            return
        behavior_cfg = get_behavior_config()
        threshold_hours = behavior_cfg.get("relation_decay", {}).get(
            "no_contact_hours_threshold", 48)
        try:
            last_time = datetime.fromisoformat(last_message_time)
            hours_gap = (datetime.now() - last_time).total_seconds() / 3600
            self.state_core["衰减记录"]["连续未联系小时数"] = round(hours_gap, 1)
            if hours_gap >= threshold_hours:
                self.apply_relation_event("long_absence", intensity=1.0)
        except:
            pass

    # ── 主更新循环 ───────────────────────────────────────────────────────
    def update(self, minutes_passed: float = 15.0):
        s    = self.state
        now  = datetime.now()
        h    = now.hour
        axes = self.state_core["三轴状态"]
        kw   = self._context_keywords

        # 从作息表确定当前时段
        phase, sleep_state = get_phase_for_hour(h, self.wake_hour, self.sleep_hour)
        s["sleep_state"] = sleep_state

        entry = self.pool_manager.weighted_choice(phase, axes, kw)
        s["current_activity"]       = entry.get("text", "在发呆")
        s["current_activity_entry"] = entry

        effects = entry.get("axis_effects", {})
        if effects:
            self.apply_axis_effects(effects, scale=minutes_passed / 60.0)

        # 低气压随机触发
        behavior_cfg     = get_behavior_config()
        low_mood_cfg     = behavior_cfg.get("low_mood", {})
        base_prob        = low_mood_cfg.get("base_probability", 0.02)
        mood_val         = axes["mood"]["value"]
        stability_factor = 1.0 - mood_val / 100.0
        recovery_thresh  = low_mood_cfg.get("recovery_threshold", 0.55)
        low_mood         = s.get("low_mood_active", False)

        if not low_mood and random.random() < base_prob * (1 + stability_factor):
            s["low_mood_active"] = True
            s["mood"] = random.uniform(0.15, 0.35)
            if not self.silent:
                print(f"[状态] 今天心情有点低落")
        elif low_mood and s["mood"] > recovery_thresh:
            s["low_mood_active"] = False

        self._sync_state_from_axes()
        self.save()
        self.save_core()

    # ── 活动结果处理 ─────────────────────────────────────────────────────
    def apply_activity_outcome(self, outcome: str = "success"):
        entry    = self.state.get("current_activity_entry", {})
        variants = entry.get("outcome_variants", {})
        effects  = variants.get(outcome, {})
        if effects:
            self.apply_axis_effects(effects, scale=1.0)

    # ── 阶段语气指导 ─────────────────────────────────────────────────────
    def get_stage_tone_guide(self) -> str:
        stage    = self.state_core["关系层"]["当前阶段"]
        char_cfg = get_char_config()
        thresholds = char_cfg.get("stage_thresholds", {})
        desc     = thresholds.get(str(stage), {}).get("desc", "")
        return f"当前阶段：{desc}"

    # ── system prompt 摘要 ───────────────────────────────────────────────
    def get_state_summary(self) -> str:
        s          = self.state
        mood_label = get_mood_label(s["mood"])
        eng_label  = get_energy_label(s["energy"])
        axes       = self.state_core["三轴状态"]
        low_mood   = "（今天心情有点低落）" if s.get("low_mood_active") else ""
        stage_guide = self.get_stage_tone_guide()
        char_cfg   = get_char_config()
        greeting   = char_cfg.get("greeting_rules", "")

        char_name = self.config.get("name", "角色")

        return f"""【{char_name}当前状态（不直接说出来，用来决定语气）】
心情：{mood_label}（{s['mood']:.2f}）{low_mood}
精力：{eng_label}（{s['energy']:.2f}）
社交需求：{axes['social_need']['label']}（{axes['social_need']['value']:.0f}）
现在正在做：{s['current_activity']}（这是事实，回复时必须与此一致，不能自行编造其他活动）

{stage_guide}

{greeting}"""

    # ── 兼容接口 ─────────────────────────────────────────────────────────
    def add_custom_activity(self, phase: str, text: str, tags: List[str] = None):
        self.pool_manager.add_custom_activity(phase, text, tags)

    def boost_activity_weight(self, keyword: str):
        self.pool_manager.boost_activity_weight(keyword)

    def set_context_keywords(self, keywords: List[str]):
        self._context_keywords = keywords

    def react_to_event(self, event_type: str, intensity: float = 0.5):
        s = self.state
        if event_type == "user_needy":
            self.apply_relation_event("gentle_interaction", intensity=intensity)
            s["mood"] = min(1.0, s["mood"] + 0.08 * intensity)
        elif event_type == "user_negative":
            self.apply_relation_event("conflict", intensity=intensity * 0.5)
            s["mood"] = max(0.0, s["mood"] - 0.03 * intensity)
        elif event_type == "user_positive":
            self.apply_relation_event("good_conversation", intensity=intensity * 0.5)
            s["mood"] = min(1.0, s["mood"] + 0.05 * intensity)
        self.save()

    def is_awake(self, now: datetime = None) -> bool:
        hour = (now or datetime.now()).hour
        _, sleep_state = get_phase_for_hour(hour, self.wake_hour, self.sleep_hour)
        return sleep_state != "sleeping"


if __name__ == "__main__":
    cs = CharacterState(data_dir="./data")
    cs.silent = False
    cs.update(15.0)
    print(cs.get_state_summary())
