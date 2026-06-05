"""
core/memory.py ── v2
借鉴 Ombre Brain 改造：
  1. 情感双坐标：valence（效价）+ arousal（唤醒度）替代单维度 emotion_weight
  2. 加权多维检索：主题相关性 × 4 + 情感共鸣 × 2 + 时间亲近 × 1.5 + 重要度 × 1
  3. 衰减归档：改进版艾宾浩斯曲线，低分记忆移入 archive，pinned 永不衰减
  4. 相似记忆合并：新记忆与已有记忆相似度超阈值时自动合并
  5. activation_count：每次被检索命中计数，越常被想起衰减越慢
"""

import json
import math
import os
import random
import re
from datetime import datetime, timedelta
from typing import List, Dict, Optional


# ── 极轻量的关键词提取 ────────────────────────────────────────────────────
def extract_keywords(text: str) -> List[str]:
    stopwords = {
        "的","了","是","在","我","你","他","她","们","这","那","有","也",
        "就","都","和","与","但","因为","所以","如果","虽然","然后","可以",
        "没有","一个","一些","什么","怎么","为什么","嗯","哦","啊","呢","吧",
        "告诉","说了","答应"
    }
    words = re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z]{3,}', text)
    keywords = [w for w in words if w not in stopwords]
    seen, result = set(), []
    for w in keywords:
        if w not in seen:
            seen.add(w)
            result.append(w)
    return result[:10]


# ── 情感双坐标（Russell 环形情感模型）────────────────────────────────────
# valence: 0=负面 → 1=正面
# arousal: 0=平静 → 1=激动
EMOTION_COORD_DICT = {
    # 高 arousal 正面
    "超开心": (0.95, 0.85), "好幸福": (0.90, 0.70), "爱你": (0.92, 0.80),
    "想你": (0.85, 0.75), "好喜欢": (0.88, 0.72), "太棒了": (0.90, 0.80),
    "感动": (0.88, 0.65), "感谢": (0.82, 0.55), "惊喜": (0.87, 0.85),
    "醒来我还在": (0.90, 0.60), "安心": (0.80, 0.35),
    # 高 arousal 负面
    "好难受": (0.10, 0.75), "很伤心": (0.12, 0.65), "生气": (0.15, 0.85),
    "讨厌": (0.18, 0.72), "崩溃": (0.08, 0.90), "委屈": (0.15, 0.68),
    "哭了": (0.12, 0.78), "失望": (0.18, 0.55), "心碎": (0.10, 0.70),
    "绝望": (0.05, 0.65),
    # 中强度正面
    "开心": (0.80, 0.55), "喜欢": (0.75, 0.45), "不错": (0.70, 0.35),
    "满意": (0.72, 0.30), "高兴": (0.78, 0.50), "期待": (0.72, 0.65),
    "放心": (0.75, 0.30), "记得": (0.65, 0.35), "承诺": (0.70, 0.45),
    "答应": (0.68, 0.40),
    # 中强度负面
    "难过": (0.25, 0.45), "烦": (0.28, 0.60), "郁闷": (0.25, 0.50),
    "担心": (0.30, 0.58), "无聊": (0.38, 0.22), "累": (0.32, 0.30),
}

def get_emotion_coords(text: str) -> tuple:
    """
    返回 (valence, arousal) 双坐标
    命中词典则返回对应坐标，否则返回中性值
    """
    for word, (v, a) in EMOTION_COORD_DICT.items():
        if word in text:
            # 加一点随机浮动，避免完全相同
            v = max(0.0, min(1.0, v + random.uniform(-0.05, 0.05)))
            a = max(0.0, min(1.0, a + random.uniform(-0.05, 0.05)))
            return round(v, 3), round(a, 3)
    return 0.5, 0.3  # 中性


