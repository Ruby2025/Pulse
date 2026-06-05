"""
core/growth.py ── Pulse 开源版
简化版成长框架：
  - 对话事件写入积压池
  - 衰减计算
  - 累积达阈值 → L3 表现层自动渗透
  - 漂移日志记录
"""

import json
import math
import os
import re
import time
from datetime import datetime
from typing import Optional


def _load_json(path: str, default=None):
    if default is None:
        default = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    return default


def _save_json(path: str, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class GrowthManager:
    def __init__(self, llm, data_dir: str = "./data",
                 config_dir: str = "./data/character"):
        self.llm        = llm
        self.data_dir   = data_dir
        self.config_dir = config_dir

        self._pool_path    = os.path.join(data_dir, "growth", "pressure_pool.json")
        self._archive_path = os.path.join(data_dir, "growth", "pressure_archive.json")
        self._log_path     = os.path.join(data_dir, "growth", "drift_log.json")
        self._surface_path = os.path.join(config_dir, "character_profile_surface.json")

        self._load_config()
        self.pool      = _load_json(self._pool_path, [])
        self.archive   = _load_json(self._archive_path, [])
        self.drift_log = _load_json(self._log_path, [])

    def _load_config(self):
        cfg_path = os.path.join(self.config_dir, "growth_config.json")
        cfg = _load_json(cfg_path, {})

        pool_rules         = cfg.get("积压池规则", {})
        self.L3_weight_thr = pool_rules.get("L3_weight_threshold", 30)
        self.L3_count_thr  = pool_rules.get("L3_count_threshold", 15)
        self.event_weights = cfg.get("事件重量标准", {})

        decay_cfg          = cfg.get("衰减规则", {})
        self.decay_lambda  = decay_cfg.get("decay_lambda", 0.04)
        self.decay_thr     = decay_cfg.get("decay_threshold", 0.15)

    # ── 事件写入 ──────────────────────────────────────────────────────────
    def record_event(self, convo: str, relation_state: dict = None):
        if not convo.strip():
            return
        try:
            prompt = self._build_event_detect_prompt(convo)
            raw = self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="", max_tokens=300, temperature=0.3,
            )
            events = self._parse_events(raw)
            if not events:
                return

            for event in events:
                direction   = event.get("direction", "")
                base_weight = event.get("base_weight", 1.0)
                event_type  = event.get("event_type", "日常互动")
                if not direction:
                    continue
                type_weight   = self.event_weights.get(event_type, 1.0)
                actual_weight = base_weight * type_weight
                self._add_to_pool(direction, actual_weight, convo[:100])

            self._save_pool()
        except Exception as e:
            print(f"[成长记录✗] {e}")

    def _add_to_pool(self, direction: str, weight: float, peak_event: str = ""):
        existing = self._find_direction(direction)
        now_ts   = time.time()
        if existing:
            existing["weight_sum"] = round(existing["weight_sum"] + weight, 3)
            existing["count"]     += 1
            existing["last_ts"]    = now_ts
            if weight > existing.get("peak_weight", 0):
                existing["peak_weight"] = weight
                existing["peak_event"]  = peak_event
        else:
            self.pool.append({
                "direction":   direction,
                "weight_sum":  round(weight, 3),
                "count":       1,
                "last_ts":     now_ts,
                "peak_weight": weight,
                "peak_event":  peak_event,
                "status":      "active",
            })

    def _find_direction(self, direction: str) -> Optional[dict]:
        for entry in self.pool:
            if entry.get("status") != "active":
                continue
            if self._has_common_substr(direction, entry["direction"], min_len=4):
                return entry
        return None

    @staticmethod
    def _has_common_substr(a: str, b: str, min_len: int = 4) -> bool:
        for i in range(len(a) - min_len + 1):
            if a[i:i + min_len] in b:
                return True
        return False

    # ── 衰减 ─────────────────────────────────────────────────────────────
    def apply_decay(self):
        now = time.time()
        to_archive = []
        for entry in self.pool:
            if entry.get("status") != "active":
                continue
            hours = (now - entry.get("last_ts", now)) / 3600
            decay = math.exp(-self.decay_lambda * hours / 24)
            entry["weight_sum"] = round(entry["weight_sum"] * decay, 3)
            entry["count"]      = max(0, int(entry["count"] * decay))
            if entry["weight_sum"] < self.decay_thr and entry["count"] < 1:
                to_archive.append(entry)
        for entry in to_archive:
            entry["status"] = "decayed"
            self.archive.append(entry)
            self.pool.remove(entry)
        if to_archive:
            self._save_pool()
            _save_json(self._archive_path, self.archive)

    # ── 阈值检测 → L3 渗透 ───────────────────────────────────────────────
    def check_thresholds(self):
        triggered = []
        for entry in self.pool:
            if entry.get("status") != "active":
                continue
            if entry["weight_sum"] >= self.L3_weight_thr:
                triggered.append((entry, "weight"))
            elif entry["count"] >= self.L3_count_thr:
                triggered.append((entry, "count"))

        for entry, path in triggered:
            self._apply_L3_drift(entry, path)

    def _apply_L3_drift(self, entry: dict, trigger_path: str):
        try:
            surface = _load_json(self._surface_path, {})
            prompt = (
                f"角色的表现层要根据以下变化方向做微调。\n"
                f"变化方向：{entry['direction']}\n"
                f"触发路径：{'累积权重' if trigger_path == 'weight' else '累积次数'}\n"
                f"当前表现层：{json.dumps(surface, ensure_ascii=False)[:500]}\n\n"
                f"输出需要更新的表现层字段（JSON格式）。\n"
                f"改动要微妙自然，不要剧烈。只输出需要修改的部分，只输出JSON。"
            )
            raw = self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="", max_tokens=300, temperature=0.2,
            )
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if not match:
                return
            updates = json.loads(match.group())
            if updates:
                self._deep_update(surface, updates)
                _save_json(self._surface_path, surface)
                self._log_drift(entry, trigger_path, updates)
                print(f"[成长L3✓] {entry['direction'][:30]}... → 表现层已更新")

            # 归档
            entry["weight_sum"] = 0
            entry["count"]      = 0
            entry["status"]     = "archived_success"
            self.archive.append(entry)
            self.pool.remove(entry)
            self._save_pool()
            _save_json(self._archive_path, self.archive)

        except Exception as e:
            print(f"[成长L3✗] {e}")

    # ── 漂移日志 ──────────────────────────────────────────────────────────
    def _log_drift(self, entry: dict, path: str, updates: dict):
        self.drift_log.append({
            "time":      datetime.now().isoformat(),
            "layer":     "L3",
            "path":      path,
            "direction": entry.get("direction", ""),
            "updates":   updates,
        })
        self.drift_log = self.drift_log[-200:]
        _save_json(self._log_path, self.drift_log)

    # ── 辅助 ─────────────────────────────────────────────────────────────
    def _build_event_detect_prompt(self, convo: str) -> str:
        return (
            f"以下对话对角色的性格和行为模式有没有影响？\n\n"
            f"对话：\n{convo[:500]}\n\n"
            f"如果有影响，输出JSON数组，每条包含：\n"
            f"{{\"direction\": \"影响方向（10-20字）\", "
            f"\"base_weight\": 1到5的数字, "
            f"\"event_type\": \"事件类型\"}}\n\n"
            f"事件类型选项：日常互动/情感表达/冲突或争吵/"
            f"明确的偏好反馈/重大生活事件\n\n"
            f"如果没有值得记录的影响，输出：[]\n"
            f"只输出JSON数组。"
        )

    def _parse_events(self, raw: str) -> list:
        try:
            cleaned = re.sub(r'```(?:json)?|```', '', raw).strip()
            result  = json.loads(cleaned)
            return result if isinstance(result, list) else []
        except:
            return []

    def _deep_update(self, base: dict, updates: dict):
        for k, v in updates.items():
            if k in base and isinstance(base[k], dict) and isinstance(v, dict):
                self._deep_update(base[k], v)
            else:
                base[k] = v

    def _save_pool(self):
        _save_json(self._pool_path, self.pool)

    # ── 外部接口 ──────────────────────────────────────────────────────────
    def get_surface_context(self) -> str:
        surface = _load_json(self._surface_path, {})
        if not surface or all(k.startswith("_") for k in surface):
            return ""
        lines = ["【当前表达倾向（根据这个调整语气和用词）】"]
        nicknames = surface.get("昵称权重", {})
        if nicknames:
            top = sorted(nicknames.items(),
                         key=lambda x: x[1].get("weight", 0) if isinstance(x[1], dict) else x[1],
                         reverse=True)[:3]
            lines.append(f"- 常用称呼：{'、'.join(n for n, _ in top)}")
        expr = surface.get("表达方式权重", {})
        if expr:
            rising  = [k for k, v in expr.items()
                       if isinstance(v, dict) and v.get("trend") == "缓慢上升"]
            if rising:
                lines.append(f"- 正在增加：{'、'.join(rising)}")
        return "\n".join(lines)
