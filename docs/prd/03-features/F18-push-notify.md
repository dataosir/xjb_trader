# F18 · 观察池即时 Push（macOS / Bark）

## 1. 背景与目标

F16 邮件提醒适合留档与周报，但盘中「回踩就绪」需要**更快、更醒目**的到达方式。本功能在 F16 扫描逻辑不变的前提下，扩展 **macOS 本地弹窗** 与 **Bark iPhone Push**，与邮件并行、互不影响。

## 2. 用户故事 / 场景

- 用户在 Mac 前看盘：观察池标的回踩就绪 → 右上角弹窗 + 提示音，无需等邮件。  
- 用户离开电脑：Bark 推送到 iPhone 锁屏，比 SMTP 更稳、配置更简单（只需 Key）。  
- 邮件仍可用于留档；F17 周报继续只走邮件。

## 3. 功能范围

**In**

- 多通道 `notify.send_alert()`：`email` / `macos` / `bark`  
- 配置：`notify.macos.*`、`notify.bark.*`、`alert.channels`（空 = 自动推断已启用且配置齐全的通道）  
- CLI：`tea setup-notify`（macOS + Bark 引导）；`tea setup-notify --test` 仅测推送  
- 菜单：**配置与维护 → 即时提醒通道（macOS / Bark）**  
- `watch-alert` 满足条件时向所有活跃通道发送；**任一通道成功即记为已提醒**（去重 state 写入成功通道列表）  
- selftest：mock macOS/Bark，覆盖部分失败 / 全失败

**Out**

- 企业微信 / 钉钉 / Telegram（后续可再加 Webhook）  
- 自动下单  
- F17 周报走 Push（仍仅邮件）

## 4. 主流程

```
watch-alert 扫描到候选
    ↓
active_channels() → [macos, bark, email, ...]
    ↓
send_alert(subject, body) 逐通道发送
    ↓
任一 ok → mark_alert_sent；全失败 → 不写 state，下次重试
```

## 5. 关键配置键

| 键 | 默认 | 说明 |
|---|---|---|
| `notify.macos.enabled` | `true` | 仅 Darwin 有效 |
| `notify.macos.sound` | `default` | osascript 提示音 |
| `notify.bark.enabled` | `false` | 需填 key |
| `notify.bark.key` | `""` | Bark App 设备 Key |
| `notify.bark.server` | `https://api.day.app` | 可改自建服 |
| `notify.bark.group` | `TEA观察` | Bark 分组名 |
| `alert.channels` | `[]` | 非空则只用列出的通道 |

## 6. 代码锚点

- `tea/core/notify.py` · `send_alert` / `send_macos_notification` / `send_bark`  
- `tea/config/notify_setup.py` · `tea setup-notify`  
- `tea/runtime/runner.py` · `watch_alert`（已改用多通道）

## 7. 验收标准

- [x] macOS：Darwin 上 osascript 弹窗；非 macOS 跳过  
- [x] Bark：HTTP POST JSON 到 `{server}/{key}`  
- [x] 与 F16 邮件可并行；单通道失败不阻断其他通道  
- [x] selftest mock 全覆盖  
- [x] 零 pip 依赖（urllib + subprocess）

## 8. 与 F16 关系

F16 负责扫描时段、购买条件、去重；F18 只扩展**传输层**。邮件配置仍走 `tea setup-email`。