def get_emotion_weight(text: str) -> float:
    """
    兼容旧接口：把双坐标转成单一权重值
    arousal 高 → 情感权重高（越激动越难忘）
    """
    v, a = get_emotion_coords(text)
    # 偏离中性越远权重越高
    dist = math.sqrt((v - 0.5) ** 2 + (a - 0.3) ** 2)
    return round(min(1.0, dist * 1.5 + 0.15), 3)


# ── 衰减得分计算（改进版艾宾浩斯）────────────────────────────────────────
DECAY_LAMBDA  = 0.04   # 衰减速率（越大越快忘）
DECAY_THRESHOLD = 0.25 # 低于此分数移入归档

def calculate_decay_score(memory: Dict, now: datetime = None) -> float:
    """
    计算一条记忆的当前活跃度得分
    score = importance × (activation_count^0.3) × e^(-λ×days) × emotion_weight
    pinned=True → 永不衰减，返回 999
    resolved=True → 得分 × 0.05（深埋但不删除）
    """
    if memory.get("pinned"):
        return 999.0

    if now is None:
        now = datetime.now()

    importance = memory.get("importance", 5)
    activation_count = max(1.0, float(memory.get("activation_count", 1)))
    arousal    = memory.get("arousal", 0.3)
    valence    = memory.get("valence", 0.5)

    # 情感权重：arousal 越高越难忘
    emotion_weight = 1.0 + arousal * 0.8

    # 距上次激活的天数
    last_active_str = memory.get("last_active", memory.get("time", ""))
    try:
        last_active = datetime.fromisoformat(last_active_str)
        days = max(0.0, (now - last_active).total_seconds() / 86400)
    except:
        days = 30

    # 短期/长期权重分离
    hours = days * 24
    time_weight = 1.0 + math.exp(-hours / 36.0)  # 刚存入×2，36小时半衰

    if days <= 3.0:
        combined = time_weight * 0.7 + emotion_weight * 0.3
    else:
        combined = emotion_weight * 0.7 + time_weight * 0.3

    base_score = (
        importance
        * (activation_count ** 0.3)
        * math.exp(-DECAY_LAMBDA * days)
        * combined
    )

    # resolved 记忆深埋（×0.05），高 arousal 未解决的紧迫度加成（×1.5）
    resolved = memory.get("resolved", False)
    urgency  = 1.5 if (arousal > 0.7 and not resolved) else 1.0
    resolved_factor = 0.05 if resolved else 1.0

    return round(base_score * resolved_factor * urgency, 4)


# ── 相似度计算（轻量，不依赖向量库）─────────────────────────────────────
def keyword_similarity(mem1: Dict, mem2: Dict) -> float:
    """
    基于关键词重叠计算两条记忆的相似度（Jaccard）
    返回 0.0-1.0
    """
    kw1 = set(mem1.get("keywords", []))
    kw2 = set(mem2.get("keywords", []))
    if not kw1 or not kw2:
        return 0.0
    intersection = kw1 & kw2
    union        = kw1 | kw2
    return len(intersection) / len(union) if union else 0.0


