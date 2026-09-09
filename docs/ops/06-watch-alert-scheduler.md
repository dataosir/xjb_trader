# 06 · 观察池盘中提醒调度（launchd）

> **分工**：本页管「观察池盘中每分钟扫描 + 邮件提醒」的机器侧安装；产品语义见 [`../prd/03-features/F16-watch-alert-notify.md`](../prd/03-features/F16-watch-alert-notify.md)；种子扫描见 [`05-seed-plan-scheduler.md`](05-seed-plan-scheduler.md)。

---

## 1. 方案说明

| 项 | 说明 |
|---|---|
| 触发频率 | **每 60 秒**（`StartInterval=60`） |
| 实际生效 | 命令内部守卫：**交易日 + 盘中**（09:30–11:30、13:00–15:00）且 `alert.enabled` + `notify.email.enabled` |
| 代码入口 | `python3 -m tea watch-alert`（一次性，非 daemon） |
| 与 F11 Out | 不做进程内 cron；外层 launchd 同方案 A |
| 纪律 | **只发邮件，不自动下单** |

---

## 2. 前置：配置邮箱

### 推荐：引导配置（163）

```bash
tea setup-email
```

向导会说明 163 授权码获取步骤，依次填写发件邮箱、授权码、收件人，并可发送测试邮件验证。

仅测试当前配置（不发向导）：

```bash
tea setup-email --test
```

菜单路径：**配置与维护 → 邮件提醒邮箱配置**。

### 手动：config set

```bash
tea config set alert.enabled true
tea config set notify.email.enabled true
tea config set notify.email.smtp_user yourname@163.com
tea config set notify.email.smtp_password '你的163授权码'
tea config set notify.email.from_addr yourname@163.com
tea config set notify.email.to_addrs '["yourname@163.com"]'
```

> 163 需在网页邮箱设置里开启 SMTP 并生成**授权码**（不是登录密码）。默认 `smtp_host`/`smtp_port` 已是 `smtp.163.com:465`。

可选：

```bash
tea config set alert.condition preflight_pass   # 默认 pullback_ready
tea setup-notify                                # macOS 弹窗 + Bark Push（F18）
tea setup-notify --test                         # 仅测 Push 通道
tea config set alert.include_eve false          # 不扫前夕观察轨
```

---

## 3. 安装（macOS）

```bash
cd /path/to/tea
chmod +x ops/*.sh
./ops/install-launchd-watch-alert.sh
```

验证：

```bash
launchctl list | grep com.tea.watch-alert
./ops/watch-alert-cron.sh          # 手动试跑（盘中 + 池非空 + 已配邮箱）
grep tea.alert logs/tea.log | tail -10
```

卸载：

```bash
./ops/uninstall-launchd-watch-alert.sh
```

### 环境变量

与 [`05-seed-plan-scheduler.md`](05-seed-plan-scheduler.md) 相同：`TEA_HOME`、`TEA_PYTHON`。

---

## 4. 日志与状态

| 路径 | 内容 |
|---|---|
| `logs/tea.log` | `tea.alert` 扫描摘要（skip / 待发 / 已发 / 失败） |
| `logs/watch-alert-cron.log` | wrapper 输出（可选） |
| `logs/launchd-watch-alert.stdout.log` | launchd stdout |
| `logs/launchd-watch-alert.stderr.log` | launchd stderr |
| `data/watch_alert_state.json` | 当日已发去重 |

### 查日志速查

```bash
grep "tea.alert" logs/tea.log | tail -20
cat data/watch_alert_state.json
```

---

## 5. 漏跑 / 误报补救

| 场景 | 动作 |
|---|---|
| 合盖错过提醒 | 盘中手动 `tea watch-alert` |
| 邮件未收到 | 查 `tea.log` ERROR；查 SMTP 配置；查 state 是否已 dedupe |
| 不想某只再提醒 | 观察池 `--rm` 或等次日 state 按日重置 |
| 非交易日 launchd 仍触发 | 正常；命令内 skip，exit 0 |
| 观察池空 | 每分钟 skip，无行情请求 |

---

## 6. 验收清单

- [ ] 邮箱配置完成且 `alert.enabled` + `notify.email.enabled` 均为 true  
- [ ] `install-launchd-watch-alert.sh` 成功，`launchctl list` 可见任务  
- [ ] 手动 `./ops/watch-alert-cron.sh` 盘中 exit 0，日志有 `tea.alert`  
- [ ] 测试池内标的满足条件时收到邮件，且正文含「不自动下单」  
- [ ] 同日同代码不重复发信  

---

## 7. 纪律链（不变）

```
seed-plan → watch_pool（观察轨）
     ↓
launchd 每分钟 watch-alert → 邮件提醒
     ↓
用户手动 eval / run（T+1 14:00–14:45 或演练 --any-time）
     ↓
pos-add / 引擎登记（仍不自动下单）
```
