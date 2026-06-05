# Pulse

### 给你的 AI 一个脉搏。

Pulse 是一个开源的 AI 伴侣框架。你可以用它创建一个有自己作息、情绪、记忆和成长轨迹的 AI 角色——不只是被动回复，而是会主动找你说话、会在发呆时自己研究感兴趣的东西、会因为你长时间不理它而想你。

它可以是你的恋人、朋友、家人，或者任何你定义的关系。运行在你自己的服务器上，数据完全由你掌控。

---

## 它和普通 AI 聊天有什么不同

大多数 AI 聊天产品是一个等待输入的对话框。Pulse 不是。它更像一个真的有自己生活的人碰巧和你保持着联系。

**主动消息** — 角色会在合适的时间主动找你。可能是在做某件事时想到了你，也可能是单纯过了几小时想聊聊。不是定时推送，是基于角色当前的状态、心情和对你的社交需求综合判断的结果。

**自己的生活** — 角色有完整的 24 小时生活节律。你不找它的时候，它在按自己的作息做自己的事：画画、看书、遛猫、工作……每小时系统会根据当前时段和状态随机抽取一个活动，这个活动反过来影响角色的精力和心情。

**回复延迟** — 真人不会秒回。角色如果正在「拳击训练」，消息会延迟 5-20 分钟才看到；如果在「睡觉」，可能半小时到一小时后才被吵醒。延迟时长由当前活动状态决定，不是固定值。

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
| 低气压 | 随机触发低情绪日，角色语气自然变化，不需要原因 |
| 重大事件检测 | 用户提到搬家/生病/崩溃等，自动进入高关注模式 48 小时 |
| 图片识别 | 通过 Gemini API 识别用户发送的图片（可选） |
| 引用回复 | 用户引用角色的话时，角色能看到被引用的内容 |

---

## 快速开始

### 环境要求

- Python 3.9+（Windows / macOS / Linux 均可）
- 一个 LLM API 密钥（DeepSeek / OpenAI / Claude / Gemini 任选）
- （可选）一个 Telegram Bot Token（用于 Telegram 部署）
- （可选）一台 VPS（用于 24 小时在线）

