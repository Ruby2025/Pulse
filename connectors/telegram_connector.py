"""
connectors/telegram_connector.py ── Pulse 开源版
从 JSON 读取所有配置，无任何角色硬编码内容
包含：图片识别（Gemini）、状态驱动延迟、话题连贯、重大事件检测（自动扩展keywords）
"""

import json
import os
import requests
import time
import random
import re
import threading
import base64
from datetime import datetime, timedelta
from typing import Optional

from core.bot import AIChanBot
from core.state import get_behavior_config, get_char_config, get_prompts, parse_schedule


def _load_keywords() -> dict:
    try:
        with open(os.path.join("data", "character", "keywords.json"), encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def _save_keywords(data: dict):
    path = os.path.join("data", "character", "keywords.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[关键词写入✗] {e}")


class TelegramAPI:

    def __init__(self, token: str):
        self.token = token
        self.base  = f"https://api.telegram.org/bot{token}"

    def get_updates(self, offset: int = 0, timeout: int = 30) -> list:
        try:
            resp = requests.get(
                f"{self.base}/getUpdates",
                params={"offset": offset, "timeout": timeout},
                timeout=timeout + 5
            )
            data = resp.json()
            if data.get("ok"):
                return data.get("result", [])
        except Exception as e:
            print(f"[Telegram] 获取消息失败: {e}")
        return []

    def send_message(self, chat_id: int, text: str,
                     reply_to_message_id: Optional[int] = None) -> bool:
        try:
            payload = {"chat_id": chat_id, "text": text}
            if reply_to_message_id:
                payload["reply_to_message_id"] = reply_to_message_id
            resp = requests.post(
                f"{self.base}/sendMessage",
                json=payload,
                timeout=15
            )
            return resp.json().get("ok", False)
        except Exception as e:
            print(f"[Telegram] 发送消息失败: {e}")
            return False

    def send_typing(self, chat_id: int):
        try:
            requests.post(
                f"{self.base}/sendChatAction",
                json={"chat_id": chat_id, "action": "typing"},
                timeout=5
            )
        except:
            pass

    def get_file_path(self, file_id: str) -> Optional[str]:
        try:
            resp = requests.get(f"{self.base}/getFile", params={"file_id": file_id}, timeout=10)
            data = resp.json()
            if data.get("ok"):
                return data["result"]["file_path"]
        except Exception as e:
            print(f"[Telegram] 获取文件路径失败: {e}")
        return None

    def download_file_as_base64(self, file_path: str) -> Optional[str]:
        try:
            url  = f"https://api.telegram.org/file/bot{self.token}/{file_path}"
            resp = requests.get(url, timeout=30)
            if resp.status_code == 200:
                return base64.b64encode(resp.content).decode("utf-8")
        except Exception as e:
            print(f"[Telegram] 下载文件失败: {e}")
        return None

    def test_connection(self) -> dict:
        try:
            resp = requests.get(f"{self.base}/getMe", timeout=10)
            data = resp.json()
            if data.get("ok"):
                return data.get("result", {})
        except Exception as e:
            print(f"[Telegram] 连接测试失败: {e}")
        return {}


class TelegramConnector:

    def __init__(self, bot: AIChanBot, config: dict):
        self.bot    = bot
        self.config = config

        token = config.get("connector", {}).get("telegram", {}).get("bot_token", "")
        self.tg = TelegramAPI(token)

        self.char_name = config.get("character", {}).get("name", "AI")
        self.user_name = config.get("user", {}).get("name", "用户")
        self.offset    = 0

        # 从 character.yaml 读取作息
        self.wake_hour, self.sleep_hour = parse_schedule(config.get("character", {}))

        self.allowed_chat_ids: list = config.get("connector", {}).get("allowed_chat_ids", [])
        self.emergency_keyword: str = config.get("connector", {}).get("telegram", {}).get("emergency_keyword", "")
        self.reset_keyword: str     = config.get("connector", {}).get("telegram", {}).get("reset_keyword", "")

        self.active_chats: set = set()
        self._pending: dict    = {}
        self._pending_lock     = threading.Lock()

        self._recent_msg_times: dict = {}
        self._flow_mode: dict        = {}
        self._msg_history: list      = []

        # 动态加载重大事件关键词
        self._major_event_keywords: list = _load_keywords().get("major_event_keywords", [
            "搬家", "生病", "发烧", "哭了", "崩溃"
        ])

        # 视觉能力检测
        llm_cfg = config.get("llm", {})
        self._supports_vision = llm_cfg.get("chat_model", {}).get("supports_vision", False)
        self._vision_llm = None
        if not self._supports_vision:
            vision_cfg = llm_cfg.get("vision_model", {})
            if vision_cfg.get("provider"):
                from core.llm_client import LLMClient
                self._vision_llm = LLMClient(
                    provider=vision_cfg.get("provider", "gemini"),
                    model=vision_cfg.get("model", "gemini-2.5-flash-lite"),
                    api_key=vision_cfg.get("api_key", "env:GOOGLE_API_KEY"),
                )
                print(f"[视觉] 使用独立视觉模型: {vision_cfg.get('provider')}/{vision_cfg.get('model')}")
            else:
                print("[视觉] 未配置 vision_model 且 chat_model 不支持视觉，图片将无法识别")
        else:
            print(f"[视觉] chat_model 自带视觉能力，图片直接发送")

    # ── 配置快捷访问 ─────────────────────────────────────────────────────
    @property
    def _bcfg(self) -> dict:
        return get_behavior_config()

    @property
    def _char_cfg(self) -> dict:
        return get_char_config()

    @property
    def _delay_scenarios(self) -> dict:
        return self._char_cfg.get("delay_scenarios", {
            "busy":     ["刚忙完手上的事"],
            "sleeping": ["刚醒，还没完全清醒"],
            "default":  ["刚放下手上的事"],
        })

    def _is_allowed(self, chat_id: int) -> bool:
        if not self.allowed_chat_ids:
            return True
        return chat_id in self.allowed_chat_ids

    # ── 话题连贯检测 ─────────────────────────────────────────────────────
    def _update_flow_mode(self, chat_id: int) -> bool:
        now    = datetime.now()
        dcfg   = self._bcfg.get("delay", {})
        window = dcfg.get("flow_mode_window_minutes", 5)
        thresh = dcfg.get("flow_mode_threshold_messages", 3)

        if chat_id not in self._recent_msg_times:
            self._recent_msg_times[chat_id] = []

        cutoff = now - timedelta(minutes=window)
        self._recent_msg_times[chat_id] = [
            t for t in self._recent_msg_times[chat_id] if t > cutoff
        ]
        self._recent_msg_times[chat_id].append(now)

        count = len(self._recent_msg_times[chat_id])
        if count >= thresh:
            if not self._flow_mode.get(chat_id):
                print(f"[连贯模式] 进入，{window}分钟内{count}条消息")
            self._flow_mode[chat_id] = True
        else:
            self._flow_mode[chat_id] = False

        return self._flow_mode.get(chat_id, False)

    # ── 重大事件检测 ─────────────────────────────────────────────────────
    def _check_major_event(self, text: str):
        if not hasattr(self.bot, 'life_manager') or not self.bot.life_manager:
            return
        life_manager = self.bot.life_manager

        for kw in self._major_event_keywords:
            if kw in text:
                if not life_manager._is_attention_mode():
                    life_manager.activate_attention_mode(reason=f"检测到：{kw}", hours=48)
                return

        if len(text) > 20:
            self._auto_detect_major_event(text, life_manager)

    def _auto_detect_major_event(self, text: str, life_manager):
        prompt = get_prompts().get("auto_detect_major_event", "").format(
            content=text,
            character_name=self.char_name,
            user_name=self.user_name,
        )
        try:
            raw   = self.bot.bg_llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="", max_tokens=80, temperature=0.1,
            )
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if not match:
                return
            data = json.loads(match.group())
            if data.get("is_major"):
                keyword = data.get("keyword", "")
                reason  = data.get("reason", "")
                if keyword and keyword not in self._major_event_keywords:
                    self._major_event_keywords.append(keyword)
                    kw_data = _load_keywords()
                    kw_data["major_event_keywords"] = self._major_event_keywords
                    _save_keywords(kw_data)
                    print(f"[自检测] 新关键词写入：{keyword}")
                life_manager.activate_attention_mode(reason=reason or keyword, hours=48)
        except:
            pass

    # ── 时间承诺检测 ─────────────────────────────────────────────────────
    def _check_time_promise(self, text: str, bot_response: str):
        if not hasattr(self.bot, 'life_manager') or not self.bot.life_manager:
            return
        combined      = f"{self.user_name}: {text}\n{self.char_name}: {bot_response}"
        time_keywords = ["小时后", "分钟后", "明天", "今晚", "下午", "待会", "等你", "到时候"]
        if not any(kw in combined for kw in time_keywords):
            return
        prompt = get_prompts().get("detect_time_promise", "").format(
            convo=combined,
            character_name=self.char_name,
            user_name=self.user_name,
        )
        try:
            raw   = self.bot.bg_llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="", max_tokens=80, temperature=0.1,
            )
            match = re.search(r'\{.*\}', raw, re.DOTALL)
            if not match:
                return
            data = json.loads(match.group())
            if data.get("has_promise") and data.get("hours_from_now"):
                alarm_time = datetime.now() + timedelta(hours=float(data["hours_from_now"]))
                self.bot.life_manager.add_alarm(content=data["content"], alarm_time=alarm_time)
        except:
            pass

    # ── 图片提取 ─────────────────────────────────────────────────────────
    def _get_image_from_message(self, message: dict) -> tuple:
        caption = message.get("caption", "")
        photos  = message.get("photo", [])
        if photos:
            file_id   = photos[-1].get("file_id", "")
            file_path = self.tg.get_file_path(file_id)
            if file_path:
                b64 = self.tg.download_file_as_base64(file_path)
                if b64:
                    print(f"[图片] 收到图片，大小约{len(b64)//1024}KB")
                    return b64, caption

        doc = message.get("document", {})
        if doc and doc.get("mime_type", "").startswith("image/"):
            file_id   = doc.get("file_id", "")
            file_path = self.tg.get_file_path(file_id)
            if file_path:
                b64 = self.tg.download_file_as_base64(file_path)
                if b64:
                    print(f"[图片] 收到图片文件，大小约{len(b64)//1024}KB")
                    return b64, caption

        return None, caption

    # ── 状态驱动延迟判断（通用版）─────────────────────────────────────────
    def _get_delay_decision(self, is_flow_mode: bool) -> tuple:
        dcfg = self._bcfg.get("delay", {})

        # 发呆/闲着时不延迟
        if self.bot.character_state:
            activity = self.bot.character_state.state.get("current_activity", "")
            if any(w in activity for w in ["发呆", "失焦", "待着", "闲"]):
                return (False, 0, "default")

        # 连贯聊天模式：极低概率延迟
        if is_flow_mode:
            if random.random() < dcfg.get("flow_mode_probability", 0.05):
                return (True, random.randint(1, 5) * 60, "default")
            return (False, 0, "default")

        # 高关注模式：低概率延迟
        if self.bot.life_manager and self.bot.life_manager._is_attention_mode():
            if random.random() < dcfg.get("attention_mode_probability", 0.08):
                return (True, random.randint(3, 10) * 60, "default")
            return (False, 0, "default")

        if not self.bot.character_state:
            prob = dcfg.get("default_probability", 0.08)
            return (random.random() < prob, random.randint(5, 20) * 60, "default")

        state    = self.bot.character_state.state
        activity = state.get("current_activity", "")
        sleep_st = state.get("sleep_state", "awake")

        # 睡觉中：小概率被吵醒，否则延迟到起床时间
        if sleep_st == "sleeping":
            if random.random() > dcfg.get("sleeping_probability", 0.85):
                return (False, 0, "sleeping")
            wake_time = datetime.now().replace(
                hour=self.wake_hour, minute=random.randint(0, 30),
                second=0, microsecond=0
            )
            if wake_time <= datetime.now():
                wake_time += timedelta(days=1)
            delay_sec = int((wake_time - datetime.now()).total_seconds())
            return (True, delay_sec, "sleeping")

        # 基于活动 tags 判断延迟场景
        scenario_key, prob, min_d, max_d = self._classify_activity_delay(activity, dcfg)

        if random.random() < prob:
            return (True, random.randint(min_d, max_d) * 60, scenario_key)
        return (False, 0, scenario_key)

    def _classify_activity_delay(self, activity: str, dcfg: dict) -> tuple:
        """
        根据当前活动的 tags 判断延迟场景。
        不依赖任何角色特定关键词，完全基于活动池的 tags。
        返回 (scenario_key, probability, min_minutes, max_minutes)
        """
        # 获取当前活动的 tags
        tags = []
        if self.bot.character_state:
            entry = self.bot.character_state.state.get("current_activity_entry", {})
            tags  = [t.lower() for t in entry.get("tags", [])]

        # 基于 tags 匹配延迟场景（优先级从高到低）
        if "outdoor" in tags:
            return (
                "busy",
                dcfg.get("busy_probability", 0.15),
                dcfg.get("busy_min_minutes", 5),
                dcfg.get("busy_max_minutes", 15),
            )

        if any(t in tags for t in ["工作", "work", "训练", "exercise"]):
            return (
                "busy",
                dcfg.get("busy_probability", 0.15),
                dcfg.get("busy_min_minutes", 5),
                dcfg.get("busy_max_minutes", 15),
            )

        # 默认
        return (
            "default",
            dcfg.get("default_probability", 0.08),
            dcfg.get("default_min_minutes", 3),
            dcfg.get("default_max_minutes", 10),
        )

    # ── 引用判断 ─────────────────────────────────────────────────────────
    def _should_quote(self, user_text: str, reply: str) -> Optional[dict]:
        """判断回复时是否应该引用用户的某句话"""
        trigger_words = ["你说", "你刚才", "就是", "不对", "错了", "明明", "哪有", "瞎说"]
        if not any(kw in reply for kw in trigger_words):
            return None
        user_msgs = [m for m in self._msg_history if not m["from_bot"]][-5:]
        if not user_msgs:
            return None
        reply_words = set(reply.replace("，","").replace("。","").split())
        for msg in reversed(user_msgs):
            msg_words = set(msg["text"].replace("，","").replace("。","").split())
            if len(reply_words & msg_words) >= 2:
                return msg
        return user_msgs[-1] if user_msgs else None

    # ── 发送回复 ─────────────────────────────────────────────────────────
    def _send_reply(self, chat_id: int, text: str, extra_hint: str = "",
                    image_base64: Optional[str] = None, image_caption: str = ""):
        if extra_hint:
            original_method = self.bot._build_system_prompt
            def patched_prompt(user_message, topic_hint=""):
                base = original_method(user_message, topic_hint)
                return base + f"\n\n== 当前特殊状态 ==\n{extra_hint}\n这条消息是你刚忙完才看到的。"
            self.bot._build_system_prompt = patched_prompt
            reply = self.bot.reply(text, image_base64=image_base64, image_caption=image_caption)
            self.bot._build_system_prompt = original_method
        else:
            reply = self.bot.reply(text, image_base64=image_base64, image_caption=image_caption)

        self.tg.send_typing(chat_id)
        parts = [p.strip() for p in re.split(r'\\|\n\n|\n', reply) if p.strip()]

        quote_msg = self._should_quote(text, reply)
        quote_msg_id = quote_msg["message_id"] if quote_msg else None

        for i, part in enumerate(parts):
            msg_id = quote_msg_id if i == 0 else None
            success = self.tg.send_message(chat_id, part, reply_to_message_id=msg_id)
            if success:
                self._msg_history.append({
                    "message_id": None,
                    "text": part,
                    "from_bot": True,
                    "time": datetime.now().strftime("%H:%M")
                })
                self._msg_history = self._msg_history[-20:]
                print(f"[{datetime.now().strftime('%H:%M')}] {self.char_name}: {part}")
            if i < len(parts) - 1:
                time.sleep(0.8)

        threading.Thread(target=self._check_time_promise, args=(text, reply), daemon=True).start()

    def _flush_pending(self, chat_id: int):
        with self._pending_lock:
            pending = self._pending.pop(chat_id, None)
        if not pending:
            return
        messages      = pending.get("messages", [])
        scenario      = pending.get("scenario", "default")
        image_base64  = pending.get("image_base64")
        image_caption = pending.get("image_caption", "")
        if not messages and not image_base64:
            return

        combined = messages[0] if len(messages) == 1 \
                   else "（连续消息）\n" + "\n".join(f"- {m}" for m in messages)
        if not combined and image_base64:
            combined = image_caption or "（发了一张图）"

        scenarios = self._delay_scenarios
        hints     = scenarios.get(scenario, scenarios.get("default", ["刚忙完手上的事"]))
        hint      = random.choice(hints)
        print(f"[延迟回复] 场景：{hint}，合并 {len(messages)} 条")

        threading.Thread(
            target  = self._send_reply,
            args    = (chat_id, combined),
            kwargs  = {"extra_hint": hint, "image_base64": image_base64, "image_caption": image_caption},
            daemon  = True
        ).start()

    # ── 主消息处理 ───────────────────────────────────────────────────────
    def _handle_message(self, update: dict):
        message  = update.get("message", {})
        if not message:
            return

        chat_id  = message.get("chat", {}).get("id")
        text     = message.get("text", "").strip()
        username = message.get("from", {}).get("first_name", "用户")

        if not chat_id:
            return

        if not self._is_allowed(chat_id):
            self.tg.send_message(chat_id, "抱歉，我只和特定的人聊天～")
            return

        # 提取引用内容（用户引用了角色的消息）
        reply_text = ""
        reply_to = message.get("reply_to_message", {})
        if reply_to:
            replied_from = reply_to.get("from", {}).get("is_bot", False)
            replied_content = reply_to.get("text", "").strip()
            if replied_from and replied_content:
                reply_text = f"【{self.user_name}引用了你说的：「{replied_content[:60]}」】\n"
                print(f"[引用] {replied_content[:30]}...")

        # 图片处理
        image_base64, image_caption = self._get_image_from_message(message)
        if image_base64:
            if self._supports_vision:
                # chat_model 自带视觉：图片直接传给 bot.reply()
                print(f"[图片] 直接发送给 chat_model（支持视觉）")
            else:
                # chat_model 不支持视觉：用 vision_model 先描述
                vision = self._vision_llm or self.bot.chat_llm
                description = vision.describe_image(image_base64, image_caption)
                if text:
                    text = f"{text}\n[图片内容：{description}]"
                elif image_caption:
                    text = f"{image_caption}\n[图片内容：{description}]"
                else:
                    text = f"[图片内容：{description}]"
                image_base64 = None  # 已转为文字，不再传图片给 bot
                print(f"[图片] 已通过 vision_model 转换为文字描述")

        if not text:
            return

        if reply_text:
            text = reply_text + text

        self.active_chats.add(chat_id)
        self._last_msg_id = message.get("message_id")
        self._msg_history.append({
            "message_id": message.get("message_id"),
            "text": text,
            "from_bot": False,
            "time": datetime.now().strftime("%H:%M")
        })
        self._msg_history = self._msg_history[-20:]
        timestamp = datetime.now().strftime("%H:%M")
        print(f"[{timestamp}] {username}({chat_id}): {text[:60]}")

        is_flow = self._update_flow_mode(chat_id)

        # 重置暗语
        if self.reset_keyword and self.reset_keyword in text:
            self.bot.soft_reset(reason="用户手动重置")
            with self._pending_lock:
                if chat_id in self._pending:
                    if self._pending[chat_id].get("timer"):
                        self._pending[chat_id]["timer"].cancel()
                    del self._pending[chat_id]
            self.tg.send_message(chat_id, "我在。刚才哪说到了？")
            return

        # 紧急暗语
        if self.emergency_keyword and self.emergency_keyword in text:
            print(f"[紧急] 暗语触发，立刻回复")
            emergency_hint = f"{self.user_name}触发了紧急联系，你无论在做什么都会立刻看到并回复"
            threading.Thread(
                target  = self._send_reply,
                args    = (chat_id, text),
                kwargs  = {"extra_hint": emergency_hint},
                daemon  = True
            ).start()
            with self._pending_lock:
                if chat_id in self._pending:
                    if self._pending[chat_id].get("timer"):
                        self._pending[chat_id]["timer"].cancel()
                    del self._pending[chat_id]
            return

        # 检测重大事件
        threading.Thread(target=self._check_major_event, args=(text,), daemon=True).start()

        # 有图片时不走延迟
        if image_base64:
            threading.Thread(target=self._send_reply, args=(chat_id, text), daemon=True).start()
            return

        # 如果有待发队列，追加
        with self._pending_lock:
            if chat_id in self._pending:
                self._pending[chat_id]["messages"].append(text)
                print(f"[队列] 追加，当前 {len(self._pending[chat_id]['messages'])} 条")
                return

        should_delay, delay_seconds, scenario_key = self._get_delay_decision(is_flow)

        if should_delay:
            delay_min = delay_seconds // 60
            print(f"[{timestamp}] 延迟：{delay_min}分钟，场景：{scenario_key}")
            timer = threading.Timer(delay_seconds, self._flush_pending, args=[chat_id])
            timer.daemon = True
            with self._pending_lock:
                self._pending[chat_id] = {
                    "messages": [text], "timer": timer,
                    "scenario": scenario_key,
                    "image_base64": None, "image_caption": "",
                }
            timer.start()
        else:
            threading.Thread(target=self._send_reply, args=(chat_id, text), daemon=True).start()

    def run(self):
        bot_info = self.tg.test_connection()
        if not bot_info:
            print("[Telegram] ❌ 连接失败，请检查 bot_token 是否正确")
            return

        print(f"[Telegram] ✅ 连接成功: @{bot_info.get('username', '?')}")
        print(f"[Telegram] {self.char_name} 已在线，等待消息...\n")

        while True:
            try:
                updates = self.tg.get_updates(offset=self.offset, timeout=30)
                for update in updates:
                    self.offset = update.get("update_id", 0) + 1
                    threading.Thread(
                        target=self._handle_message, args=(update,), daemon=True
                    ).start()
            except Exception as e:
                print(f"[Telegram] 轮询出错: {e}，5秒后重试...")
                time.sleep(5)


def start_telegram(bot: AIChanBot, config: dict):
    connector = TelegramConnector(bot, config)
    connector.run()
