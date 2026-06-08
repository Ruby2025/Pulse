#!/usr/bin/env python3
"""
工具脚本：给 telegram_connector.py 打视觉路由补丁
运行方式：python3 tools/patch_vision.py
"""
import re

filepath = "connectors/telegram_connector.py"

with open(filepath, encoding="utf-8") as f:
    code = f.read()

# ── 补丁 1：在 __init__ 末尾添加 vision 相关属性 ──
old_init_end = '''        self._major_event_keywords: list = _load_keywords().get("major_event_keywords", [
            "搬家", "生病", "发烧", "哭了", "崩溃"
        ])'''

new_init_end = '''        self._major_event_keywords: list = _load_keywords().get("major_event_keywords", [
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
            print(f"[视觉] chat_model 自带视觉能力，图片直接发送")'''

if old_init_end in code:
    code = code.replace(old_init_end, new_init_end)
    print("✅ 补丁 1: __init__ 视觉属性 - 已应用")
else:
    print("⚠️  补丁 1: 未找到匹配位置，请手动添加")

# ── 补丁 2：修改图片处理逻辑 ──
old_image_handling = '''        # 图片处理
        image_base64, image_caption = self._get_image_from_message(message)
        if image_base64:
            description = self.bot.chat_llm.describe_image(image_base64, image_caption)
            if text:
                text = f"{text}\\n[图片内容：{description}]"
            elif image_caption:
                text = f"{image_caption}\\n[图片内容：{description}]"
            else:
                text = f"[图片内容：{description}]"
            print(f"[图片] 已转换为文字描述")'''

new_image_handling = '''        # 图片处理
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
                    text = f"{text}\\n[图片内容：{description}]"
                elif image_caption:
                    text = f"{image_caption}\\n[图片内容：{description}]"
                else:
                    text = f"[图片内容：{description}]"
                image_base64 = None  # 已转为文字，不再传图片给 bot
                print(f"[图片] 已通过 vision_model 转换为文字描述")'''

if old_image_handling in code:
    code = code.replace(old_image_handling, new_image_handling)
    print("✅ 补丁 2: 图片处理路由 - 已应用")
else:
    print("⚠️  补丁 2: 未找到匹配位置，请手动修改")

with open(filepath, "w", encoding="utf-8") as f:
    f.write(code)

print("\n完成！请更新 config/character.yaml 添加 supports_vision 和 vision_model 配置。")
