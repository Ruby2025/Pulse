# 迁移指南：bot.py / life.py / telegram_connector.py

> 这三个文件体量大(合计2000+行)，结构逻辑不变，改动主要是机械性的名称替换和接口适配。
> 按以下步骤操作，顺序不能乱。

---

## 第一步：全局字符串替换（三个文件都要做）

### 1.1 变量名 / 键名替换

| 旧 | 新 | 说明 |
|----|-----|------|
| `关系层_Ruby` | `关系层` | state_core 的键名 |
| `ruby_profile` | `user_profile` | 文件名和变量名 |
| `ruby_info` | `user_info` | prompt 变量 |
| `_ruby_sleeping` | `_user_sleeping` | life.py 变量 |
| `_ruby_sleep_ts` | `_user_sleep_ts` | life.py 变量 |
| `_is_ruby_sleeping` | `_is_user_sleeping` | life.py 方法 |
| `_detect_ruby_sleeping` | `_detect_user_sleeping` | life.py 方法 |
| `_load_ruby_profile` | `_load_user_profile` | bot.py 方法 |
| `_update_ruby_profile` | `_update_user_profile` | bot.py 方法 |
| `ruby_status` | `user_status` | life.py 变量 |
| `update_ruby_profile` | `update_user_profile` | prompts.json 的 key 引用 |
| `五轴内驱力` | `三轴状态` | state_core 的键名 |

### 1.2 字面量字符串替换

| 旧 | 新 | 出现位置 |
|----|-----|---------|
| `"Ruby"` (作为角色名出现在打印/注释中) | 从config读取 `user_name` | bot.py, life.py |
| `"秦彻"` (作为角色名出现在打印/注释中) | 从config读取 `char_name` | bot.py, life.py |
| `"ruby_profile.json"` | `"user_profile.json"` | bot.py |
| `Ruby已{hours_gap:.0f}小时未联系` | `用户已{hours_gap:.0f}小时未联系` | state.py(已改) |
| `f"Ruby: {user_message}` | `f"{self.user_name}: {user_message}` | bot.py |
| `f"秦彻: {bot_response}` | `f"{self.char_name}: {bot_response}` | bot.py |

---

## 第二步：bot.py 结构性改动

### 2.1 __init__ 中添加名称变量

在 `__init__` 开头添加：
```python
self.char_name = config.get("character", {}).get("name", "AI")
self.user_name = config.get("user", {}).get("name", "用户")
```

### 2.2 _compress_and_save 中的角色名

```python
# 旧
f"{'Ruby' if m['role']=='user' else '秦彻'}: {m['content']}"

# 新
f"{self.user_name if m['role']=='user' else self.char_name}: {m['content']}"
```

### 2.3 _build_system_prompt 中的注入

system prompt 中所有硬编码的角色设定需要改为从 config 读取：

```python
# 旧：直接写秦彻的人设
# 新：从 character.yaml 动态注入
char = self.config.get("character", {})
identity = char.get("core_personality", "")
style    = char.get("speaking_style", "")
```

### 2.4 get_state_summary 引用

bot.py 调用 `self.character_state.get_state_summary()` 不需要改，state.py 已经处理了。

### 2.5 growth_manager 接口

growth.py 已简化，去掉了 `record_confirm()` 方法。找到 `_record_growth_event` 方法：

```python
# 旧
def _record_growth_event(self, user_message, bot_response):
    ...
    self.growth_manager.record_event(convo, relation)
    self.growth_manager.check_thresholds()
    self.growth_manager.record_confirm(convo)   # ← 删除这行

# 新
def _record_growth_event(self, user_message, bot_response):
    ...
    self.growth_manager.record_event(convo, relation)
    self.growth_manager.check_thresholds()
```

### 2.6 关系层字段名

所有访问 `state_core["关系层_Ruby"]` 的地方改为 `state_core["关系层"]`。
字段名变更：
- `信任值` → `trust`
- `执念值` → 删除（不再使用）
- `情感依赖度` → 删除
- `不安全感` → 删除
- `控制欲` → 删除
- `LoveIndex` → 删除
- `当前阶段` → 保留
- `阶段名称` → 保留

### 2.7 react_to_event 事件名

