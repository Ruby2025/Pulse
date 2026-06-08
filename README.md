# Pulse

### 给你的 AI 一个脉搏。

Pulse 是一个开源的 AI 伴侣框架。你可以用它创建一个有自己作息、情绪、记忆和成长轨迹的 AI 角色——不只是被动回复，而是会主动找你说话、会在发呆时自己研究感兴趣的东西、会因为你长时间不理它而想你。

它可以是你的恋人、朋友、家人，或者任何你定义的关系。运行在你自己的服务器上，数据完全由你掌控。

---

## 它和普通 AI 聊天有什么不同

大多数 AI 聊天产品是一个等待输入的对话框。Pulse 不是。它更像一个真的有自己生活的人碰巧和你保持着联系。

**主动消息** — 角色会在合适的时间主动找你。可能是在做某件事时想到了你，也可能是单纯过了几小时想聊聊。不是定时推送，是基于角色当前的状态、心情和对你的社交需求综合判断的结果。

**自己的生活** — 角色有完整的 24 小时生活节律。你不找它的时候，它在按自己的作息做自己的事。每小时系统会根据当前时段和状态随机抽取一个活动，这个活动反过来影响角色的精力和心情。

**回复延迟** — 真人不会秒回。角色如果正在忙，消息会延迟几分钟才看到；如果在睡觉，可能半小时到一小时后才被吵醒。延迟时长由当前活动状态决定，不是固定值。

**记忆** — 角色会记住你说过的话。重要的事情（你的生日、你的宠物叫什么、你们的约定）会长期保留；日常琐事会按照类似艾宾浩斯曲线的规则逐渐淡忘。记忆按情感强度加权：让角色印象深刻的瞬间衰减得更慢。

**性格成长** — 角色的表达方式会随着你们的互动缓慢变化。系统会分析每次对话对角色行为模式的影响，累积到一定程度就自动调整角色的表现层（称呼习惯、语气倾向等）。变化是渐进的，不是突变。

---

## 核心设计原则

```
┌─────────────────────────┐      ┌──────────────────────────┐
│     Python（零 token）    │      │     LLM（只负责表达）      │
│                          │      │                           │
│  三轴状态衰减计算         │      │  看到的是自然语言描述：     │
│  活动池加权随机抽取        │ ───→ │  「心情：低落，精力：疲惫， │
│  关系阶段自动判断         │      │   正在做：窝在沙发上看书」  │
│  记忆衰减 / 归档          │      │                           │
│  积压池累积 / 渗透        │      │  基于这些描述生成回复       │
│  生活循环调度             │      │  永远不看原始数字           │
└─────────────────────────┘      └──────────────────────────┘
```

Python 负责所有数值计算，零 token 消耗。LLM 只在需要生成自然语言时才被调用，且永远看不到原始数字——它只看到 Python 翻译后的状态描述。这样做的好处是：数值不会因为 LLM 的幻觉而漂移，token 成本可控。

---

## 系统能力一览

| 能力 | 说明 |
|------|------|
| 三轴状态引擎 | 精力 / 情绪 / 社交需求，每小时随活动自动变化 |
| 动态作息 | 从配置读取起床/睡觉时间，自动划分时段 |
| 活动池 | 按时段加权随机抽取活动，支持条件触发和结果变体 |
| 三阶段关系 | 陌生→熟悉→亲密，由信任度和亲密度驱动自动升级 |
| 分层记忆 | 活跃层 + 核心层 + 归档层，情感双坐标加权，相似记忆自动合并 |
| 主动消息 | 思念调度、分享发现、关心用户，每日次数和冷却时间可配 |
| 用户状态检测 | 自动检测用户说了「去睡了」「去忙」，切换为留言模式 |
| 话题抑制 | 同类催促话题 24 小时内不超过 2 次，避免令人烦躁 |
| 约定系统 | 自动检测对话中的承诺/赌约，跟踪完成状态 |
| 话题探索 | 自动记录用户透露的话题线索，后续主动延续 |
| 用户档案 | 自动从对话中提取用户的个人信息、喜好、禁忌 |
| 高光时刻 | 自动保存高情感强度的对话片段 |
| 世界设定 | 角色说过的世界细节自动捕捉并写入 world_bible |
| 社会网络 | 角色提到的人物自动进入关系网，后续对话保持一致 |
| 自主学习 | 角色发呆时会自动研究感兴趣的话题，之后分享给用户 |
| 性格成长 | 对话事件→积压池→表现层渗透，角色的表达方式缓慢演化 |
| 图片识别 | 支持直接看图（GPT-4o/Claude/Gemini）或独立视觉模型（Gemini 免费额度）|
| 记忆迁移 | 从 ChatGPT / Character.AI / 文本记录导入已有对话记忆 |

---

## 快速开始

### 环境要求

- Python 3.9+（Windows / macOS / Linux 均可）
- 一个 LLM API 密钥（DeepSeek / OpenAI / Claude / Gemini 任选）
- （可选）一个 Telegram Bot Token（用于 Telegram 部署）
- （可选）一台 VPS（用于 24 小时在线，最低 $5/月）

