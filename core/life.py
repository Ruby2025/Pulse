"""
core/life.py ── Pulse 开源版
从 JSON 读取所有配置，无任何角色硬编码内容
包含：生活循环、主动消息、思念调度、学习链路、世界扩充、社会网络、话题抑制
"""

import json
import os
import re
import random
import threading
from datetime import datetime, date, timedelta
from typing import Optional, Callable, List

from core.state import (
    get_behavior_config, get_prompts,
    parse_schedule, get_phase_for_hour,
)


def _load_keywords(data_dir: str) -> dict:
    try:
        with open(os.path.join(data_dir, "character", "keywords.json"), encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


class LifeManager:

    def __init__(self, data_dir: str, llm_client, send_func: Callable[[str], None],
                 config: dict = None):
        self.data_dir  = data_dir
        self.llm       = llm_client
        self.send_func = send_func
        self.config    = config or {}

        # 角色名 / 用户名
        self.char_name = self.config.get("character", {}).get("name", "AI")
        self.user_name = self.config.get("user", {}).get("name", "用户")

        # 从 character.yaml 读取作息
        self.wake_hour, self.sleep_hour = parse_schedule(self.config.get("character", {}))

        self.life_log_path         = os.path.join(data_dir, "life_log.json")
        self.alarms_path           = os.path.join(data_dir, "alarms.json")
        self.attention_mode_path   = os.path.join(data_dir, "attention_mode.json")
        self.longing_schedule_path = os.path.join(data_dir, "longing_schedule.json")
        self.learning_notes_path   = os.path.join(data_dir, "learning_notes.json")
        self.pending_learn_path    = os.path.join(data_dir, "pending_learn.json")
        self.world_bible_path      = os.path.join(data_dir, "world_bible.json")
        self.social_network_path   = os.path.join(data_dir, "social_network.json")
        self.keywords_path         = os.path.join(data_dir, "character", "keywords.json")
        os.makedirs(data_dir, exist_ok=True)

        self.life_log: list = self._load(self.life_log_path, [])
        self.last_proactive_ts: Optional[datetime] = self._last_proactive_from_log()
        self.last_user_message_ts: Optional[datetime] = None
        self.last_user_message_content: str = "（还没发过消息）"

        self.bot        = None
        self._was_awake = True
        self._daydream_timer: Optional[threading.Timer] = None

        # 用户睡眠/忙碌状态追踪
        self._user_sleeping: bool = False
        self._user_sleep_ts: Optional[datetime] = None

        # 话题重复抑制
        self._recent_topics: list = []
        self._topic_suppress: dict = {}

        self._keywords = _load_keywords(data_dir)
        self._ensure_longing_schedule()

    # ── 配置快捷访问 ─────────────────────────────────────────────────────
    @property
    def _bcfg(self) -> dict:
        return get_behavior_config()

    @property
    def _prompts(self) -> dict:
        return get_prompts()

    @property
    def _high_openness_keywords(self) -> list:
        return self._keywords.get("high_openness_keywords", [
            "朋友", "出门", "聚会", "工作", "项目"
        ])

    @property
    def _self_interest_topics(self) -> list:
        kw = _load_keywords(self.data_dir)
        return kw.get("self_interest_topics", [])

    # ── 基础工具 ─────────────────────────────────────────────────────────
    def _load(self, path: str, default):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except:
            return default

    def _save(self, path: str, data):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[Life] 保存失败 {path}: {e}")

    def _save_log(self):
        self._save(self.life_log_path, self.life_log)

    def _last_proactive_from_log(self) -> Optional[datetime]:
        for entry in reversed(self.life_log):
            if entry.get("sent_message"):
                try:
                    return datetime.fromisoformat(entry["ts"])
                except:
                    pass
        return None

    def update_user_message(self, content: str):
        self.last_user_message_ts      = datetime.now()
        self.last_user_message_content = content[:100]
        self._user_sleeping = False
        self._user_sleep_ts = None

    def _detect_user_sleeping(self, content: str):
        """检测用户是否说了去睡觉或去忙"""
        away_keywords = [
            "睡了", "去睡", "睡觉", "晚安", "好梦", "睡着", "不在了", "拜拜睡",
            "去忙", "忙去了", "要忙", "有事", "等下要去", "我要去", "去做",
            "先去了", "先忙", "一会儿回来", "等下回来", "稍后", "不在线"
        ]
        return any(kw in content for kw in away_keywords)

    def is_awake(self, now: Optional[datetime] = None) -> bool:
        hour = (now or datetime.now()).hour
        _, sleep_state = get_phase_for_hour(hour, self.wake_hour, self.sleep_hour)
        return sleep_state != "sleeping"

    # ── 思念时间表（每天 N 次）───────────────────────────────────────────
    def _ensure_longing_schedule(self):
        today    = date.today().isoformat()
        schedule = self._load(self.longing_schedule_path, {})
        if schedule.get("date") == today:
            return

        proactive_cfg = self._bcfg.get("proactive", {})
        active_hours  = proactive_cfg.get("longing_active_hours",
                        list(range(10, 24)))
        count         = proactive_cfg.get("longing_count_per_day", 1)

        chosen = random.sample(active_hours, min(count, len(active_hours)))
        times  = [f"{h:02d}:{random.randint(0,59):02d}" for h in chosen]

        self._save(self.longing_schedule_path, {
            "date": today, "times": times, "fired": [],
        })
        print(f"[思念] 今天的时间：{times}")

    def _check_longing_trigger(self, now: datetime) -> bool:
        schedule = self._load(self.longing_schedule_path, {})
        if schedule.get("date") != date.today().isoformat():
            self._ensure_longing_schedule()
            return False

        last_fired_ts = schedule.get("last_fired_ts")
        if last_fired_ts:
            try:
                last_fired = datetime.fromisoformat(last_fired_ts)
                if (now - last_fired).total_seconds() < 20 * 3600:
                    return False
            except:
                pass

        fired = schedule.get("fired", [])
        for t in schedule.get("times", []):
            if t in fired:
                continue
            t_h, t_m = map(int, t.split(":"))
            target   = now.replace(hour=t_h, minute=t_m, second=0)
            if abs((now - target).total_seconds() / 60) <= 5:
                fired.append(t)
                schedule["fired"] = fired
                schedule["last_fired_ts"] = now.isoformat()
                self._save(self.longing_schedule_path, schedule)
                return True
        return False

    def _send_longing_message(self, now: datetime):
        user_info = ""
        try:
            with open(os.path.join(self.data_dir, "user_profile.json"), encoding="utf-8") as f:
                rp = json.load(f)
            if any(v for v in rp.values() if v):
                user_info = json.dumps(rp, ensure_ascii=False)
        except:
            pass

        current_activity = "发呆"
        if self.bot and self.bot.character_state:
            current_activity = self.bot.character_state.state.get("current_activity", "发呆")

        prompt = self._prompts.get("longing", "").format(
            current_time        = now.strftime("%H:%M"),
            ruby_info           = user_info or "（还不太了解）",
            user_info           = user_info or "（还不太了解）",
            relationship_status = self._get_relationship_status() or "刚重新连接",
            activity            = current_activity,
            character_name      = self.char_name,
            user_name           = self.user_name,
        )

        try:
            msg = self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="", max_tokens=60, temperature=0.88,
            ).strip()
            if msg:
                self.send_func(msg)
                self.last_proactive_ts = now
                print(f"[思念✉️] {now.strftime('%H:%M')} {msg[:40]}...")
        except Exception as e:
            print(f"[思念✗] {e}")

    # ── 活动开放度 ───────────────────────────────────────────────────────
    def _is_high_openness(self, activity: str, tags: List[str] = None) -> bool:
        tags = tags or []
        for kw in self._high_openness_keywords:
            if kw in activity or kw in tags:
                return True
        return False

    # ── 世界扩充 ─────────────────────────────────────────────────────────
    def _try_expand_world(self, activity: str):
        cfg = self._bcfg.get("world_expand", {})
        if random.random() > cfg.get("trigger_probability", 0.30):
            return

        world_bible = self._load(self.world_bible_path, {"已确认": {}, "待验证": []})
        known_facts = []
        for category, facts in world_bible.get("已确认", {}).items():
            if isinstance(facts, list):
                known_facts.extend([f"{category}：{f}" for f in facts[:2]])

        prompt = self._prompts.get("world_expand", "").format(
            activity    = activity,
            known_facts = "\n".join(known_facts[:6]) if known_facts else "（暂无）",
            character_name = self.char_name,
            character_identity = self.config.get("character", {}).get("core_personality", ""),
        )

        try:
            new_detail = self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="", max_tokens=60, temperature=0.8,
            ).strip()
            if new_detail:
                pending = world_bible.get("待验证", [])
                if new_detail not in pending:
                    pending.append(new_detail)
                    world_bible["待验证"] = pending[-cfg.get("max_pending", 10):]
                    self._save(self.world_bible_path, world_bible)
                    print(f"[世界扩充] 待验证：{new_detail[:40]}...")
        except Exception as e:
            print(f"[世界扩充✗] {e}")

    # ── 社会网络事件生成 ─────────────────────────────────────────────────
    def _try_update_social_network(self, activity: str):
        cfg = self._bcfg.get("social_network", {})
        if random.random() > cfg.get("life_trigger_probability", 0.25):
            return

        try:
            sn = self._load(self.social_network_path, {})
        except:
            return

        involved = []
        for circle, members in sn.items():
            if not isinstance(members, dict):
                continue
            for name in members:
                if name in activity:
                    involved.append(name)

        if not involved:
            return

        name   = involved[0]
        prompt = self._prompts.get("social_network_event", "").format(
            activity       = activity,
            name           = name,
            character_name = self.char_name,
        )

        try:
            event = self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="", max_tokens=40, temperature=0.8,
            ).strip()

            if event:
                max_behaviors = cfg.get("max_behaviors_per_node", 10)
                for circle, members in sn.items():
                    if not isinstance(members, dict):
                        continue
                    if name in members:
                        members[name]["recent_event"] = event
                        behaviors = members[name].get("confirmed_behaviors", [])
                        if event not in behaviors:
                            behaviors.append(event)
                            members[name]["confirmed_behaviors"] = behaviors[-max_behaviors:]
                        break

                self._save(self.social_network_path, sn)
                print(f"[社会网络✓] {name} 事件：{event[:30]}...")
        except Exception as e:
            print(f"[社会网络✗] {e}")

    # ── 高关注模式 ───────────────────────────────────────────────────────
    def _get_attention_mode(self) -> dict:
        return self._load(self.attention_mode_path, {"active": False, "reason": "", "until": ""})

    def _is_attention_mode(self) -> bool:
        mode = self._get_attention_mode()
        if not mode.get("active"):
            return False
        until = mode.get("until", "")
        if until and datetime.now() > datetime.fromisoformat(until):
            mode["active"] = False
            self._save(self.attention_mode_path, mode)
            print("[高关注] 模式已结束")
            return False
        return True

    def activate_attention_mode(self, reason: str, hours: int = 48):
        until = (datetime.now() + timedelta(hours=hours)).isoformat()
        self._save(self.attention_mode_path, {"active": True, "reason": reason, "until": until})
        print(f"[高关注] 激活：{reason}，持续{hours}小时")

    # ── 闹钟系统 ─────────────────────────────────────────────────────────
    def _check_alarms(self, now: datetime):
        alarms    = self._load(self.alarms_path, [])
        remaining = []
        for alarm in alarms:
            alarm_time = datetime.fromisoformat(alarm["time"])
            if now >= alarm_time and not alarm.get("fired"):
                alarm["fired"] = True
                print(f"[闹钟] 触发：{alarm.get('content', '提醒')}")
                self._send_alarm_message(alarm, now)
            remaining.append(alarm)
        self._save(self.alarms_path, remaining)

    def add_alarm(self, content: str, alarm_time: datetime):
        alarms = self._load(self.alarms_path, [])
        alarms.append({
            "id": f"alarm_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "content": content, "time": alarm_time.isoformat(), "fired": False,
        })
        self._save(self.alarms_path, alarms)
        print(f"[闹钟✓] {content}，时间：{alarm_time.strftime('%m/%d %H:%M')}")

    def _send_alarm_message(self, alarm: dict, now: datetime):
        prompt = self._prompts.get("alarm_message", "").format(
            content        = alarm.get("content", ""),
            current_time   = now.strftime("%H:%M"),
            character_name = self.char_name,
        )
        try:
            msg = self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="", max_tokens=100, temperature=0.8,
            ).strip()
            if msg:
                self.send_func(msg)
                print(f"[闹钟✉️] {msg[:40]}...")
        except Exception as e:
            print(f"[闹钟发送✗] {e}")

    # ── 学习系统 ─────────────────────────────────────────────────────────
    def add_to_learn(self, topic: str, background: str = ""):
        pending = self._load(self.pending_learn_path, [])
        if any(p.get("topic") == topic for p in pending):
            return
        pending.append({"topic": topic, "background": background,
                        "added": datetime.now().isoformat()})
        self._save(self.pending_learn_path, pending)
        print(f"[学习队列] 加入：{topic}")

    def _get_next_learn_topic(self) -> Optional[dict]:
        pending = self._load(self.pending_learn_path, [])
        if pending:
            return pending[0]
        topics = self._self_interest_topics
        if not topics:
            return None
        topic = random.choice(topics)
        return {"topic": topic, "background": f"{self.char_name}自己感兴趣的领域"}

    def _mark_topic_learned(self, topic: str):
        pending = self._load(self.pending_learn_path, [])
        pending = [p for p in pending if p.get("topic") != topic]
        self._save(self.pending_learn_path, pending)

        try:
            with open(self.keywords_path, encoding="utf-8") as f:
                kw_data = json.load(f)
            topics = kw_data.get("self_interest_topics", [])
            if topic not in topics:
                topics.append(topic)
                kw_data["self_interest_topics"] = topics
                with open(self.keywords_path, "w", encoding="utf-8") as f:
                    json.dump(kw_data, f, ensure_ascii=False, indent=2)
                print(f"[兴趣扩展✓] 新增：{topic}")
        except Exception as e:
            print(f"[兴趣扩展✗] {e}")

    def _do_learning(self, topic_data: dict):
        topic      = topic_data.get("topic", "")
        background = topic_data.get("background", "")
        print(f"[学习] 开始研究：{topic}")

        prompt = self._prompts.get("learning", "").format(
            topic          = topic,
            background     = background or "从零开始了解",
            character_name = self.char_name,
            user_name      = self.user_name,
        )
        try:
            findings = self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="", max_tokens=200, temperature=0.85,
            ).strip()

            if findings:
                notes = self._load(self.learning_notes_path, [])
                notes.append({
                    "ts": datetime.now().isoformat(),
                    "topic": topic, "findings": findings, "shared": False,
                })
                self._save(self.learning_notes_path, notes[-20:])
                print(f"[学习✓] {topic}：{findings[:40]}...")
                self._mark_topic_learned(topic)

                if self.bot and self.bot.character_state:
                    self.bot.character_state.state["current_activity"] = f"在研究{topic}，有点意思"
                    self.bot.character_state.save()

                learning_cfg = self._bcfg.get("learning", {})
                if random.random() < learning_cfg.get("share_probability", 0.5):
                    self._share_learning(topic, findings)
        except Exception as e:
            print(f"[学习✗] {e}")

    def _share_learning(self, topic: str, findings: str):
        learning_cfg = self._bcfg.get("learning", {})
        cooldown     = learning_cfg.get("share_cooldown_minutes", 20)
        if self.last_proactive_ts:
            gap_min = (datetime.now() - self.last_proactive_ts).total_seconds() / 60
            if gap_min < cooldown:
                return

        prompt = self._prompts.get("share_learning", "").format(
            topic          = topic,
            findings       = findings,
            character_name = self.char_name,
            user_name      = self.user_name,
        )
        try:
            msg = self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="", max_tokens=120, temperature=0.85,
            ).strip()
            if msg:
                self.send_func(msg)
                self.last_proactive_ts = datetime.now()
                print(f"[学习分享✉️] {msg[:40]}...")
        except Exception as e:
            print(f"[学习分享✗] {e}")

    def _trigger_daydream_to_learning(self):
        learning_cfg = self._bcfg.get("learning", {})
        delay = random.randint(
            learning_cfg.get("daydream_min_minutes", 5),
            learning_cfg.get("daydream_max_minutes", 10),
        ) * 60
        print(f"[发呆] {delay//60}分钟后进入学习模式")
        if self._daydream_timer:
            self._daydream_timer.cancel()
        self._daydream_timer = threading.Timer(delay, self._execute_daydream_learning)
        self._daydream_timer.daemon = True
        self._daydream_timer.start()

    def _execute_daydream_learning(self):
        topic_data = self._get_next_learn_topic()
        if topic_data:
            self._do_learning(topic_data)

    # ── 用户睡眠检测 ────────────────────────────────────────────────────
    def _is_user_sleeping(self, now: datetime) -> bool:
        if not self._user_sleeping:
            content = self.last_user_message_content
            if self._detect_user_sleeping(content):
                if self.last_user_message_ts:
                    gap_h = (now - self.last_user_message_ts).total_seconds() / 3600
                    if gap_h >= 0.5:
                        self._user_sleeping = True
                        self._user_sleep_ts = self.last_user_message_ts
                        print(f"[生活] 检测到用户已离开")
        return self._user_sleeping

    # ── 话题重复抑制（通用版）────────────────────────────────────────────
    def _classify_message_topic(self, decision: dict) -> str:
        """基于 message_seed 和 activity 对话题分类，防止同类话题重复发送"""
        seed = decision.get("message_seed", "") + decision.get("activity", "")
        msg_type = decision.get("message_type", "")

        # care 类消息单独归类（关心/催促）
        if msg_type == "care":
            return "关心"
        # check 类消息（确认状态）
        if msg_type == "check":
            return "确认"
        # share 类消息（分享自己的事）
        if msg_type == "share":
            return "分享"
        # miss 类消息（想念）
        if msg_type == "miss":
            return "想念"
        return "其他"

    def _is_topic_suppressed(self, topic_type: str, now: datetime) -> bool:
        if topic_type in ("其他", "分享"):
            return False
        suppress_until = self._topic_suppress.get(topic_type)
        if suppress_until and now < suppress_until:
            return True
        recent_same = [
            ts for t, ts in self._recent_topics
            if t == topic_type
            and (now - ts).total_seconds() < 3600 * 24
        ]
        count = len(recent_same)
        if count >= 3:
            self._topic_suppress[topic_type] = now + timedelta(hours=24)
            print(f"[抑制] 「{topic_type}」已发送{count}次，抑制24小时")
            return True
        if count >= 2:
            last_ts = max(ts for t, ts in self._recent_topics if t == topic_type)
            if (now - last_ts).total_seconds() < 3600 * 6:
                return True
        return False

    def _record_topic(self, topic_type: str, now: datetime):
        self._recent_topics.append((topic_type, now))
        self._recent_topics = [
            (t, ts) for t, ts in self._recent_topics
            if (now - ts).total_seconds() < 3600 * 48
        ]

    def suppress_nag_topics(self):
        """用户表示烦了，立刻抑制所有催促类话题 12 小时"""
        now = datetime.now()
        for topic in ["关心", "确认", "想念"]:
            self._topic_suppress[topic] = now + timedelta(hours=12)
        print(f"[抑制] 用户抱怨了，催促类话题抑制12小时")

    # ── 关系状态 ─────────────────────────────────────────────────────────
    def _get_relationship_status(self) -> str:
        try:
            with open(os.path.join(self.data_dir, "relationship_status.json"), encoding="utf-8") as f:
                return json.load(f).get("status", "")
        except:
            return ""

    # ── 主 tick ──────────────────────────────────────────────────────────
    def tick(self):
        now = datetime.now()

        is_now_sleeping = not self.is_awake(now)
        if self._was_awake and is_now_sleeping:
            if self.bot:
                self.bot.soft_reset(reason="进入睡眠时段")
        if not is_now_sleeping and self.bot:
            self.bot.clear_reset_flag()
        self._was_awake = not is_now_sleeping

        if self.bot and self.bot.last_user_message_time:
            gap_h = (now - self.bot.last_user_message_time).total_seconds() / 3600
            if 1 <= gap_h < 2 and len(self.bot.recent_history) >= 2:
                self.bot.force_compress()
                print(f"[自动压缩] 用户超过1小时未发消息")

        if self.bot and self.bot.character_state and self.last_user_message_ts:
            self.bot.character_state.check_relation_decay(
                self.last_user_message_ts.isoformat()
            )

        hour = now.hour
        if not self.is_awake(now):
            from core.state import ActivityPoolManager
            pool_mgr = ActivityPoolManager()
            # 根据作息判断是 sleep 还是 waking
            phase, _ = get_phase_for_hour(hour, self.wake_hour, self.sleep_hour)
            axes     = self.bot.character_state.state_core["三轴状态"] if self.bot and self.bot.character_state else {}
            entry    = pool_mgr.weighted_choice(phase, axes)
            activity = entry.get("text", "睡着了")

            self.life_log.append({"ts": now.isoformat(), "activity": activity, "should_message": False})
            self._save_log()
            print(f"[生活] {now.strftime('%H:%M')} 💤 {activity}")
            self._check_alarms(now)
            return

        if self._check_longing_trigger(now):
            if not self._is_user_sleeping(now):
                self._send_longing_message(now)
            else:
                print(f"[思念] 用户离开了，跳过思念消息")

        self._check_alarms(now)

        user_sleeping = self._is_user_sleeping(now)
        decision = self._call_life_tick(now, user_sleeping=user_sleeping)
        activity  = decision.get("activity", "")

        if "发呆" in activity or "失焦" in activity:
            decision["activity"] = "发呆，思路开始游走"
            self._trigger_daydream_to_learning()

        if self._is_high_openness(activity):
            threading.Thread(target=self._try_expand_world, args=(activity,), daemon=True).start()
            threading.Thread(target=self._try_update_social_network, args=(activity,), daemon=True).start()

        if decision.get("search_topic"):
            decision = self._enrich_with_simulated_search(decision)

        self.life_log.append(decision)
        self._save_log()

        detail = decision.get("activity_detail", decision.get("activity", "?"))
        print(f"[生活] {now.strftime('%H:%M')} ✦ {detail[:60]}")

        if not decision.get("should_message"):
            self._maybe_share_pending_note(now)
            return

        bcfg         = self._bcfg.get("proactive", {})
        is_attention = self._is_attention_mode()
        cooldown_min = bcfg.get("attention_cooldown_minutes", 20) if is_attention \
                       else bcfg.get("normal_cooldown_minutes", 30)
        daily_max    = bcfg.get("attention_daily_max", 8) if is_attention \
                       else bcfg.get("normal_daily_max", 4)

        if self.last_proactive_ts:
            gap_min = (now - self.last_proactive_ts).total_seconds() / 60
            if gap_min < cooldown_min:
                print(f"[生活] 冷却中（{gap_min:.0f}/{cooldown_min}min）")
                return

        if self._count_today_proactive() >= daily_max:
            print(f"[生活] 今日上限（{daily_max}条）")
            return

        topic_type = self._classify_message_topic(decision)
        if self._is_topic_suppressed(topic_type, now):
            print(f"[生活] 话题「{topic_type}」被抑制，跳过")
            return

        msg = self._compose_message(decision, now)
        if msg:
            self.send_func(msg)
            decision["sent_message"] = msg
            self.last_proactive_ts   = now
            self._record_topic(topic_type, now)
            self._save_log()
            print(f"[生活] {now.strftime('%H:%M')} ✉️ {msg[:50]}...")

    def _maybe_share_pending_note(self, now: datetime):
        notes    = self._load(self.learning_notes_path, [])
        unshared = [n for n in notes if not n.get("shared")]
        if not unshared:
            return
        note     = random.choice(unshared)
        cooldown = self._bcfg.get("learning", {}).get("share_cooldown_minutes", 20)
        if self.last_proactive_ts:
            gap_min = (now - self.last_proactive_ts).total_seconds() / 60
            if gap_min < cooldown:
                return
        self._share_learning(note["topic"], note["findings"])
        for n in notes:
            if n.get("topic") == note["topic"] and n.get("ts") == note["ts"]:
                n["shared"] = True
        self._save(self.learning_notes_path, notes)

    def _call_life_tick(self, now: datetime, user_sleeping: bool = False) -> dict:
        recent      = self.life_log[-5:]
        recent_text = "\n".join(
            f"  [{e.get('ts','')[:16]}] {e.get('activity_detail', e.get('activity','?'))}"
            for e in recent
        ) or "  （刚开始活动）"

        last_msg_time = self.last_user_message_ts.strftime("%m/%d %H:%M") \
                        if self.last_user_message_ts else "未知"
        time_gap = "未知"
        if self.last_user_message_ts:
            gap_h    = (now - self.last_user_message_ts).total_seconds() / 3600
            time_gap = f"{gap_h:.1f}小时前" if gap_h >= 1 else f"{int(gap_h*60)}分钟前"

        last_proactive      = self.last_proactive_ts.strftime("%m/%d %H:%M") \
                              if self.last_proactive_ts else "还没主动找过"
        is_attention        = self._is_attention_mode()
        attention_mode      = "是（高关注模式）" if is_attention else "否"
        relationship_status = self._get_relationship_status() or "刚重新连接"

        last_content = self.last_user_message_content
        sleep_words = ["睡了", "去睡", "睡觉", "晚安", "好梦", "睡着"]
        busy_words  = ["去忙", "忙去了", "要忙", "有事", "等下要去", "我要去", "去做", "先去了", "先忙"]
        if user_sleeping:
            if any(kw in last_content for kw in sleep_words):
                user_status = (
                    f"{self.user_name}目前在睡觉（说了去睡，超过30分钟没有新消息）。"
                    f"如果要发消息，只说说自己在做什么，像是给睡着的人留言，绝对不要催任何事。"
                )
            else:
                user_status = (
                    f"{self.user_name}说去忙了（超过30分钟没有新消息）。"
                    f"如果要发消息，说说自己的事或随手一提，不要催做任何事。"
                )
        else:
            user_status = f"{self.user_name}目前在线或状态未知。"

        prompt = self._prompts.get("life_tick", "").format(
            current_time        = now.strftime("%Y年%m月%d日 %H:%M"),
            last_msg_time       = last_msg_time,
            last_msg_content    = self.last_user_message_content,
            last_proactive_time = last_proactive,
            relationship_status = relationship_status,
            attention_mode      = attention_mode,
            recent_activities   = recent_text,
            ruby_status         = user_status,
            user_status         = user_status,
            character_name      = self.char_name,
            user_name           = self.user_name,
        )

        default = {
            "ts": now.isoformat(), "activity": "休息", "mood": "calm",
            "should_message": False, "message_type": "none",
            "message_seed": "", "search_topic": "",
        }

        try:
            raw = self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="", max_tokens=200, temperature=0.7,
            )
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if match:
                result       = json.loads(match.group())
                result["ts"] = now.isoformat()
                return result
        except Exception as e:
            print(f"[Life] Tick 解析失败: {e}")

        return default

    def _enrich_with_simulated_search(self, decision: dict) -> dict:
        topic = decision.get("search_topic", "")
        if not topic:
            return decision
        prompt = (
            f"{self.char_name}正在研究：{decision.get('activity', topic)}\n"
            f"主题：{topic}\n"
            f'用JSON回复：{{"activity_detail": "具体在看什么", "found": [{{"title": "标题", "brief": "一句话"}}]}}\n'
            f"只输出JSON。"
        )
        try:
            raw = self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="", max_tokens=200, temperature=0.8,
            )
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if match:
                enrichment = json.loads(match.group())
                decision["activity_detail"] = enrichment.get("activity_detail", "")
                decision["found"]           = enrichment.get("found", [])
        except Exception as e:
            print(f"[Life] 搜索充实失败: {e}")
        return decision

    def _compose_message(self, decision: dict, now: datetime) -> Optional[str]:
        time_gap = "未知"
        if self.last_user_message_ts:
            gap_h    = (now - self.last_user_message_ts).total_seconds() / 3600
            time_gap = f"{gap_h:.1f}小时前" if gap_h >= 1 else f"{int(gap_h*60)}分钟前"

        prompt = self._prompts.get("compose_proactive", "").format(
            activity            = decision.get("activity_detail", decision.get("activity", "")),
            mood                = decision.get("mood", ""),
            message_type        = decision.get("message_type", ""),
            message_seed        = decision.get("message_seed", ""),
            current_time        = now.strftime("%H:%M"),
            last_msg_content    = self.last_user_message_content,
            time_gap            = time_gap,
            relationship_status = self._get_relationship_status() or "刚重新连接",
            character_name      = self.char_name,
            user_name           = self.user_name,
        )

        try:
            return self.llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="", max_tokens=150, temperature=0.85,
            ).strip()
        except Exception as e:
            print(f"[Life] 消息生成失败: {e}")
            return None

    def _count_today_proactive(self) -> int:
        today = date.today()
        return sum(
            1 for e in self.life_log
            if e.get("sent_message") and e.get("ts")
            and datetime.fromisoformat(e["ts"]).date() == today
        )

    def get_recent_activities_text(self, n: int = 5) -> str:
        recent = [e for e in self.life_log[-n:] if e.get("activity")]
        if not recent:
            return ""
        return "\n".join(
            f"  [{e.get('ts','')[:16]}] {e.get('activity_detail', e.get('activity','?'))}"
            for e in recent
        )

    def get_24h_activities_text(self) -> str:
        cutoff = datetime.now() - timedelta(hours=24)
        recent = [
            e for e in self.life_log
            if e.get("ts") and datetime.fromisoformat(e["ts"]) > cutoff and e.get("activity")
        ]
        if not recent:
            return ""
        lines = [
            f"  [{e.get('ts','')[:16]}] {e.get('activity_detail', e.get('activity','?'))}"
            for e in recent[-10:]
        ]
        notes = self._load(self.learning_notes_path, [])
        for n in [n for n in notes if n.get("ts") and datetime.fromisoformat(n["ts"]) > cutoff][-3:]:
            lines.append(f"  [{n['ts'][:16]}] 研究了{n['topic']}：{n['findings'][:40]}...")
        return "\n".join(lines)