state.py 已经改了事件名。bot.py 里调用的地方不需要改（react_to_event 内部做了映射）。

### 2.8 world_constraints 注入

_build_system_prompt 中读取 world_constraints 的地方，改为从 prompts.json 读取（已经是空字符串），代码逻辑不变，只是不再硬编码内容。

### 2.9 prompts.json 引用中的占位符

bot.py 中调用 prompt.format(...) 时，需要确保传入 `character_name` 和 `user_name`：

```python
# 在所有 self._prompts.get("xxx").format(...) 调用中添加：
character_name = self.char_name,
user_name      = self.user_name,
character_identity = self.config.get("character", {}).get("core_personality", ""),
character_interests = ", ".join(self.config.get("character", {}).get("interests", [])),
```

---

## 第三步：life.py 结构性改动

### 3.1 __init__ 签名变更

```python
# 旧
def __init__(self, data_dir, llm_client, send_func):

# 新
def __init__(self, data_dir, llm_client, send_func, config=None):
    ...
    self.config     = config or {}
    self.char_name  = self.config.get("character", {}).get("name", "AI")
    self.user_name  = self.config.get("user", {}).get("name", "用户")
```

### 3.2 AWAKE_START / AWAKE_END 替换

```python
# 旧（硬编码）
AWAKE_START = 17
AWAKE_END   = 11

# 新（从 config 读取）
from core.state import parse_schedule
wake_hour, sleep_hour = parse_schedule(self.config.get("character", {}))
self.wake_hour  = wake_hour
self.sleep_hour = sleep_hour
```

`is_awake()` 方法改为用 `get_phase_for_hour`：
```python
def is_awake(self, now=None):
    from core.state import get_phase_for_hour
    hour = (now or datetime.now()).hour
    _, sleep_state = get_phase_for_hour(hour, self.wake_hour, self.sleep_hour)
    return sleep_state != "sleeping"
```

### 3.3 ruby_profile.json → user_profile.json

`_send_longing_message` 方法中：
```python
# 旧
with open(os.path.join(self.data_dir, "ruby_profile.json"), ...) as f:
# 新
with open(os.path.join(self.data_dir, "user_profile.json"), ...) as f:
```

### 3.4 prompt format 调用添加占位符

所有 `self._prompts.get("xxx").format(...)` 调用添加：
```python
character_name = self.char_name,
user_name      = self.user_name,
```

### 3.5 life_tick prompt 中的 ruby_status

变量名已在 1.1 中替换为 `user_status`，检查 `_call_life_tick` 方法中的字符串构建，把所有 "Ruby" 字面量替换。

---

## 第四步：telegram_connector.py 改动

### 4.1 添加名称变量

```python
self.char_name = config.get("character", {}).get("name", "AI")
self.user_name = config.get("user", {}).get("name", "用户")
```

### 4.2 delay_scenarios 默认值

删除所有角色特定的场景描述，只保留从 character_config.json 读取的逻辑。

### 4.3 _should_quote 方法

引用检测的关键词列表（"你说/明明/哪有/瞎说"）应该移入 keywords.json 作为 `quote_trigger_keywords`。

### 4.4 _enrich_with_simulated_search

life.py 中这个方法有一处硬编码 `秦彻正在研究`，替换为 `{self.char_name}正在研究`。

---

## 第五步：验证清单

完成所有替换后，运行以下检查：

```bash
# 搜索残留的私人信息
grep -rn "秦彻" core/ connectors/ main.py config/ data/character/
grep -rn "Ruby" core/ connectors/ main.py config/ data/character/
grep -rn "67.216" .
grep -rn "guoqianru" .
grep -rn "aichan" .
grep -rn "7742496" .
grep -rn "8297551" .

# 搜索旧的五轴/五阶段引用
grep -rn "五轴内驱力" core/
grep -rn "关系层_Ruby" core/
grep -rn "执念值" core/
grep -rn "LoveIndex" core/
grep -rn "掌控轴" core/

# 验证 JSON
python3 -c "import json; [json.load(open(f)) for f in ['data/character/prompts.json','data/character/character_config.json','data/character/behavior_config.json','data/character/activity_pools.json','data/character/keywords.json','data/character/growth_config.json']]; print('All JSON OK')"

# 试运行
python3 main.py
```
