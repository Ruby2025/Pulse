#!/usr/bin/env python3
"""
tools/import_memory.py ── Pulse 记忆迁移工具
从其他 AI 聊天平台的导出数据导入到 Pulse 的记忆系统

支持格式：
  1. ChatGPT 导出（JSON）
  2. 纯文本聊天记录（txt）
  3. CSV 格式
  4. Character.AI 导出

用法：
  python tools/import_memory.py --input chat_export.json --format chatgpt
  python tools/import_memory.py --input chat.txt --format text
  python tools/import_memory.py --input chat.csv --format csv
  python tools/import_memory.py --input history.json --format characterai

可选参数：
  --user-name    用户名（用于识别谁是谁）
  --char-name    角色名
  --use-llm      使用 LLM 提炼记忆（更精准但消耗 token）
  --dry-run      只预览不写入
"""

import argparse
import csv
import json
import os
import re
import sys
from datetime import datetime
from typing import List, Dict, Optional

# 添加项目根目录到路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def parse_chatgpt_export(filepath: str) -> List[Dict]:
    """解析 ChatGPT 导出的 JSON（conversations.json）"""
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)

    messages = []
    conversations = data if isinstance(data, list) else [data]

    for convo in conversations:
        mapping = convo.get("mapping", {})
        for node_id, node in mapping.items():
            msg = node.get("message")
            if not msg or not msg.get("content"):
                continue
            parts = msg["content"].get("parts", [])
            text = " ".join(str(p) for p in parts if isinstance(p, str)).strip()
            if not text:
                continue
            role = msg.get("author", {}).get("role", "unknown")
            ts = msg.get("create_time")
            messages.append({
                "role": "user" if role == "user" else "assistant",
                "content": text,
                "timestamp": datetime.fromtimestamp(ts).isoformat() if ts else "",
            })
    return messages


def parse_text_export(filepath: str, user_name: str = "", char_name: str = "") -> List[Dict]:
    """
    解析纯文本聊天记录
    支持格式：
      用户名: 消息内容
      [时间] 用户名: 消息内容
      用户名 (HH:MM): 消息内容
    """
    with open(filepath, encoding="utf-8") as f:
        lines = f.readlines()

    messages = []
    # 自动检测分隔模式
    patterns = [
        # [2024-01-01 12:00] Name: message
        re.compile(r'\[([^\]]+)\]\s*([^:：]+)[：:]\s*(.+)'),
        # Name (12:00): message
        re.compile(r'([^(（]+)[（(]([^)）]+)[）)][：:]\s*(.+)'),
        # Name: message
        re.compile(r'^([^:：]{1,20})[：:]\s*(.+)'),
    ]

    for line in lines:
        line = line.strip()
        if not line:
            continue

        matched = False
        for i, pattern in enumerate(patterns):
            m = pattern.match(line)
            if m:
                if i == 0:  # [timestamp] name: msg
                    ts_str, name, text = m.group(1), m.group(2).strip(), m.group(3).strip()
                elif i == 1:  # name (time): msg
                    name, ts_str, text = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
                else:  # name: msg
                    name, text = m.group(1).strip(), m.group(2).strip()
                    ts_str = ""

                is_user = False
                if user_name and user_name.lower() in name.lower():
                    is_user = True
                elif char_name and char_name.lower() in name.lower():
                    is_user = False
                elif name.lower() in ["我", "me", "user", "you"]:
                    is_user = True

                messages.append({
                    "role": "user" if is_user else "assistant",
                    "content": text,
                    "timestamp": ts_str,
                    "speaker": name,
                })
                matched = True
                break

        if not matched and messages:
            # 续行：追加到上一条消息
            messages[-1]["content"] += "\n" + line

    return messages


