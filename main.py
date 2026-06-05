"""
main.py ── Pulse 开源版
启动入口，整合 LifeManager 生活循环
"""
import os
import time
import threading
import random
from datetime import datetime, timedelta

import yaml
from dotenv import load_dotenv

from core.bot import AIChanBot
from core.state import CharacterState
from core.life import LifeManager
from core.llm_client import LLMClient

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))


def load_config(path: str = "config/character.yaml") -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def minutes_until_next_hour() -> float:
    now = datetime.now()
    next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return (next_hour - now).total_seconds()


def schedule_life_tick(life_manager: LifeManager):
    try:
        life_manager.tick()
    except Exception as e:
        print(f"[Life Tick 出错] {e}")

    offset = random.randint(-25, 25) * 60
    delay = max(60, minutes_until_next_hour() + offset)
    t = threading.Timer(delay, schedule_life_tick, args=[life_manager])
    t.daemon = True
    t.start()
    next_time = (datetime.now() + timedelta(seconds=delay)).strftime("%H:%M")
    print(f"[Life] 下次触发：{next_time}")


def main():
    print("=" * 50)
    print("  Pulse — 给你的 AI 一个脉搏")
    print("=" * 50)

    config    = load_config()
    char_name = config.get("character", {}).get("name", "AI")
    user_name = config.get("user", {}).get("name", "用户")
    print(f"角色: {char_name}")
    print(f"用户: {user_name}")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    char_state = CharacterState(
        data_dir         = "./data",
        character_config = config.get("character", {})
    )
    char_state.silent = False

    connector_type = config.get("connector", {}).get("type", "none")
    print(f"连接器: {connector_type}")
    print("=" * 50)

    # ── 本地命令行测试模式 ──
    if connector_type == "none":
        bot = AIChanBot(config, character_state=char_state)
        print("本地命令行模式，输入 'quit' 退出\n")
        while True:
            user_input = input(f"{user_name}: ").strip()
            if user_input.lower() == "quit":
                bot.force_compress()
                break
            if user_input:
                print(f"{char_name}: {bot.reply(user_input)}\n")
        return

    # ── Telegram 模式 ──
    if connector_type == "telegram":
        from connectors.telegram_connector import TelegramConnector

        tg_cfg    = config.get("connector", {}).get("telegram", {})
        allowed   = tg_cfg.get("allowed_chat_ids", [])
        target_id = allowed[0] if allowed else None

        connector_holder: dict = {}

        def send_proactive(message: str):
            conn = connector_holder.get("conn")
            if conn and target_id:
                parts = [p.strip() for p in message.split("\\") if p.strip()]
                for i, part in enumerate(parts):
                    conn.tg.send_message(target_id, part)
                    if i < len(parts) - 1:
                        time.sleep(0.8)

        bg_cfg = config.get("llm", {}).get("background_model", {})
        bg_llm = LLMClient(
            provider = bg_cfg.get("provider", "deepseek"),
            model    = bg_cfg.get("model", "deepseek-chat"),
            api_key  = bg_cfg.get("api_key", "")
        )

        life_manager = LifeManager(
            data_dir   = "./data",
            llm_client = bg_llm,
            send_func  = send_proactive,
            config     = config,
        )

        bot = AIChanBot(config, character_state=char_state, life_manager=life_manager)
        life_manager.bot = bot

        def start_tg():
            conn = TelegramConnector(bot, config)
            connector_holder["conn"] = conn
            conn.run()

        tg_thread = threading.Thread(target=start_tg, daemon=True)
        tg_thread.start()
        time.sleep(3)

        delay     = minutes_until_next_hour()
        next_time = (datetime.now() + timedelta(seconds=delay)).strftime("%H:%M")
        print(f"\n[Life] 生活循环已启动，首次触发：{next_time}")
        t = threading.Timer(delay, schedule_life_tick, args=[life_manager])
        t.daemon = True
        t.start()

    print(f"\n{char_name} 已上线，进入24小时模式")
    print("按 Ctrl+C 停止\n")

    last_state_update = time.time()

    try:
        while True:
            sleep_state = char_state.state.get("sleep_state", "awake")
            if sleep_state == "sleeping":
                update_interval = 3 * 3600
            elif sleep_state in ("preparing_sleep", "waking_up"):
                update_interval = 10 * 60
            else:
                update_interval = 15 * 60

            if time.time() - last_state_update > update_interval:
                minutes = (time.time() - last_state_update) / 60.0
                char_state.update(minutes)
                last_state_update = time.time()
                print(f"[状态] {datetime.now().strftime('%H:%M')} "
                      f"{char_state.state['current_activity']}")

            time.sleep(30)

    except KeyboardInterrupt:
        print(f"\n保存记忆...")
        bot.force_compress()
        print("已退出")


if __name__ == "__main__":
    main()