# ── 记忆管理核心类 ────────────────────────────────────────────────────────
class MemoryManager:

    MERGE_THRESHOLD = 0.5  # 相似度超过此值则合并

    def __init__(self, data_dir: str, config: dict):
        self.data_dir = data_dir
        self.config   = config

        self.paths = {
            "core":      os.path.join(data_dir, "active", "core_memory.json"),
            "active":    os.path.join(data_dir, "active", "active_memory.json"),
            "archive":   os.path.join(data_dir, "archive", "archive_memory.json"),
            "reminders": os.path.join(data_dir, "active", "reminders.json"),
        }

        for path in self.paths.values():
            os.makedirs(os.path.dirname(path), exist_ok=True)

        self.core      = self._load(self.paths["core"],      default={"user_info": {}, "milestones": []})
        self.active    = self._load(self.paths["active"],    default=[])
        self.archive   = self._load(self.paths["archive"],   default=[])
        self.reminders = self._load(self.paths["reminders"], default=[])

    # ── 基础文件操作 ──────────────────────────────────────────────────────
    def _load(self, path: str, default):
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except:
                return default
        return default

    def _save(self, path: str, data) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def save_all(self):
        self._save(self.paths["core"],      self.core)
        self._save(self.paths["active"],    self.active)
        self._save(self.paths["archive"],   self.archive)
        self._save(self.paths["reminders"], self.reminders)

    # ── 永久核心层操作 ────────────────────────────────────────────────────
    def update_user_info(self, key: str, value: str):
        self.core["user_info"][key] = value
        self.save_all()

    # ── 添加记忆（带相似度合并）─────────────────────────────────────────
    def add_memory(self, content: str, memory_type: str = "对话事件",
                   extra_keywords: List[str] = None,
                   importance: int = 5,
                   pinned: bool = False) -> Dict:
        """
        存入一条新记忆
        - 先检查是否有高度相似的已有记忆，有则合并
        - 带 valence/arousal 双坐标
        - pinned=True 的记忆永不衰减
        """
        keywords = extract_keywords(content)
        if extra_keywords:
            keywords = list(set(keywords + extra_keywords))[:10]

        valence, arousal = get_emotion_coords(content)

        new_mem = {
            "id":               f"mem_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "time":             datetime.now().isoformat(),
            "last_active":      datetime.now().isoformat(),
            "type":             memory_type,
            "content":          content,
            "keywords":         keywords,
            "valence":          valence,
            "arousal":          arousal,
            "emotion_weight":   get_emotion_weight(content),  # 兼容旧代码
            "importance":       importance,
            "activation_count": 1,
            "resolved":         False,
            "pinned":           pinned,
            "layer":            "活跃事件层",
        }

        # 检查是否可以合并
        merged = self._try_merge(new_mem)
        if not merged:
            self.active.append(new_mem)

        self.save_all()
        return new_mem

    def _try_merge(self, new_mem: Dict) -> bool:
        """
        检查新记忆是否与已有记忆高度相似
        相似度 > MERGE_THRESHOLD → 合并内容，更新关键词和情感坐标
        返回是否成功合并
        """
        for existing in self.active:
            sim = keyword_similarity(new_mem, existing)
            if sim >= self.MERGE_THRESHOLD:
                # 合并：把新内容追加到已有记忆，更新元数据
                existing["content"] = existing["content"] + "；" + new_mem["content"]
                existing["content"] = existing["content"][:500]  # 防止无限膨胀

                # 合并关键词
                merged_kw = list(set(existing["keywords"] + new_mem["keywords"]))[:10]
                existing["keywords"] = merged_kw

                # 情感坐标取平均（新内容权重稍高）
                existing["valence"]  = round((existing["valence"] * 0.4 + new_mem["valence"] * 0.6), 3)
                existing["arousal"]  = round((existing["arousal"] * 0.4 + new_mem["arousal"] * 0.6), 3)
                existing["emotion_weight"] = get_emotion_weight(existing["content"])

                # 提升重要度
                existing["importance"]       = min(10, existing.get("importance", 5) + 1)
                existing["activation_count"] = existing.get("activation_count", 1) + 1
                existing["last_active"]      = datetime.now().isoformat()

                print(f"[记忆] 合并相似记忆：{existing['content'][:30]}...")
                return True
        return False

    # ── 检索（加权多维排序）──────────────────────────────────────────────
    def search_memories(self, query: str,
                        top_k: int = 8,
                        time_range_days: Optional[int] = None,
                        query_valence: Optional[float] = None,
                        query_arousal: Optional[float] = None) -> List[Dict]:
        """
        加权多维检索（借鉴 Ombre Brain）：
          主题相关性 × 4.0
          情感共鸣   × 2.0
          时间亲近   × 1.5
          重要度     × 1.0
        """
        query_keywords = set(extract_keywords(query))

        # 自动推断查询的情感坐标
        if query_valence is None or query_arousal is None:
            query_valence, query_arousal = get_emotion_coords(query)

        candidates = list(self.active)

        if time_range_days:
            cutoff     = datetime.now() - timedelta(days=time_range_days)
            candidates = [m for m in candidates
                          if datetime.fromisoformat(m["time"]) >= cutoff]

        W_TOPIC      = 4.0
        W_EMOTION    = 2.0
        W_TIME       = 1.5
        W_IMPORTANCE = 1.0
        WEIGHT_SUM   = W_TOPIC + W_EMOTION + W_TIME + W_IMPORTANCE

        def score(mem: Dict) -> float:
            # 1. 主题相关性（关键词重叠 Jaccard）
            mem_kw      = set(mem.get("keywords", []))
            overlap     = len(query_keywords & mem_kw)
            union_size  = len(query_keywords | mem_kw)
            topic_score = overlap / union_size if union_size > 0 else 0.0

            # 2. 情感共鸣（欧氏距离，越近越高）
            b_v     = mem.get("valence", 0.5)
            b_a     = mem.get("arousal", 0.3)
            dist    = math.sqrt((query_valence - b_v) ** 2 + (query_arousal - b_a) ** 2)
            emotion_score = max(0.0, 1.0 - dist / 1.414)

            # 3. 时间亲近（指数衰减）
            last_active_str = mem.get("last_active", mem.get("time", ""))
            try:
                last_active = datetime.fromisoformat(last_active_str)
                days        = max(0.0, (datetime.now() - last_active).total_seconds() / 86400)
            except:
                days = 30
            time_score = math.exp(-0.02 * days)

            # 4. 重要度（直接归一化）
            importance_score = min(10, mem.get("importance", 5)) / 10.0

            total      = (topic_score * W_TOPIC + emotion_score * W_EMOTION
                          + time_score * W_TIME + importance_score * W_IMPORTANCE)
            normalized = (total / WEIGHT_SUM) * 100

            # resolved 记忆排序降权（但仍可被检索到）
            if mem.get("resolved"):
                normalized *= 0.3

            return normalized

        scored = sorted(candidates, key=score, reverse=True)
        results = scored[:top_k]

        # 更新命中记忆的 activation_count 和 last_active
        hit_ids = {m["id"] for m in results}
        for mem in self.active:
            if mem["id"] in hit_ids:
                mem["activation_count"] = mem.get("activation_count", 1) + 1
                mem["last_active"]      = datetime.now().isoformat()

        return results

    # ── 主动浮现高权重未解决记忆 ─────────────────────────────────────────
    def surface_important_memories(self, top_k: int = 3) -> List[Dict]:
        """
        主动推送：未解决的高 arousal 记忆优先浮现
        在 build_context_string 里调用
        """
        candidates = [m for m in self.active if not m.get("resolved") and not m.get("pinned")]
        if not candidates:
            return []

        def surface_score(mem: Dict) -> float:
            arousal    = mem.get("arousal", 0.3)
            importance = mem.get("importance", 5) / 10.0
            # 距上次激活越久，越需要被想起（最多加0.3分）
            try:
                last_active = datetime.fromisoformat(mem.get("last_active", mem["time"]))
                days        = (datetime.now() - last_active).total_seconds() / 86400
                staleness   = min(0.3, days * 0.01)
            except:
                staleness = 0.0
            return arousal * 0.6 + importance * 0.4 + staleness

        sorted_candidates = sorted(candidates, key=surface_score, reverse=True)
        return sorted_candidates[:top_k]

    # ── 约定系统（兼容旧接口）────────────────────────────────────────────
    def add_reminder(self, content: str, remind_time: Optional[str] = None):
        reminder = {
            "id":          f"rem_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "content":     content,
            "created_at":  datetime.now().isoformat(),
            "remind_time": remind_time,
            "done":        False,
        }
        self.reminders.append(reminder)
        self.save_all()

    def get_pending_reminders(self) -> List[Dict]:
        now     = datetime.now()
        pending = []
        for r in self.reminders:
            if r["done"]:
                continue
            if r["remind_time"] is None:
                continue
            try:
                if now >= datetime.fromisoformat(r["remind_time"]):
                    pending.append(r)
            except:
                pass
        return pending

    # ── 衰减归档（每天运行一次）──────────────────────────────────────────
    def run_daily_archive(self):
        """
        改进版衰减逻辑（借鉴 Ombre Brain）：
        - 计算每条记忆的衰减得分
        - 低于 DECAY_THRESHOLD 的记忆移入 archive（不删除）
        - pinned=True 的记忆永不归档
        - archive 里超过 180 天且 emotion_weight < 0.3 的，才有概率彻底删除
        """
        now = datetime.now()
        still_active = []
        newly_archived = 0

        for mem in self.active:
            if mem.get("pinned"):
                still_active.append(mem)
                continue

            score = calculate_decay_score(mem, now)

            if score < DECAY_THRESHOLD:
                # 移入归档（深埋，不删除）
                mem["layer"]      = "模糊归档层"
                mem["archived_at"] = now.isoformat()
                self.archive.append(mem)
                newly_archived += 1
                print(f"[记忆] 归档：{mem['content'][:30]}... (score={score:.3f})")
            else:
                still_active.append(mem)

        self.active = still_active

        # archive 里极老且情感权重极低的记忆，才有概率彻底删除
        surviving_archive = []
        for mem in self.archive:
            try:
                mem_time = datetime.fromisoformat(mem.get("archived_at", mem["time"]))
                age_days = (now - mem_time).days
            except:
                age_days = 0

            if age_days > 180:
                ew = mem.get("emotion_weight", get_emotion_weight(mem.get("content", "")))
                forget_prob = max(0.0, 1.0 - ew - mem.get("arousal", 0.3))
                if random.random() < forget_prob * 0.3:  # 概率很低，你说的深埋不彻底删
                    print(f"[记忆] 彻底遗忘：{mem.get('content','')[:20]}...")
                    continue
            surviving_archive.append(mem)

        self.archive = surviving_archive
        self.save_all()
        print(f"[记忆] 衰减完成，活跃:{len(self.active)}条，归档:{len(self.archive)}条，新归档:{newly_archived}条")

    # ── 生成注入 LLM 的记忆上下文字符串 ─────────────────────────────────
    def build_context_string(self, query: str) -> str:
        """
        注入 system prompt 的记忆上下文
        1. 永久核心层
        2. 主动浮现：高权重未解决记忆
        3. 相关性检索：和当前话题最相关的记忆
        4. 待提醒约定
        """
        lines = []

        # 永久核心层
        if self.core.get("user_info"):
            info_str = "，".join([f"{k}:{v}" for k, v in self.core["user_info"].items()])
            lines.append(f"[关于用户] {info_str}")

        if self.core.get("milestones"):
            ms_str = "；".join([m.get("brief", "") for m in self.core["milestones"][-3:]])
            if ms_str:
                lines.append(f"[重要节点] {ms_str}")

        # 主动浮现（未解决的高情感记忆）
        surfaced = self.surface_important_memories(top_k=2)
        if surfaced:
            lines.append("[一直放在心上的事]")
            for mem in surfaced:
                lines.append(f"  {mem['content'][:60]}")

        # 相关性检索
        relevant = self.search_memories(query, top_k=6)
        if relevant:
            lines.append("[近期记忆]")
            for mem in relevant:
                date_str = mem["time"][:10]
                lines.append(f"  {date_str} {mem['content'][:80]}")

        # 待提醒约定
        pending = self.get_pending_reminders()
        if pending:
            lines.append("[需要提起的约定]")
            for r in pending:
                lines.append(f"  {r['content']}")

        return "\n".join(lines)