def parse_csv_export(filepath: str) -> List[Dict]:
    """解析 CSV 格式（需要有 role/sender 和 content/message 列）"""
    with open(filepath, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        messages = []
        for row in reader:
            role = row.get("role") or row.get("sender") or row.get("from") or ""
            content = row.get("content") or row.get("message") or row.get("text") or ""
            ts = row.get("timestamp") or row.get("time") or row.get("date") or ""
            if content.strip():
                is_user = role.lower() in ["user", "human", "me"]
                messages.append({
                    "role": "user" if is_user else "assistant",
                    "content": content.strip(),
                    "timestamp": ts,
                })
    return messages


def parse_characterai_export(filepath: str) -> List[Dict]:
    """解析 Character.AI 导出的 JSON"""
    with open(filepath, encoding="utf-8") as f:
        data = json.load(f)

    messages = []
    # Character.AI 的格式可能是 histories -> histories -> msgs
    histories = data.get("histories", {}).get("histories", [])
    if not histories:
        # 尝试扁平格式
        histories = data if isinstance(data, list) else [data]

    for history in histories:
        msgs = history.get("msgs", history.get("messages", []))
        for msg in msgs:
            text = msg.get("text", msg.get("content", "")).strip()
            if not text:
                continue
            is_human = msg.get("is_human", msg.get("role") == "user")
            messages.append({
                "role": "user" if is_human else "assistant",
                "content": text,
                "timestamp": msg.get("timestamp", msg.get("created_at", "")),
            })
    return messages


def chunk_messages(messages: List[Dict], chunk_size: int = 20) -> List[List[Dict]]:
    """将消息列表分成固定大小的块"""
    return [messages[i:i+chunk_size] for i in range(0, len(messages), chunk_size)]


def extract_memories_simple(messages: List[Dict],
                            user_name: str, char_name: str) -> List[Dict]:
    """简单模式：从聊天记录中提取关键信息作为记忆（不用 LLM）"""
    memories = []
    important_patterns = [
        # 个人信息
        (r'我(?:叫|是|名字).{1,10}', 8, "个人信息"),
        (r'我(?:住在|在).{2,15}', 7, "个人信息"),
        (r'我(?:喜欢|爱|讨厌|害怕).{2,20}', 6, "偏好"),
        (r'我(?:养了|有一只|有一个).{2,15}', 7, "个人信息"),
        (r'我的(?:工作|职业|专业).{2,15}', 7, "个人信息"),
        (r'我(?:\d{1,2}岁|今年\d{1,2})', 7, "个人信息"),
        # 约定
        (r'(?:说好了|答应|约定|打赌|一言为定).{2,30}', 8, "约定"),
        # 重大事件
        (r'(?:搬家|毕业|结婚|分手|离职|生病|手术|怀孕).{0,20}', 9, "重大事件"),
        # 情感表达
        (r'(?:我爱你|喜欢你|想你|对不起|谢谢你).{0,20}', 7, "情感"),
    ]

    for msg in messages:
        if msg["role"] != "user":
            continue
        content = msg["content"]
        for pattern, importance, mem_type in important_patterns:
            match = re.search(pattern, content)
            if match:
                # 取匹配位置前后的上下文
                start = max(0, match.start() - 10)
                end = min(len(content), match.end() + 30)
                snippet = content[start:end].strip()
                memory_text = f"{user_name}说过：{snippet}"
                memories.append({
                    "content": memory_text,
                    "importance": importance,
                    "memory_type": mem_type,
                    "timestamp": msg.get("timestamp", datetime.now().isoformat()),
                })
                break  # 每条消息只提取一条记忆

    return memories


def extract_memories_with_llm(messages: List[Dict],
                              user_name: str, char_name: str) -> List[Dict]:
    """LLM 模式：分块发给 LLM 提炼记忆"""
    from core.llm_client import LLMClient
    from dotenv import load_dotenv
    load_dotenv()

    import yaml
    try:
        with open("config/character.yaml", encoding="utf-8") as f:
            config = yaml.safe_load(f)
        bg_cfg = config.get("llm", {}).get("background_model", {})
    except:
        bg_cfg = {"provider": "deepseek", "model": "deepseek-chat", "api_key": "env:LLM_API_KEY"}

    llm = LLMClient(
        provider=bg_cfg.get("provider", "deepseek"),
        model=bg_cfg.get("model", "deepseek-chat"),
        api_key=bg_cfg.get("api_key", ""),
    )

    chunks = chunk_messages(messages, chunk_size=30)
    all_memories = []

    for i, chunk in enumerate(chunks):
        convo_text = "\n".join([
            f"{'用户' if m['role']=='user' else '角色'}: {m['content']}"
            for m in chunk
        ])

        prompt = f"""从以下聊天记录中提取值得长期记住的内容。

聊天记录：
{convo_text[:2000]}

要求：
- 提取 1-5 条最重要的信息
- 每条带明确主语：「用户告诉角色……」或「角色答应了……」
- 只记录：重要的个人信息、偏好、约定、情感事件、重大生活事件
- 不记录日常寒暄和无意义闲聊

输出JSON数组，每条格式：
[{{"content": "记忆内容", "importance": 5到9的数字, "type": "类型"}}]

类型选项：个人信息/偏好/约定/情感/重大事件

如果没有值得记录的，输出：[]
只输出JSON。"""

        try:
            raw = llm.chat(
                messages=[{"role": "user", "content": prompt}],
                system_prompt="", max_tokens=500, temperature=0.2,
            )
            cleaned = re.sub(r'```(?:json)?|```', '', raw).strip()
            result = json.loads(cleaned)
            if isinstance(result, list):
                for item in result:
                    if item.get("content"):
                        all_memories.append({
                            "content": item["content"],
                            "importance": item.get("importance", 6),
                            "memory_type": item.get("type", "导入记忆"),
                            "timestamp": chunk[0].get("timestamp", "") if chunk else "",
                        })
            print(f"  [块 {i+1}/{len(chunks)}] 提取了 {len(result) if isinstance(result, list) else 0} 条记忆")
        except Exception as e:
            print(f"  [块 {i+1}/{len(chunks)}] 提取失败: {e}")

    return all_memories


def save_memories(memories: List[Dict], data_dir: str = "./data"):
    """将提取的记忆写入 Pulse 的记忆系统"""
    active_path = os.path.join(data_dir, "active", "active_memory.json")
    os.makedirs(os.path.dirname(active_path), exist_ok=True)

    existing = []
    if os.path.exists(active_path):
        try:
            with open(active_path, encoding="utf-8") as f:
                existing = json.load(f)
        except:
            existing = []

    from core.memory import extract_keywords, estimate_emotion_coords

    for mem in memories:
        content = mem["content"]
        keywords = extract_keywords(content)
        valence, arousal = estimate_emotion_coords(content)

        ts = mem.get("timestamp", "")
        if not ts:
            ts = datetime.now().isoformat()
        elif not re.match(r'\d{4}-\d{2}-\d{2}', ts):
            ts = datetime.now().isoformat()

        entry = {
            "content": content,
            "keywords": keywords,
            "valence": valence,
            "arousal": arousal,
            "timestamp": ts,
            "importance": mem.get("importance", 6),
            "activation_count": 1,
            "resolved": False,
            "pinned": mem.get("importance", 6) >= 8,
            "memory_type": mem.get("memory_type", "导入记忆"),
        }
        existing.append(entry)

    with open(active_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    return len(memories)


def save_user_profile_from_memories(memories: List[Dict], data_dir: str = "./data"):
    """从导入的记忆中提取用户档案信息"""
    profile_path = os.path.join(data_dir, "user_profile.json")
    try:
        with open(profile_path, encoding="utf-8") as f:
            profile = json.load(f)
    except:
        profile = {"基本信息": {}, "喜好": {}, "禁忌": {}, "重要约定": [], "关系里程碑": []}

    for mem in memories:
        content = mem.get("content", "")
        mem_type = mem.get("memory_type", "")

        if mem_type == "约定" or "约定" in content or "答应" in content:
            if content not in profile.get("重要约定", []):
                profile.setdefault("重要约定", []).append(content)
        elif mem_type == "重大事件":
            if content not in profile.get("关系里程碑", []):
                profile.setdefault("关系里程碑", []).append(content)

    with open(profile_path, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Pulse 记忆迁移工具")
    parser.add_argument("--input", "-i", required=True, help="输入文件路径")
    parser.add_argument("--format", "-f", required=True,
                        choices=["chatgpt", "text", "csv", "characterai"],
                        help="输入文件格式")
    parser.add_argument("--user-name", "-u", default="用户", help="用户名（用于识别）")
    parser.add_argument("--char-name", "-c", default="角色", help="角色名（用于识别）")
    parser.add_argument("--use-llm", action="store_true", help="使用 LLM 提炼记忆（更精准）")
    parser.add_argument("--dry-run", action="store_true", help="只预览，不写入")
    parser.add_argument("--data-dir", default="./data", help="Pulse 数据目录")
    args = parser.parse_args()

    print("=" * 50)
    print("  Pulse 记忆迁移工具")
    print("=" * 50)
    print(f"  输入: {args.input}")
    print(f"  格式: {args.format}")
    print(f"  模式: {'LLM 提炼' if args.use_llm else '关键词匹配'}")
    print()

    # 解析输入文件
    print("📖 读取聊天记录...")
    if args.format == "chatgpt":
        messages = parse_chatgpt_export(args.input)
    elif args.format == "text":
        messages = parse_text_export(args.input, args.user_name, args.char_name)
    elif args.format == "csv":
        messages = parse_csv_export(args.input)
    elif args.format == "characterai":
        messages = parse_characterai_export(args.input)
    else:
        print(f"❌ 不支持的格式: {args.format}")
        return

    print(f"   找到 {len(messages)} 条消息")
    if not messages:
        print("❌ 没有找到可解析的消息")
        return

    user_msgs = sum(1 for m in messages if m["role"] == "user")
    char_msgs = sum(1 for m in messages if m["role"] == "assistant")
    print(f"   用户消息: {user_msgs} 条，角色消息: {char_msgs} 条")
    print()

    # 提取记忆
    print("🧠 提取记忆...")
    if args.use_llm:
        memories = extract_memories_with_llm(messages, args.user_name, args.char_name)
    else:
        memories = extract_memories_simple(messages, args.user_name, args.char_name)

    print(f"   提取了 {len(memories)} 条记忆")
    print()

    # 预览
    print("📋 记忆预览（前 10 条）：")
    for i, mem in enumerate(memories[:10]):
        importance = mem.get("importance", 5)
        stars = "★" * min(importance, 5)
        print(f"   {i+1}. [{stars}] {mem['content'][:60]}...")
    if len(memories) > 10:
        print(f"   ... 还有 {len(memories) - 10} 条")
    print()

    if args.dry_run:
        print("🏁 预览模式，不写入文件。去掉 --dry-run 参数执行实际导入。")
        return

    # 写入
    print("💾 写入 Pulse 记忆系统...")
    count = save_memories(memories, args.data_dir)
    save_user_profile_from_memories(memories, args.data_dir)
    print(f"   ✅ 成功导入 {count} 条记忆")
    print()
    print("🎉 完成！启动 Pulse 后角色会记住这些内容。")
    print("   提示：导入的高重要度记忆（≥8）已自动 pin，不会衰减。")


if __name__ == "__main__":
    main()