> **Windows 用户**：推荐从 [python.org](https://www.python.org/downloads/) 安装 Python，安装时勾选 "Add Python to PATH"。
>
> **macOS 用户**：自带 Python 或通过 `brew install python` 安装。
>
> **Linux 用户**：确认 `python3 --version` ≥ 3.9。

### 5 分钟本地测试

```bash
# 克隆项目
git clone https://github.com/Ruby2025/Pulse.git
cd Pulse

# 安装依赖
pip install -r requirements.txt

# 配置 API Key
cp .env.example .env
# 编辑 .env，填入你的 LLM API Key
# Windows: copy .env.example .env，用记事本编辑

# 启动（本地命令行模式）
python main.py
```

默认是空白模板角色。第一次运行前，你需要在 `config/character.yaml` 中定义你的角色。详见 [创建你的角色](docs/character_guide.md)。

---

## 创建你的角色

> 📖 完整指南见 [docs/character_guide.md](docs/character_guide.md)

**最快方式**：用指南里提供的 prompt 丢给任何 AI（ChatGPT / Claude / DeepSeek / Kimi），输入你的角色描述，一次性生成所有配置文件。

**手动方式**：编辑 `config/character.yaml`，至少填写 `name`、`core_personality`、`speaking_style` 三个字段。

---

## 从其他平台迁移

如果你已经在 ChatGPT、Character.AI 或其他平台有了聊天历史，可以把那些对话记忆导入 Pulse：

```bash
# 从 ChatGPT 导出文件导入
python tools/import_memory.py -i conversations.json -f chatgpt

# 从纯文本聊天记录导入
python tools/import_memory.py -i chat.txt -f text -u 你的名字 -c 角色名

# 从 Character.AI 导出导入
python tools/import_memory.py -i history.json -f characterai

# 先预览不写入
python tools/import_memory.py -i chat.txt -f text --dry-run

# 用 LLM 精细提炼（更准但消耗 token）
python tools/import_memory.py -i chat.txt -f text --use-llm
```

导入的记忆会自动进入角色的记忆系统，高重要度的记忆会被 pin 住不衰减。

---

## 部署到 Telegram

### 第一步：创建 Telegram Bot

1. 在 Telegram 中找到 [@BotFather](https://t.me/BotFather)
2. 发送 `/newbot`，按提示取名
3. 获得 Bot Token（格式：`123456789:ABCdefGHIjklMNOpqrsTUVwxyz`）
4. 给 bot 发一条消息，然后访问 `https://api.telegram.org/bot<你的token>/getUpdates`
5. 在返回的 JSON 中找到 `chat.id`，这是你的 Chat ID

### 第二步：修改配置

编辑 `config/character.yaml`：

```yaml
connector:
  type: "telegram"                    # 改为 telegram
  telegram:
    bot_token: "你的Bot Token"
    allowed_chat_ids: [你的Chat ID]   # 限制只有你能聊，留空允许所有人
```

### 第三步：本地测试

```bash
python main.py
```

确认 Telegram 能正常收发消息。

### 第四步：24 小时在线部署

本地测试通过后，你需要让程序 24 小时运行。以下是三种方案：

#### 方案 A：Linux VPS（推荐）

最稳定的方案。买一台最低配 VPS（1核512MB 足够，月费 ≈ $5）。

```bash
scp -r Pulse/ root@你的服务器IP:/root/
ssh root@你的服务器IP
cd /root/Pulse
pip install -r requirements.txt
cp .env.example .env && nano .env

tmux new -s pulse
python3 -u main.py 2>&1 | tee -a data/run.log
# Ctrl+B D 退到后台
```

#### 方案 B：macOS 本地后台

```bash
tmux new -s pulse
python3 -u main.py 2>&1 | tee -a data/run.log
# Ctrl+B D 退到后台
```

#### 方案 C：Windows 本地后台

```powershell
# 方法1：直接开着 PowerShell 窗口跑
python main.py

# 方法2：用 pythonw 后台跑（无窗口）
pythonw main.py
```

> **注意**：方案 B/C 依赖电脑不关机不休眠。24/7 稳定运行推荐方案 A。

### 日常维护

```bash
# 查看日志
tail -f data/run.log                          # Linux / macOS
# Windows: Get-Content data\run.log -Wait     # PowerShell

# 备份数据
scp -r root@你的服务器IP:/root/Pulse/data/ ~/backup/$(date +%Y%m%d)/
```

---

## 图片识别配置

Pulse 支持两种图片识别方式：

| 方案 | 配置 | 适用场景 |
|------|------|---------|
| 模型自带视觉 | `supports_vision: true` | GPT-4o、Claude、Gemini 等多模态模型 |
| 独立视觉模型 | `supports_vision: false` + `vision_model` | DeepSeek 等纯文本模型 + Gemini 看图 |

**如果你用 DeepSeek 又想识别图片**，推荐用 Google Gemini 作为视觉模型——有免费额度（每天约 500 次），在 `character.yaml` 中配置：

```yaml
llm:
  chat_model:
    provider: "deepseek"
    model: "deepseek-chat"
    api_key: "env:LLM_API_KEY"
    supports_vision: false

  vision_model:
    provider: "gemini"
    model: "gemini-3.1-flash-lite"
    api_key: "env:GOOGLE_API_KEY"
```

Gemini API Key 免费申请：https://aistudio.google.com/apikey

如果你用 GPT-4o 或 Claude，直接设 `supports_vision: true`，不需要 `vision_model`。

---

## 文件结构

```
Pulse/
├── main.py                          # 启动入口
├── config/
│   └── character.yaml               # ★ 角色设定（你要编辑这个）
├── core/
│   ├── bot.py                       # 主回复逻辑、记忆压缩、对话衍生
│   ├── state.py                     # 三轴状态引擎、关系层、活动池
│   ├── life.py                      # 生活循环、主动消息、学习链路
│   ├── memory.py                    # 分层记忆、衰减归档、情感加权检索
│   ├── growth.py                    # 积压池→表现层渗透
│   └── llm_client.py               # 多 LLM 统一调用（含视觉路由）
├── connectors/
│   └── telegram_connector.py        # Telegram 连接器
├── tools/
│   └── import_memory.py             # 记忆迁移工具
├── data/
│   └── character/                   # ★ 配置文件（你要编辑这些）
│       ├── character_config.json    # 数值参数
│       ├── behavior_config.json     # 行为参数
│       ├── prompts.json             # LLM 提示词模板
│       ├── activity_pools.json      # 活动库
│       ├── keywords.json            # 关键词
│       ├── growth_config.json       # 成长系统配置
│       └── character_profile_surface.json  # 表现层（自动更新）
├── docs/
│   └── character_guide.md           # 角色创建指南
├── .env                             # API 密钥（不会上传 git）
└── .env.example                     # API 密钥模板
```

---

## 支持的 LLM

| Provider | model 示例 | 视觉能力 | 说明 |
|----------|-----------|---------|------|
| `deepseek` | `deepseek-chat` | ❌ 需配 vision_model | 推荐，性价比高，中文好 |
| `openai` | `gpt-4o` | ✅ 自带 | 设 `supports_vision: true` |
| `claude` | `claude-sonnet-4-20250514` | ✅ 自带 | 角色扮演能力强 |
| `gemini` | `gemini-3.1-flash` | ✅ 自带 | 有免费额度 |

---

## 系统要求

| 项目 | 最低配置 | 说明 |
|------|---------|------|
| **电脑** | 任何能跑 Python 的电脑 | Pulse 自身只占约 50MB 内存 |
| **VPS** | 1核 / 512MB / 5GB | 最低 $5/月，推荐 Ubuntu 22.04 |
| **Python** | 3.9+ | 唯一硬性要求 |
| **网络** | 能访问 LLM API + Telegram API | 国内需注意网络环境 |

Pulse 对硬件几乎没有要求——所有重活（LLM 推理）都在 API 那边，本地只做调度和 JSON 读写。十年前的笔记本也能流畅运行。

---

## Token 消耗估算

以每天和角色聊 50 轮为例：

| 来源 | 每日调用次数 | 单次 token | 日合计 |
|------|-------------|-----------|--------|
| 主回复 | ~50 | ~800 | ~40K |
| 记忆压缩 | ~10 | ~300 | ~3K |
| 生活循环 | ~18 | ~400 | ~7K |
| 事件检测 | ~50 | ~150 | ~7.5K |
| 主动消息 | ~4 | ~300 | ~1.2K |
| **日合计** | | | **~59K** |

用 DeepSeek 大约每天 ¥0.1-0.3。三轴计算、衰减、阈值判断全部在 Python 里完成，零 token。

---

## 常见问题

**Q：角色回复了不存在的记忆怎么办？**
system prompt 里有防捏造规则。如果仍然发生，检查 `prompts.json` 中的 `system_history_ban` 和 `reply_rules`。多强调"记忆库里没有的不能说"会有改善。

**Q：角色的性格漂移了怎么办？**
检查 `data/character/character_profile_surface.json`，这是成长系统自动写入的。漂移方向不对可以手动编辑或清空。`core_personality` 不会被自动修改。

**Q：主动消息太多/太少？**
编辑 `behavior_config.json` 中的 `proactive` 部分：`normal_daily_max`（每日上限）和 `normal_cooldown_minutes`（冷却时间）。

**Q：角色永远不睡觉？**
检查 `character.yaml` 中的 `schedule.wake_up` 和 `schedule.sleep`。

**Q：怎么让角色忘记某条错误的记忆？**
编辑 `data/active/active_memory.json`，找到那条记忆删除或把 `resolved` 改为 `true`。

**Q：可以同时给多个用户使用吗？**
当前版本设计为单用户。多用户需要部署多份实例。

---

## License

MIT
