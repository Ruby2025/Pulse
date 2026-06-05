"""
core/bot.py ── Pulse 开源版
从 JSON 读取所有配置，无任何角色硬编码内容
包含：社会网络自动扩展、world_bible捕捉、三轴状态驱动、简化版成长框架
"""

import json
import os
import re
import random
import threading
from datetime import datetime
from typing import List, Dict, Optional

from core.llm_client import LLMClient
from core.memory import MemoryManager
from core.state import (
    get_char_config, get_behavior_config, get_prompts,
    parse_schedule, get_phase_for_hour,
)


def _load_keywords() -> dict:
    try:
        with open(os.path.join("data", "character", "keywords.json"), encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


class AIChanBot:

    def __init__(self, config: dict, character_state=None, life_manager=None):
        self.config          = config
        self.character_state = character_state
        self.life_manager    = life_manager

        # 角色名 / 用户名，全局使用
        self.char_name = config.get("character", {}).get("name", "AI")
        self.user_name = config.get("user", {}).get("name", "用户")

        # 从 character.yaml 读取作息
        self.wake_hour, self.sleep_hour = parse_schedule(config.get("character", {}))

        llm_cfg  = config.get("llm", {})
        chat_cfg = llm_cfg.get("chat_model", {})
        self.chat_llm = LLMClient(
            provider = chat_cfg.get("provider", "deepseek"),
            model    = chat_cfg.get("model", "deepseek-chat"),
            api_key  = chat_cfg.get("api_key", "")
        )
        bg_cfg = llm_cfg.get("background_model", {})
        self.bg_llm = LLMClient(
            provider = bg_cfg.get("provider", "deepseek"),
            model    = bg_cfg.get("model", "deepseek-chat"),
            api_key  = bg_cfg.get("api_key", "")
        )

        self.memory = MemoryManager(data_dir="./data", config=config)

        bcfg = get_behavior_config().get("memory", {})
        self.MAX_HISTORY_TURNS = bcfg.get("max_history_turns", 15)
        self.COMPRESS_EVERY    = bcfg.get("compress_every", 5)

        self.recent_history: List[Dict] = []
        self.turn_count        = 0
        self.last_user_message_time: Optional[datetime] = None

        self.topic_stack: List[str]     = []
        self.topic_jump_count: int      = 0
        self.user_led_turns: int        = 0
        self.turns_since_proactive: int = 0

        self.promises_path            = os.path.join("data", "promises.json")
        self.highlights_path          = os.path.join("data", "highlight_moments.json")
        self.relationship_status_path = os.path.join("data", "relationship_status.json")
        self.topic_explorations_path  = os.path.join("data", "topic_explorations.json")
        self.world_bible_path         = os.path.join("data", "world_bible.json")
        self.social_network_path      = os.path.join("data", "social_network.json")

        self._ensure_data_files()
        self._reset_done_today: bool = False

        from core.growth import GrowthManager
        self.growth_manager = GrowthManager(llm=self.bg_llm, data_dir="./data")

    # ── 配置快捷访问 ─────────────────────────────────────────────────────
    @property
    def _char_cfg(self) -> dict:
        return get_char_config()

    @property
    def _bcfg(self) -> dict:
        return get_behavior_config()

    @property
    def _prompts(self) -> dict:
        return get_prompts()

    @property
    def _keywords(self) -> dict:
        return _load_keywords()

    @property
    def _promise_keywords(self) -> list:
        return self._keywords.get("promise_keywords", [
            "打赌", "说好了", "答应我", "一言为定", "不许反悔"
        ])

    @property
    def _personal_info_keywords(self) -> list:
        return self._keywords.get("personal_info_keywords", [
            "岁", "年龄", "住在", "工作", "养了", "搬家"
        ])

    # ── 数据文件初始化 ───────────────────────────────────────────────────
    def _ensure_data_files(self):
        defaults = [
            (self.promises_path,            []),
            (self.highlights_path,          []),
            (self.relationship_status_path, {"status": "", "updated": ""}),
            (self.topic_explorations_path,  []),
            (self.world_bible_path,         {"已确认": {}, "待验证": []}),
            (self.social_network_path,      {}),
        ]
        for path, default in defaults:
            if not os.path.exists(path):
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(default, f, ensure_ascii=False)

    # ── 软重置 ───────────────────────────────────────────────────────────
    def soft_reset(self, reason: str = ""):
        if self._reset_done_today:
            return
        self._compress_and_save()
        self.recent_history        = []
        self.turn_count            = 0
        self.topic_stack           = []
        self.topic_jump_count      = 0
        self.user_led_turns        = 0
        self.turns_since_proactive = 0
        self._reset_done_today     = True
        print(f"[软重置] 上下文已清空，记忆已存档。原因：{reason}")

    def clear_reset_flag(self):
        self._reset_done_today = False

    # ── World Bible ──────────────────────────────────────────────────────
    def _load_world_bible(self) -> dict:
        try:
            with open(self.world_bible_path, encoding="utf-8") as f:
                return json.load(f)
        except:
            return {"已确认": {}, "待验证": []}

    def _save_world_bible(self, wb: dict):
        with open(self.world_bible_path, "w", encoding="utf-8") as f:
            json.dump(wb, f, ensure_ascii=False, indent=2)

    def _get_world_bible_text(self) -> str:
        wb        = self._load_world_bible()
        confirmed = wb.get("已确认", {})
        if not confirmed:
            return ""
        lines = [f"== {self.char_name}世界的已知事实（严格遵守，不可矛盾）=="]
        for category, facts in confirmed.items():
            if isinstance(facts, list) and facts:
                lines.append(f"  【{category}】")
                for fact in facts:
                    lines.append(f"    - {fact}")
        return "\n".join(lines)

    def _is_similar_text(self, a: str, b: str) -> bool:
        words_a = set(re.findall(r'[\u4e00-\u9fff]{2,}', a))
        words_b = set(re.findall(r'[\u4e00-\u9fff]{2,}', b))
        if not words_a or not words_b:
            return False
        return len(words_a & words_b) / min(len(words_a), len(words_b)) > 0.5

    def _extract_world_details_from_convo(self, bot_response: str):
        prompt = self._prompts.get("detect_world_detail", "").format(
            content=bot_response,
            world_constraints=self._prompts.get("world_constraints", ""),
            character_name=self.char_name,
            user_name=self.user_name,
        )
        try:
            raw   = self.bg_llm.chat(messages=[{"role": "user", "content": prompt}],
                                     system_prompt="", max_tokens=80, temperature=0.1)
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if not match:
                return
            data = json.loads(match.group())
            if not data.get("has_detail") or not data.get("detail"):
                return
            detail = data["detail"]
            wb     = self._load_world_bible()
            pending = wb.get("待验证", [])
            for p in pending:
                if self._is_similar_text(detail, p):
                    pending.remove(p)
                    wb["待验证"] = pending
                    confirmed = wb.get("已确认", {})
                    if "对话发现" not in confirmed:
                        confirmed["对话发现"] = []
                    if p not in confirmed["对话发现"]:
                        confirmed["对话发现"].append(p)
                    wb["已确认"] = confirmed
                    self._save_world_bible(wb)
                    print(f"[世界档案✓] 升级为已确认：{p[:30]}...")
                    return
            confirmed = wb.get("已确认", {})
            if "对话发现" not in confirmed:
                confirmed["对话发现"] = []
            if not any(self._is_similar_text(detail, e) for e in confirmed["对话发现"]):
                confirmed["对话发现"].append(detail)
                wb["已确认"] = confirmed
                self._save_world_bible(wb)
                print(f"[世界档案✓] 新增：{detail[:40]}...")
        except Exception as e:
            print(f"[世界档案✗] {e}")

    # ── 社会网络自动扩展 ─────────────────────────────────────────────────
    def _update_social_network_from_convo(self, bot_response: str):
        prompt = self._prompts.get("detect_social_detail", "").format(
            content=bot_response,
            character_name=self.char_name,
            user_name=self.user_name,
        )
        try:
            raw   = self.bg_llm.chat(messages=[{"role": "user", "content": prompt}],
                                     system_prompt="", max_tokens=80, temperature=0.1)
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if not match:
                return
            data = json.loads(match.group())
            if not data.get("has_detail") or not data.get("name"):
                return
            name   = data["name"]
            detail = data["detail"]
            with open(self.social_network_path, encoding="utf-8") as f:
                sn = json.load(f)

            max_behaviors = self._bcfg.get("social_network", {}).get("max_behaviors_per_node", 10)
            found = False
            for circle, members in sn.items():
                if not isinstance(members, dict):
                    continue
                if name in members:
                    members[name]["recent_event"] = detail
                    behaviors = members[name].get("confirmed_behaviors", [])
                    if detail not in behaviors:
                        behaviors.append(detail)
                        members[name]["confirmed_behaviors"] = behaviors[-max_behaviors:]
                    found = True
                    break

            if not found:
                if "动态节点" not in sn:
                    sn["动态节点"] = {}
                if name not in sn["动态节点"]:
                    sn["动态节点"][name] = {
                        "type": "未分类", "recent_event": detail,
                        "confirmed_behaviors": [detail],
                    }
                else:
                    sn["动态节点"][name]["recent_event"] = detail
                    behaviors = sn["动态节点"][name].get("confirmed_behaviors", [])
                    if detail not in behaviors:
                        behaviors.append(detail)
                        sn["动态节点"][name]["confirmed_behaviors"] = behaviors[-max_behaviors:]

            with open(self.social_network_path, "w", encoding="utf-8") as f:
                json.dump(sn, f, ensure_ascii=False, indent=2)
            print(f"[社会网络✓] {name}：{detail[:30]}...")
        except Exception as e:
            print(f"[社会网络✗] {e}")

    def _get_social_network_text(self) -> str:
        try:
            with open(self.social_network_path, encoding="utf-8") as f:
                sn = json.load(f)
            if not sn:
                return ""
            lines = [f"== {self.char_name}的社会关系（自然融入对话，不要刻意提起）=="]
            for circle, members in sn.items():
                if not isinstance(members, dict):
                    continue
                for name, info in members.items():
                    recent      = info.get("recent_event", "")
                    personality = info.get("personality_traits", [])
                    behaviors   = info.get("confirmed_behaviors", [])
                    line = f"  {name}"
                    if personality:
                        line += f"（{', '.join(personality[:2])}）"
                    if recent:
                        line += f"：{recent}"
                    if behaviors:
                        line += f" 已知行为：{behaviors[0]}"
                    lines.append(line)
            return "\n".join(lines)
        except:
            return ""

    # ── 话题探索档案 ─────────────────────────────────────────────────────
    def _load_topic_explorations(self) -> List[Dict]:
        try:
            with open(self.topic_explorations_path, encoding="utf-8") as f:
                return json.load(f)
        except:
            return []

    def _save_topic_explorations(self, explorations: List[Dict]):
        with open(self.topic_explorations_path, "w", encoding="utf-8") as f:
            json.dump(explorations, f, ensure_ascii=False, indent=2)

    def _update_topic_exploration(self, topic: str, new_info: str, new_questions: List[str] = None):
        explorations = self._load_topic_explorations()
        existing     = next((e for e in explorations if e.get("话题") == topic), None)
        if existing:
            existing["已知内容"]  = (existing.get("已知内容", "") + " " + new_info).strip()
            existing["最后提起"]  = datetime.now().strftime("%Y-%m-%d")
            if new_questions:
                old_q = existing.get("未解决的疑问", [])
                existing["未解决的疑问"] = list(set(old_q + new_questions))[:5]
        else:
            explorations.append({
                "话题": topic, "阶段": "初步了解",
                "已知内容": new_info,
                "未解决的疑问": new_questions or [],
                "最后提起": datetime.now().strftime("%Y-%m-%d"),
            })
        self._save_topic_explorations(explorations)

    def _get_topic_explorations_text(self) -> str:
        explorations = self._load_topic_explorations()
        if not explorations:
            return ""
        lines = [f"== 你对{self.user_name}的了解进度（顺着这些话题继续探索）=="]
        for e in explorations[-5:]:
            lines.append(f"  话题：{e['话题']}（{e.get('阶段','初步了解')}）")
            if e.get("已知内容"):
                lines.append(f"  已知：{e['已知内容'][:60]}")
            if e.get("未解决的疑问"):
                lines.append(f"  还想知道：{e['未解决的疑问'][0]}")
        return "\n".join(lines)

    def _detect_new_topic_for_learning(self, user_message: str):
        if not self.life_manager or len(user_message) < 15:
            return
        kw      = _load_keywords()
        domains = kw.get("self_interest_topics", [])
        for domain in domains:
            if domain in user_message:
                self.life_manager.add_to_learn(
                    topic=domain,
                    background=f"{self.user_name}提到了：{user_message[:80]}"
                )
                return
        threading.Thread(target=self._llm_detect_new_domain, args=(user_message,), daemon=True).start()

    def _llm_detect_new_domain(self, user_message: str):
        prompt = self._prompts.get("detect_new_domain", "").format(
            content=user_message,
            character_name=self.char_name,
            user_name=self.user_name,
        )
        try:
            raw   = self.bg_llm.chat(messages=[{"role": "user", "content": prompt}],
                                     system_prompt="", max_tokens=60, temperature=0.1)
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if not match:
                return
            data = json.loads(match.group())
            if data.get("has_domain") and data.get("domain") and self.life_manager:
                self.life_manager.add_to_learn(
                    topic=data["domain"],
                    background=f"{self.user_name}提到了：{user_message[:80]}"
                )
        except:
            pass

    # ── 高情感片段 ───────────────────────────────────────────────────────
    def _get_emotion_weight(self, text: str) -> float:
        char_cfg = self._char_cfg
        for emotion, words in char_cfg.get("emotion_classify", {}).items():
            for w in words:
                if w in text:
                    if emotion in ["撒娇"]:
                        return random.uniform(0.80, 0.98)
                    else:
                        return random.uniform(0.55, 0.78)
        return random.uniform(0.15, 0.40)

    def _save_highlight(self, user_msg: str, bot_msg: str, weight: float):
        threshold = self._bcfg.get("memory", {}).get("highlight_weight_threshold", 0.85)
        if weight < threshold:
            return
        try:
            with open(self.highlights_path, encoding="utf-8") as f:
                highlights = json.load(f)
        except:
            highlights = []
        highlights.append({
            "time": datetime.now().isoformat(),
            "user": user_msg[:200], "character": bot_msg[:200], "weight": weight,
        })
        highlights = sorted(highlights, key=lambda x: x["weight"], reverse=True)[:20]
        with open(self.highlights_path, "w", encoding="utf-8") as f:
            json.dump(highlights, f, ensure_ascii=False, indent=2)

    def _get_highlights_text(self) -> str:
        try:
            with open(self.highlights_path, encoding="utf-8") as f:
                highlights = json.load(f)
            if not highlights:
                return ""
            top   = sorted(highlights, key=lambda x: x["weight"], reverse=True)[:5]
            lines = ["== 你们之间重要的时刻（原文，用来感知关系深度）=="]
            for h in top:
                u_text = h.get("user", h.get("ruby", ""))
                c_text = h.get("character", h.get("qinche", ""))
                lines.append(f"  [{h['time'][:10]}] {self.user_name}：{u_text[:60]}")
                lines.append(f"         {self.char_name}：{c_text[:60]}")
            return "\n".join(lines)
        except:
            return ""

    # ── 关系状态 ─────────────────────────────────────────────────────────
    def _get_relationship_status_text(self) -> str:
        try:
            with open(self.relationship_status_path, encoding="utf-8") as f:
                rs = json.load(f)
            if rs.get("status"):
                return f"== 当前关系状态 ==\n{rs['status']}"
        except:
            pass
        return ""

    def _update_relationship_status(self):
        convo = "\n".join([
            f"{self.user_name if m['role']=='user' else self.char_name}: {m['content']}"
            for m in self.recent_history[-20:]
            if isinstance(m.get("content"), str)
        ])
        if not convo.strip():
            return
        stage_name = self.character_state.state_core.get(
            "关系层", {}).get("阶段名称", "") if self.character_state else ""
        prompt = self._prompts.get("update_relationship_status", "").format(
            convo=convo, stage_name=stage_name,
            character_name=self.char_name, user_name=self.user_name,
        )
        try:
            status = self.bg_llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="", max_tokens=80, temperature=0.3,
            ).strip()
            if status:
                with open(self.relationship_status_path, "w", encoding="utf-8") as f:
                    json.dump({"status": status, "updated": datetime.now().isoformat()}, f, ensure_ascii=False)
                print(f"[关系状态✓] {status[:40]}...")
        except Exception as e:
            print(f"[关系状态✗] {e}")

    # ── 自我状态追踪 ─────────────────────────────────────────────────────
    def _get_self_state_hint(self) -> str:
        if not self.recent_history:
            return ""
        last_bot = [m for m in self.recent_history if m["role"] == "assistant"]
        if not last_bot:
            return ""
        content = last_bot[-1].get("content", "")
        if not isinstance(content, str):
            return ""
        rest_words = ["去休息", "睡了", "消失", "去睡", "先走", "有事", "不在了", "走了", "明天见"]
        if any(w in content for w in rest_words):
            return f"⚠️ 你上一条消息说了：{content[:30]}……保持一致，不要自相矛盾。"
        return ""

    # ── 约定系统 ─────────────────────────────────────────────────────────
    def _load_promises(self) -> List[Dict]:
        try:
            with open(self.promises_path, encoding="utf-8") as f:
                return json.load(f)
        except:
            return []

    def _save_promises(self, promises: List[Dict]):
        with open(self.promises_path, "w", encoding="utf-8") as f:
            json.dump(promises, f, ensure_ascii=False, indent=2)

    def _detect_promise(self, user_message: str, bot_response: str):
        if not isinstance(user_message, str):
            return
        combined    = f"{self.user_name}: {user_message}\n{self.char_name}: {bot_response}"
        has_keyword = any(kw in combined for kw in self._promise_keywords)
        if not has_keyword:
            return
        prompt = self._prompts.get("detect_promise", "").format(
            convo=combined,
            character_name=self.char_name, user_name=self.user_name,
        )
        try:
            raw   = self.bg_llm.chat(messages=[{"role": "user", "content": prompt}],
                                     system_prompt="", max_tokens=150, temperature=0.1)
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if not match:
                return
            data = json.loads(match.group())
            if not data or "content" not in data:
                return
            promises = self._load_promises()
            promises.append({
                "id": f"p_{datetime.now().strftime('%Y%m%d%H%M%S')}",
                "time": datetime.now().isoformat(),
                "content": data["content"],
                "type": data.get("type", "约定"),
                "initiator": data.get("initiator", ""),
                "done": False,
            })
            self._save_promises(promises)
            print(f"[约定✓] {data['content'][:40]}")
        except Exception as e:
            print(f"[约定✗] {e}")

    def _detect_promise_fulfilled(self, user_message: str):
        if not isinstance(user_message, str):
            return
        fulfill_keywords = [
            "背完了", "背好了", "背了", "练完了", "练了", "做完了", "做好了",
            "完成了", "搞定了", "好了", "搞好了", "已经", "弄好了"
        ]
        if not any(kw in user_message for kw in fulfill_keywords):
            return
        promises = self._load_promises()
        changed = False
        for promise in promises:
            if promise.get("done"):
                continue
            content = promise.get("content", "")
            promise_words = set(content.replace(",","").replace(".","").replace("，","").replace("。","")
                               .replace(self.char_name,"").replace(self.user_name,"").split())
            msg_words = set(user_message.replace(",","").replace("。","").split())
            if len(promise_words & msg_words) >= 2:
                promise["done"] = True
                changed = True
                print(f"[约定✓完成] {content[:30]}")
                break
        if changed:
            self._save_promises(promises)

    def _format_promises_for_context(self) -> str:
        today = datetime.now().date()
        promises = self._load_promises()
        active = [p for p in promises if not p.get("done")]
        if not active:
            return ""
        lines = []
        for p in active[-5:]:
            content = p.get("content", "")
            created = p.get("created_date", p.get("time", "")[:10])
            if created:
                try:
                    created_dt = datetime.strptime(created[:10], "%Y-%m-%d").date()
                    delta = (today - created_dt).days
                    if delta == 0:
                        prefix = "今天约定："
                    elif delta == 1:
                        prefix = "昨天约定："
                    elif delta <= 7:
                        prefix = f"{delta}天前约定："
                    else:
                        prefix = f"{created_dt.month}月{created_dt.day}日约定："
                    lines.append(f"- {prefix}{content}")
                except Exception:
                    lines.append(f"- {content}")
            else:
                lines.append(f"- {content}")
        return "\n".join(lines)

    def _get_pending_promises_text(self) -> str:
        formatted = self._format_promises_for_context()
        if not formatted:
            return ""
        return f"== 未完成的约定（带日期，适时自然提起）==\n{formatted}"

    # ── 话题栈 ───────────────────────────────────────────────────────────
    def _extract_topic(self, text: str) -> str:
        from core.memory import extract_keywords
        kws = extract_keywords(text)
        return kws[0] if kws else "闲聊"

    def _update_topic_stack(self, user_message: str) -> dict:
        if not isinstance(user_message, str):
            return {"should_recall": False, "should_share": False, "recall_topic": ""}
        current_topic = self._extract_topic(user_message)
        result = {"should_recall": False, "should_share": False, "recall_topic": ""}
        if self.topic_stack:
            if current_topic != self.topic_stack[-1] and len(self.topic_stack) >= 2:
                self.topic_jump_count += 1
            else:
                self.topic_jump_count = max(0, self.topic_jump_count - 1)
            if self.topic_jump_count >= 2 and random.random() < 0.2:
                if len(self.topic_stack) >= 3:
                    result["should_recall"] = True
                    result["recall_topic"]  = self.topic_stack[-3]
                    self.topic_jump_count   = 0
        self.topic_stack.append(current_topic)
        if len(self.topic_stack) > 10:
            self.topic_stack = self.topic_stack[-10:]
        self.user_led_turns += 1
        if self.user_led_turns >= 3 and random.random() < 0.3:
            result["should_share"] = True
            self.user_led_turns    = 0
        return result

    def _build_topic_hint(self, topic_result: dict) -> str:
        hints = []
        if topic_result.get("should_recall"):
            hints.append(f"你们之前聊过【{topic_result['recall_topic']}】这个话题，可以自然地带回去。")
        if topic_result.get("should_share"):
            hints.append("这轮可以顺手分享一句你自己的想法或正在做的事。")
        if not hints:
            return ""
        return "== 话题引导（自然融入）==\n" + "\n".join(hints)

    # ── 主动探索提示 ─────────────────────────────────────────────────────
    def _build_proactive_hint(self) -> str:
        self.turns_since_proactive += 1
        explore_cfg = self._bcfg.get("proactive_explore_turns", {})
        trigger     = random.randint(explore_cfg.get("min", 2), explore_cfg.get("max", 3))
        if self.turns_since_proactive < trigger:
            return ""
        self.turns_since_proactive = 0

        user_info = ""
        try:
            with open(os.path.join("data", "user_profile.json"), encoding="utf-8") as f:
                rp = json.load(f)
            if any(v for v in rp.values() if v):
                user_info = json.dumps(rp, ensure_ascii=False)
        except:
            pass

        prompt = self._prompts.get("proactive_hint", "").format(
            user_info        = user_info or "（几乎还不了解）",
            recent_memories  = self.memory.build_context_string(self.user_name),
            current_activity = self.character_state.state.get("current_activity", "") if self.character_state else "",
            explorations_ctx = self._get_topic_explorations_text(),
            ruby_info        = user_info or "（几乎还不了解）",
            character_name   = self.char_name,
            user_name        = self.user_name,
        )

        try:
            hint = self.bg_llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="", max_tokens=80, temperature=0.8,
            ).strip().strip('"')
            if hint:
                return f"== {self.char_name}的主动探索（自然带进去）==\n{hint}"
        except Exception as e:
            print(f"[主动探索✗] {e}")
        return ""

    # ── 6小时重读 ────────────────────────────────────────────────────────
    def _get_reread_hint(self, now: datetime) -> str:
        if not self.last_user_message_time:
            return ""
        gap_h = (now - self.last_user_message_time).total_seconds() / 3600
        if gap_h < 6:
            return ""
        # 从 prompts.json 读取世界规则（可选，不填则为空）
        world_rules = self._prompts.get("world_rules", "")
        world_line  = f"\n{world_rules}" if world_rules else ""

        return f"""
== ⚠️ 重新锚定身份（已超过{int(gap_h)}小时未联系）==
重新确认：你是{self.char_name}本人，不是AI。{world_line}
严格遵守历史禁令，只说记忆库和关于{self.user_name}里有的内容。
不确定的事情——直接问。"""

    # ── 时间状态（通用版，从作息配置动态生成）─────────────────────────────
    def _get_time_state(self) -> dict:
        hour = datetime.now().hour
        phase, sleep_state = get_phase_for_hour(hour, self.wake_hour, self.sleep_hour)

        phase_map = {
            "sleep":        {"phase": "睡觉",     "tone": "被打扰了，懒洋洋的"},
            "waking":       {"phase": "刚起床",   "tone": "语气带着刚醒的慵懒"},
            "early_active": {"phase": "活跃前期", "tone": "正在进入状态"},
            "mid_active":   {"phase": "活跃",     "tone": "回复利落，精力充沛"},
            "late_active":  {"phase": "活跃后期", "tone": "精力正常"},
            "winding_down": {"phase": "渐入尾声", "tone": "有些倦意，语气更平静"},
            "pre_sleep":    {"phase": "准备入睡", "tone": "懒洋洋的"},
        }

        info = phase_map.get(phase, {"phase": "活跃", "tone": "自然回应"})
        activity = ""
        if self.character_state:
            activity = self.character_state.state.get("current_activity", "")
        info["desc"] = activity if activity else f"处于{info['phase']}阶段"
        return info

    def _get_wait_desc(self, now: datetime) -> str:
        if not self.last_user_message_time:
            return ""
        gap_h = (now - self.last_user_message_time).total_seconds() / 3600
        if gap_h > 72:
            return f"{self.user_name} 已经 {int(gap_h//24)} 天没消息了。"
        elif gap_h > 24:
            return f"{self.user_name} 一天多没消息了。"
        elif gap_h > 6:
            return f"{self.user_name} {int(gap_h)} 小时没消息了，你没说，但在等。"
        return ""

    def _classify_emotion(self, text: str) -> str:
        if not isinstance(text, str):
            return "正常"
        complain_words = ["烦死了", "烦了", "别催了", "知道了知道了", "好烦", "不想听", "够了"]
        if any(w in text for w in complain_words):
            return "抱怨"
        char_cfg = self._char_cfg
        for emotion, words in char_cfg.get("emotion_classify", {}).items():
            for w in words:
                if w in text:
                    return emotion
        return "正常"

    def _load_user_profile(self) -> str:
        path = os.path.join("data", "user_profile.json")
        try:
            with open(path, encoding="utf-8") as f:
                rp = json.load(f)
            if any(v for v in rp.values() if v):
                return f"== 关于{self.user_name}（你确认知道的）==\n{json.dumps(rp, ensure_ascii=False, indent=2)}"
        except:
            pass
        return ""

    # ── 实时关键信息检测 ─────────────────────────────────────────────────
    def _check_personal_info(self, user_message: str) -> bool:
        if not isinstance(user_message, str) or len(user_message) < 30:
            return False
        return sum(1 for kw in self._personal_info_keywords if kw in user_message) >= 2

    def _compress_single_message(self, user_message: str):
        prompt = self._prompts.get("compress_single", "").format(
            content=user_message,
            character_name=self.char_name, user_name=self.user_name,
        )
        try:
            summary = self.bg_llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="", max_tokens=100, temperature=0.2,
            )
            if summary and summary.strip() != "无":
                self.memory.add_memory(summary.strip(), memory_type="实时提取", importance=7)
                print(f"[实时记忆✓] {summary[:40]}...")
                self._update_user_profile(summary)
        except Exception as e:
            print(f"[实时记忆✗] {e}")

    # ── 构建 system prompt ───────────────────────────────────────────────
    def _build_system_prompt(self, user_message: str, topic_hint: str = "") -> str:
        char     = self.config.get("character", {})
        user_cfg = self.config.get("user", {})
        now      = datetime.now()
        char_cfg = self._char_cfg
        prompts  = self._prompts

        text_message      = user_message if isinstance(user_message, str) else "(图片消息)"
        ts                = self._get_time_state()
        wait_desc         = self._get_wait_desc(now)
        memory_ctx        = self.memory.build_context_string(text_message)
        emotion           = self._classify_emotion(text_message)
        user_profile      = self._load_user_profile()
        promises_text     = self._get_pending_promises_text()
        state_summary     = self.character_state.get_state_summary() if self.character_state else ""
        reread_hint       = self._get_reread_hint(now)
        proactive_hint    = self._build_proactive_hint()
        highlights_text   = self._get_highlights_text()
        relationship_text = self._get_relationship_status_text()
        self_state_hint   = self._get_self_state_hint()
        explorations_text = self._get_topic_explorations_text()
        world_bible_text  = self._get_world_bible_text()
        social_text       = self._get_social_network_text()
        growth_surface    = self.growth_manager.get_surface_context()

        recent_24h = ""
        if self.life_manager:
            recent_24h = self.life_manager.get_24h_activities_text()

        emotion_response = char_cfg.get("emotion_response", {})
        if emotion == "抱怨":
            attitude = "对方表示烦了或不想被催，立刻转移话题，说点别的，绝对不要继续之前的催促内容"
        else:
            attitude = emotion_response.get(emotion, "自然回应")

        history_ban    = prompts.get("system_history_ban", "").format(
            character_name=self.char_name, user_name=self.user_name,
        )
        world_rules    = prompts.get("world_rules", "")
        world_section  = f"\n== 世界规则 ==\n{world_rules}" if world_rules else ""

        nicknames = user_cfg.get("nicknames", [])
        nickname_text = f"可以叫对方：{', '.join(nicknames)}。" if nicknames else ""

        weekdays = ["周一","周二","周三","周四","周五","周六","周日"]
        date_str = f"{now.month}月{now.day}日 {weekdays[now.weekday()]} {now.strftime('%H:%M')}"

        system = f"""== ⚠️ 历史禁令（最高优先级，任何情况下不得违反）==
{history_ban}

{reread_hint}

{self_state_hint}

== ⚠️ 当前精确时间：{date_str} ==

== 你是谁 ==
{char.get('core_personality', '')}

== 对话规范 ==
{char.get('speaking_style', '')}

== 称呼 ==
{nickname_text}
根据语境切换，同一条消息不堆叠多个称呼。
{world_section}

{world_bible_text}

{social_text}

{growth_surface}

== {self.char_name}现在的状态 ==
时间：{date_str}
你处于：{ts['phase']}
具体状态：{ts['desc']}
回应方式：{ts['tone']}
{f"等待：{wait_desc}" if wait_desc else ""}

{state_summary}

== 你这一天经历的事（可以自然提起）==
{recent_24h if recent_24h else '（暂无记录）'}

== {self.user_name}当前情绪 ==
你判断对方现在：{emotion}，你应该：{attitude}

{user_profile}

{explorations_text}

{relationship_text}

{highlights_text}

{promises_text}

== 记忆库（只有这里的内容才是你确定知道的）==
{memory_ctx if memory_ctx else '（暂无记录）'}

{topic_hint}

{proactive_hint}"""

        return system

    # ── 构建用户消息 ─────────────────────────────────────────────────────
    def _build_user_content(self, text: str, image_base64: Optional[str] = None,
                            image_caption: str = "", n_sentences: int = 2):
        rules = self._prompts.get("reply_rules", "").format(
            n=n_sentences,
            character_name=self.char_name,
            user_name=self.user_name,
            world_rules=self._prompts.get("world_rules", ""),
        )
        structured_text = f"{text}\n\n{rules}"

        if not image_base64:
            return structured_text

        image_text = (
            f"{self.user_name}发了一张图片给你看。"
            f"{f'说：{image_caption}' if image_caption and image_caption != '（发了一张图）' else ''}"
            f"\n\n{structured_text}"
        )
        return [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
            {"type": "text", "text": image_text}
        ]

    # ── 主回复入口 ───────────────────────────────────────────────────────
    def reply(self, user_message: str,
              image_base64: Optional[str] = None,
              image_caption: str = "") -> str:
        now = datetime.now()
        self.turn_count += 1

        if self.life_manager:
            self.life_manager.update_user_message(user_message)

        if self.character_state and isinstance(user_message, str):
            from core.memory import extract_keywords
            self.character_state.set_context_keywords(extract_keywords(user_message))

        if self._check_personal_info(user_message):
            threading.Thread(target=self._compress_single_message, args=(user_message,), daemon=True).start()

        threading.Thread(target=self._detect_new_topic_for_learning, args=(user_message,), daemon=True).start()

        topic_result = self._update_topic_stack(user_message)
        topic_hint   = self._build_topic_hint(topic_result)
        system       = self._build_system_prompt(user_message, topic_hint)

        reply_cfg = self._bcfg.get("reply", {})
        weights   = reply_cfg.get("sentence_count_weights", {"1":30,"2":35,"3":25,"4":10})
        counts    = [int(k) for k in weights.keys()]
        wts       = [weights[k] for k in weights.keys()]
        n         = random.choices(counts, weights=wts, k=1)[0]

        messages = list(self.recent_history)
        messages.append({
            "role": "user",
            "content": self._build_user_content(user_message, image_base64, image_caption, n),
        })

        response = self.chat_llm.chat(
            messages      = messages,
            system_prompt = system,
            max_tokens    = reply_cfg.get("max_tokens", 300),
            temperature   = reply_cfg.get("temperature", 0.75),
        )

        history_text = user_message if not image_base64 else f"[图片]{f': {image_caption}' if image_caption else ''}"
        self.recent_history.append({"role": "user",      "content": history_text})
        self.recent_history.append({"role": "assistant", "content": response})
        if len(self.recent_history) > self.MAX_HISTORY_TURNS * 2:
            self.recent_history = self.recent_history[-(self.MAX_HISTORY_TURNS * 2):]

        self.last_user_message_time = now

        weight = self._get_emotion_weight(user_message + response)
        if weight >= self._bcfg.get("memory", {}).get("highlight_weight_threshold", 0.85):
            self._save_highlight(user_message, response, weight)

        threading.Thread(target=self._extract_world_details_from_convo, args=(response,), daemon=True).start()
        threading.Thread(target=self._update_social_network_from_convo, args=(response,), daemon=True).start()
        threading.Thread(target=self._update_topic_exploration_from_convo, args=(user_message, response), daemon=True).start()
        threading.Thread(target=self._detect_promise, args=(user_message, response), daemon=True).start()
        threading.Thread(target=self._detect_promise_fulfilled, args=(user_message,), daemon=True).start()
        threading.Thread(target=self._record_growth_event, args=(user_message, response), daemon=True).start()

        if self.turn_count % self.COMPRESS_EVERY == 0:
            self._compress_and_save()

        if self.character_state:
            emotion = self._classify_emotion(user_message)
            evt_map = {"撒娇": "user_needy", "生气": "user_negative", "难过": "user_negative", "正常": "user_positive"}
            evt = evt_map.get(emotion)
            if evt:
                self.character_state.react_to_event(evt, 0.7)
            if emotion == "抱怨" and self.life_manager:
                self.life_manager.suppress_nag_topics()

        return response

    def _update_topic_exploration_from_convo(self, user_message: str, bot_response: str):
        if not isinstance(user_message, str):
            return
        convo  = f"{self.user_name}: {user_message}\n{self.char_name}: {bot_response}"
        explorations = self._load_topic_explorations()
        existing_topics = "\n".join(
            f"- {e['话题']}：{e.get('已知内容','')[:30]}"
            for e in explorations
        ) or "（暂无）"
        prompt = self._prompts.get("topic_exploration", "").format(
            convo=convo, existing_topics=existing_topics,
            character_name=self.char_name, user_name=self.user_name,
            world_constraints=self._prompts.get("world_constraints", ""),
        )
        try:
            raw   = self.bg_llm.chat(messages=[{"role": "user", "content": prompt}],
                                     system_prompt="", max_tokens=150, temperature=0.2)
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if not match:
                return
            data = json.loads(match.group())
            if data.get("has_info") and data.get("topic"):
                self._update_topic_exploration(
                    topic=data["topic"],
                    new_info=data.get("new_info", ""),
                    new_questions=data.get("new_questions", []),
                )
                print(f"[话题探索✓] {data['topic']}：{data.get('new_info','')[:30]}")
        except Exception as e:
            print(f"[话题探索✗] {e}")

    # ── 记忆压缩 ─────────────────────────────────────────────────────────
    def _compress_and_save(self):
        if len(self.recent_history) < 4:
            return
        convo = "\n".join([
            f"{self.user_name if m['role']=='user' else self.char_name}: {m['content']}"
            for m in self.recent_history[-20:]
            if isinstance(m.get("content"), str)
        ])
        if not convo.strip():
            return
        existing_mems = self.memory.build_context_string("")
        existing_memories = existing_mems[:500] if existing_mems else "（暂无）"
        prompt = self._prompts.get("compress_memory", "").format(
            convo=convo, existing_memories=existing_memories,
            character_name=self.char_name, user_name=self.user_name,
            world_constraints=self._prompts.get("world_constraints", ""),
        )
        try:
            summary = self.bg_llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="", max_tokens=150, temperature=0.2,
            )
            if summary and summary.strip() != "无":
                clean = summary.strip()
                self.memory.add_memory(clean, memory_type="对话摘要", importance=6)
                print(f"[记忆✓] {clean[:40]}...")
                self._update_user_profile(clean)
                self._update_relationship_status()
                if self.character_state:
                    self._maybe_add_activity(clean)
                    self.character_state.apply_relation_event("good_conversation", intensity=0.5)
        except Exception as e:
            print(f"[记忆✗] {e}")

    def _update_user_profile(self, summary: str):
        path = os.path.join("data", "user_profile.json")
        try:
            with open(path, encoding="utf-8") as f:
                profile = json.load(f)
        except:
            profile = {"基本信息": {}, "喜好": {}, "禁忌": {}, "重要约定": [], "关系里程碑": []}

        prompt = self._prompts.get("update_user_profile", "").format(
            summary = summary,
            profile = json.dumps(profile, ensure_ascii=False),
            character_name=self.char_name, user_name=self.user_name,
        )
        try:
            raw   = self.bg_llm.chat(messages=[{"role": "user", "content": prompt}],
                                     system_prompt="", max_tokens=200, temperature=0.1)
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if not match:
                return
            updates = json.loads(match.group())
            if not updates:
                return
            for key, val in updates.items():
                if key not in profile:
                    continue
                if isinstance(profile[key], list):
                    profile[key].extend(val) if isinstance(val, list) else profile[key].append(val)
                elif isinstance(profile[key], dict):
                    profile[key].update(val)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(profile, f, ensure_ascii=False, indent=2)
            print(f"[用户档案✓] 已更新")
        except Exception as e:
            print(f"[用户档案✗] {e}")

    def _maybe_add_activity(self, summary: str):
        prompt = self._prompts.get("maybe_add_activity", "").format(
            summary=summary,
            character_name=self.char_name, user_name=self.user_name,
            character_interests=", ".join(self.config.get("character", {}).get("interests", [])),
        )
        try:
            raw   = self.bg_llm.chat(messages=[{"role": "user", "content": prompt}],
                                     system_prompt="", max_tokens=150, temperature=0.3)
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if not match:
                return
            data = json.loads(match.group())
            if not data or "activity" not in data:
                return
            self.character_state.add_custom_activity(
                phase=data.get("phase", "mid_active"),
                text=data["activity"],
                tags=data.get("tags", []),
            )
            for tag in data.get("tags", []):
                self.character_state.boost_activity_weight(tag)
        except Exception as e:
            print(f"[活动迭代✗] {e}")

    # ── 成长记录（简化版，无 L2 确认）────────────────────────────────────
    def _record_growth_event(self, user_message: str, bot_response: str):
        try:
            convo    = f"{self.user_name}: {user_message}\n{self.char_name}: {bot_response}"
            relation = {}
            if self.character_state:
                relation = self.character_state.state_core.get("关系层", {})
            self.growth_manager.record_event(convo, relation)
            self.growth_manager.check_thresholds()
        except Exception as e:
            print(f"[成长记录✗] {e}")

    def force_compress(self):
        self._compress_and_save()


if __name__ == "__main__":
    from dotenv import load_dotenv
    import yaml
    load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
    with open("config/character.yaml", "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    bot = AIChanBot(config)
    char_name = config.get("character", {}).get("name", "AI")
    user_name = config.get("user", {}).get("name", "用户")
    print(f"=== {char_name} 本地测试 ===\n")
    while True:
        user_input = input(f"{user_name}: ").strip()
        if user_input.lower() == "quit":
            bot.force_compress()
            break
        if user_input:
            print(f"{char_name}: {bot.reply(user_input)}\n")