> **Windows 用户**：推荐从 [python.org](https://www.python.org/downloads/) 安装 Python，安装时勾选 "Add Python to PATH"。之后在 PowerShell 或 CMD 中操作即可。
>
> **macOS 用户**：自带 Python 或通过 `brew install python` 安装。
>
> **Linux 用户**：大多数发行版自带 Python 3，确认版本 `python3 --version` ≥ 3.9。

### 5 分钟本地测试

```bash
# 克隆项目
git clone https://github.com/yourname/Pulse.git
cd Pulse

# 安装依赖
pip install -r requirements.txt

# 配置 API Key
cp .env.example .env
# 编辑 .env，填入你的 LLM API Key：
# LLM_API_KEY=sk-xxxxxxxxxxxxxxxx
#
# Windows 没有 cp 命令？用这个：
# copy .env.example .env
# 然后用记事本打开 .env 编辑

# 启动（本地命令行模式）
python main.py
```

默认角色是 Aria（一个自由插画师）。你可以直接在命令行和她聊天，输入 `quit` 退出。

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
    allowed_chat_ids: [你的Chat ID]   # 限制只有你能聊
    emergency_keyword: ""              # 紧急关键词，触发立刻回复
    reset_keyword: ""                  # 重置暗语，清空上下文
```

### 第三步：本地测试

```bash
python main.py
```

确认 Telegram 能正常收发消息。

### 第四步：24 小时在线部署

本地测试通过后，你需要让程序 24 小时运行。以下是三种方案：

#### 方案 A：Linux VPS（推荐）

最稳定的方案。买一台最低配 VPS（1核1G 足够，月费 ≈ $5）。

```bash
# 上传项目到 VPS
scp -r Pulse/ root@你的服务器IP:/root/

# SSH 连接服务器
ssh root@你的服务器IP
cd /root/Pulse

# 安装依赖
pip install -r requirements.txt

# 创建 .env
cp .env.example .env
nano .env   # 填入 API Key

# 用 tmux 后台运行
tmux new -s pulse
python3 -u main.py 2>&1 | tee -a data/run.log
# 按 Ctrl+B 然后按 D 退出 tmux（程序继续运行）

# 之后想回来看：
tmux attach -t pulse
```

#### 方案 B：macOS 本地后台

如果你的 Mac 长期开机，可以直接后台跑：

```bash
cd /你的路径/Pulse

# 方法1：用 tmux（推荐）
brew install tmux    # 没装过的话先安装
tmux new -s pulse
python3 -u main.py 2>&1 | tee -a data/run.log
# Ctrl+B D 退到后台

# 方法2：用 nohup
nohup python3 -u main.py > data/run.log 2>&1 &
```

#### 方案 C：Windows 本地后台

```powershell
cd C:\你的路径\Pulse

# 方法1：直接开着 PowerShell 窗口跑（最简单）
python main.py

# 方法2：用 pythonw 后台跑（无窗口）
pythonw main.py

# 方法3：注册为 Windows 服务（高级，需要 nssm）
# 下载 nssm: https://nssm.cc/download
# nssm install Pulse "C:\Python3x\python.exe" "C:\你的路径\Pulse\main.py"
# nssm start Pulse
```

> **注意**：方案 B/C 依赖电脑不关机不休眠。如果需要 24/7 稳定运行，推荐方案 A。

### 日常维护

```bash
# 查看日志
tail -f data/run.log                          # Linux / macOS
# Windows: Get-Content data\run.log -Wait     # PowerShell

# 备份数据（在本地执行）
scp -r root@你的服务器IP:/root/Pulse/data/ ~/backup/$(date +%Y%m%d)/
# Windows: 直接复制 data 文件夹到别处

# 重启（VPS 上）
tmux attach -t pulse
# Ctrl+C 停止
python3 -u main.py 2>&1 | tee -a data/run.log
# Ctrl+B D 退出
```

---

## 定制你的角色

> 📖 完整指南见 [docs/character_guide.md](docs/character_guide.md)，包含一个可以直接丢给 AI 的角色生成 prompt——你只需要写一段角色描述，AI 帮你生成所有配置文件。

### character.yaml — 角色的灵魂

这是你唯一必须编辑的文件。定义角色是谁、怎么说话、什么作息、喜欢什么。

```yaml
character:
  name: "你的角色名"
  age: 25
  gender: "女"

  core_personality: |
    在这里写角色设定。越具体越好。
    包括：性格、背景故事、说话方式、内心特点。
    这段文字会注入所有 LLM 调用作为身份锚点。

  speaking_style: |
    语气、用词习惯、句式特点。
    关键规则：禁止动作描写，直接说话，像真人发消息。

  interests:
    - 兴趣1（会影响活动池和主动话题方向）
    - 兴趣2

  schedule:
    wake_up: "08:00"      # 起床时间
    sleep: "00:00"        # 睡觉时间

  relationship_type: "恋人"   # 恋人 / 朋友 / 家人 / 同事
```

**写好 `core_personality` 是最重要的事情。** 它决定了角色在所有场景下的表现。建议至少写 200 字，包含具体的性格特征、说话示例、禁止事项。

### activity_pools.json — 角色在做什么

定义角色在不同时段可能做的事情。系统每小时随机抽一个。

```json
{
  "mid_active": [
    {
      "text": "在咖啡馆画画",
      "base_weight": 4,
      "tags": ["画画", "咖啡"],
      "trigger_condition": {"energy": {"min": 35}},
      "axis_effects": {"mood": 6, "energy": -4},
      "outcome_variants": {
        "success": {"mood": 10},
        "fail": {"mood": -5}
      }
    }
  ]
}
```

时段名称（按活跃周期自动分配）：

| Phase | 含义 | 说明 |
|-------|------|------|
| `sleep` | 睡眠 | 只放「睡着了」之类的条目 |
| `waking` | 刚醒 | 起床相关活动 |
| `early_active` | 活跃前期 | 约占活跃时段前 20% |
| `mid_active` | 活跃中段 | 约占 30%，最适合丰富的活动 |
| `late_active` | 活跃后段 | 约占 20% |
| `winding_down` | 渐入尾声 | 约占 15% |
| `pre_sleep` | 准备睡 | 最后约 5% |

每个条目的字段：

| 字段 | 必填 | 说明 |
|------|------|------|
| `text` | ✅ | 活动描述，会注入 system prompt |
| `base_weight` | ✅ | 基础权重，越大越容易被抽到 |
| `tags` | 否 | 标签，用于关键词匹配加权 |
| `trigger_condition` | 否 | 触发条件，如 `{"energy": {"min": 50}}` |
| `axis_effects` | 否 | 对三轴的影响，如 `{"mood": 5, "energy": -3}` |
| `outcome_variants` | 否 | 结果变体效果 |

### prompts.json — LLM 提示词

一般不需要改结构，只需要微调语气。所有 prompt 使用 `{character_name}` 和 `{user_name}` 占位符，运行时自动替换。

如果你的角色有特殊的世界观约束（比如角色和用户不在同一个世界），在 `world_rules` 和 `world_constraints` 字段中填写。不填则为空，不会影响功能。

### character_config.json — 数值参数

三轴标签、关系阶段阈值、情绪分类关键词、延迟场景。大多数情况默认值就够用。你可能想改的：

- `axis_labels`：三轴的标签文字，比如一个暴躁角色的心情标签可以改成「亢奋/平常/烦躁/暴怒」
- `stage_thresholds`：关系阶段的描述文字
- `emotion_classify`：哪些用户消息算「撒娇」「生气」等
- `delay_scenarios`：延迟回复时的场景描述

### keywords.json — 关键词库

- `self_interest_topics`：角色会在发呆时自己去研究的话题
- `major_event_keywords`：触发高关注模式的关键词（用户说了这些词，角色会更频繁关心）
- `promise_keywords`：约定检测的触发词
- `personal_info_keywords`：用户个人信息检测的关键词

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
│   └── llm_client.py               # 多 LLM 统一调用
├── connectors/
│   └── telegram_connector.py        # Telegram 连接器
├── data/
│   └── character/                   # ★ 配置文件（你要编辑这些）
│       ├── character_config.json    # 数值参数
│       ├── behavior_config.json     # 行为参数
│       ├── prompts.json             # LLM 提示词模板
│       ├── activity_pools.json      # 活动库
│       ├── keywords.json            # 关键词
│       ├── growth_config.json       # 成长系统配置
│       └── character_profile_surface.json  # 表现层（自动更新）
├── .env                             # API 密钥（不要提交到 git）
├── requirements.txt
└── README.md
```

运行后自动生成的数据文件（在 `data/` 下）：

| 文件 | 内容 | 建议检查频率 |
|------|------|-------------|
| `user_profile.json` | 从对话中自动提取的用户信息 | 每周 |
| `active/active_memory.json` | 活跃记忆 | 每 1-2 周 |
| `active/core_memory.json` | 核心记忆（永不衰减） | 需要时 |
| `archive/archive_memory.json` | 归档记忆 | 基本不用看 |
| `promises.json` | 约定列表 | 每 3-5 天 |
| `topic_explorations.json` | 话题探索档案 | 每 3-5 天 |
| `world_bible.json` | 世界设定档案 | 每 1-2 周 |
| `social_network.json` | 角色的社会关系 | 需要时 |
| `growth/pressure_pool.json` | 积压池 | 每 3-5 天 |
| `growth/drift_log.json` | 性格漂移日志 | 好奇时看看 |
| `life_log.json` | 生活日志 | 调试时 |
| `character/state_current.json` | 当前状态快照 | 调试时 |
| `character/state_core.json` | 三轴 + 关系层数值 | 异常时 |

---

## 架构详解

### 三轴状态系统

角色的内在状态用三个独立轴表示，每个轴 0-100：

| 轴 | 影响 | 怎么变化 |
|----|------|---------|
| **energy** 精力 | 回复长度、是否愿意聊长内容、延迟回复概率 | 活动消耗，睡觉恢复 |
| **mood** 情绪 | 语气、主动发消息的意愿 | 活动影响，对话影响，随机低气压 |
| **social_need** 社交需求 | 想找用户聊天的欲望、思念触发敏感度 | 独处时上升，聊天后下降 |

LLM 永远不会看到数字。它看到的是：`心情：低落，精力：疲惫，社交需求：想找人聊`。

### 关系三阶段

| 阶段 | 名称 | 条件 |
|------|------|------|
| 0 | 陌生 | 默认 |
| 1 | 熟悉 | 亲密度 ≥ 30 且 信任值 ≥ 20 |
| 2 | 亲密 | 亲密度 ≥ 70 且 信任值 ≥ 50 |

关系事件（高质量对话、温柔互动、冲突、长时间不联系）会自动调整亲密度和信任值，阶段随之自动升降。每个阶段有不同的语气指导注入 system prompt。

### 记忆系统

借鉴了 Ombre Brain 的思路：

- **情感双坐标**：每条记忆带 valence（正面/负面）和 arousal（激动/平静），比单一维度更准确
- **加权检索**：主题相关性 × 4 + 情感共鸣 × 2 + 时间亲近 × 1.5 + 重要度 × 1
- **衰减公式**：`score = importance × (activation_count^0.3) × e^(-0.04×days) × emotion_weight`
- **pinned 记忆永不衰减**，resolved 记忆分数 ×0.05（深埋不删）
- **相似记忆自动合并**（替换不拼接，防止膨胀）

### 生活循环

```
每小时（±25分钟随机偏移）
  │
  ├→ life_tick: LLM 判断角色这一小时在做什么、要不要找用户
  │     ├→ 不发消息：记录活动日志
  │     └→ 要发消息：compose_proactive 生成消息内容
  │           ├→ 冷却检查（30分钟内发过就跳过）
  │           ├→ 每日上限检查（默认4条）
  │           ├→ 话题抑制检查（同类话题别重复催）
  │           └→ 通过所有检查 → 发送
  │
  ├→ 思念触发：每天随机1个时间点，发一条想念的消息
  │
  ├→ 发呆→学习：如果抽到「发呆」活动，延迟后自动研究话题
  │
  └→ 状态更新：三轴数值按活动效果变化
```

### 性格成长（积压池）

```
对话事件
  → LLM 分析是否对角色行为模式有影响
  → 有：写入积压池（方向 + 权重 + 次数）
  → 同方向事件不断累积
  → 累积权重 ≥ 30 或 次数 ≥ 15
  → 触发表现层渗透（LLM 生成微调）
  → 角色的称呼习惯、语气倾向等缓慢变化
```

---

## 支持的 LLM

在 `character.yaml` 中配置：

| Provider | model 示例 | 说明 |
|----------|-----------|------|
| `deepseek` | `deepseek-chat` | 推荐，性价比高，中文好 |
| `openai` | `gpt-4o` | 贵但稳定 |
| `claude` | `claude-sonnet-4-20250514` | 角色扮演能力强 |
| `gemini` | `gemini-2.5-flash` | 便宜，图片识别用 |

建议：主对话模型用好一点的（deepseek-chat 或 gpt-4o），后台模型（记忆压缩、事件检测）用便宜的（deepseek-chat flash 版）。

---

## Token 消耗估算

以每天和角色聊 50 轮为例：

| 来源 | 每日调用次数 | 单次 token | 日合计 |
|------|-------------|-----------|--------|
| 主回复 | ~50 | ~800 | ~40K |
| 记忆压缩 | ~10 | ~300 | ~3K |
| 生活循环 | ~18 | ~400 | ~7K |
| 事件检测（约定/话题/世界等） | ~50 | ~150 | ~7.5K |
| 主动消息生成 | ~4 | ~300 | ~1.2K |
| **日合计** | | | **~59K** |

用 DeepSeek 大约每天 ¥0.1-0.3（2026年6月价格）。三轴计算、衰减、阈值判断全部在 Python 里完成，零 token。

---

## 常见问题

**Q：角色回复了不存在的记忆/捏造了历史事件怎么办？**
system prompt 里有防捏造规则。如果仍然发生，检查 `prompts.json` 中的 `system_history_ban` 和 `reply_rules` 是否正确注入。这是 LLM 的通病，多强调几遍"记忆库里没有的不能说"会有改善。

**Q：角色的性格漂移了/越来越不像设定怎么办？**
检查 `data/character/character_profile_surface.json`，这是成长系统自动写入的。如果漂移方向不对，可以手动编辑或清空这个文件。`core_personality` 是不会被自动修改的。

**Q：主动消息太多/太少？**
编辑 `behavior_config.json` 中的 `proactive` 部分：`normal_daily_max`（每日上限）和 `normal_cooldown_minutes`（冷却时间）。

**Q：角色永远不睡觉/睡觉时间不对？**
检查 `character.yaml` 中的 `schedule.wake_up` 和 `schedule.sleep`。系统会自动计算哪些小时是睡眠时段。

**Q：怎么让角色忘记某条错误的记忆？**
编辑 `data/active/active_memory.json`，找到那条记忆，把 `resolved` 改为 `true`（会被深埋但不删除），或者直接删除整条。

**Q：可以同时给多个用户使用吗？**
当前版本设计为单用户。`allowed_chat_ids` 中可以填多个 ID，但所有用户共享同一个角色状态和记忆。多用户独立实例需要部署多份。

---

## License

MIT — 随便用，注明出处就好。
