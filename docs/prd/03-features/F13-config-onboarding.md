# F13 · 配置与向导

## 1. 背景与目标

全部可调阈值集中在一份 JSON（约 410 参数），点号路径读写；首次启动向导收集因人而异项，其余默认。私有配置不进公开仓库。

## 2. 用户故事 / 场景

- 首次进菜单自动 `setup`。  
- `config set strategy.pass_threshold 7`。  
- `setup --defaults` 沙箱一键默认。  
- `TEA_HOME` 隔离多账户数据目录。

## 3. 功能范围

**In**

- `Config` 加载/保存/迁移兼容  
- `DEFAULTS` 单一真相源  
- 向导问项（资金、选股风控、板块权限、数据源链等）  
- `list/get/set/reset/count`  

**Out**

- 把密钥提交 git  
- 在业务代码硬编码阈值绕过配置  

## 4. 主流程与边界

1. 启动读配置；无则默认；未 initialized → 向导。  
2. set 后原子写。  
3. 新参数：DEFAULTS → 模块读取 → selftest →（若影响决策）README/PRD。  

**边界**：`tea_config.json` 含本金等，必须 gitignore。

## 5. 关键配置键

| 键 | 用途 |
|---|---|
| `meta.initialized` | 是否已向导 |
| 全域 `DEFAULTS` | `tea/config/config_store.py` |

## 6. 代码锚点

- `tea/config/config_store.py`  
- `tea/config/onboarding.py`  
- CLI：`setup` / `config`

## 7. 验收标准

- [ ] 点号路径读写正确（嵌套 list/dict）  
- [ ] 新策略开关有默认值且可一键回滚  
- [ ] selftest 覆盖关键默认与迁移  
- [ ] 加参顺序符合 CONTRIBUTING  

## 8. 已知缺口 / 待迭代

- README 参数总数随 DEFAULTS 变化需偶尔核对  
- 向导问项过少/过多时按一人公司需求微调，避免劝退  
