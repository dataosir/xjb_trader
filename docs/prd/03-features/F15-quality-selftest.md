# F15 · 质量 / 自测 / CI

## 1. 背景与目标

项目正确性防线是 **selftest：断言内按规格独立重算，再与引擎比对**。CI 多平台矩阵保证可移植；运行时保持零强依赖。

## 2. 用户故事 / 场景

- 改公式后：先改断言期望，再改实现，最后 `tea selftest` 全绿。  
- PR 前：`selftest` + `ruff` + `compileall`。  
- 贡献者不接受「把断言改成当前输出」的假绿。

## 3. 功能范围

**In**

- `tea/selftest.py` 离线全链路  
- 临时 `TEA_HOME` 沙箱，不碰真实 data/reports  
- GitHub Actions：多 OS × Python 3.8–3.13、lint、构包  
- 颜色语义锁、门禁/评分/筛选关键用例  

**Out**

- 用线上真实成交做 CI（不稳定且泄密）  
- 引入重型测试框架作为运行时依赖  

## 4. 主流程与边界

1. 构造行情与配置。  
2. 调引擎函数。  
3. 用规格重算期望 → assert。  

**边界**：分层依赖单向；CLI 无交易逻辑；原子写。

## 5. 关键配置键

自测可临时改 cfg；默认以 DEFAULTS 为准。无单独产品键。

## 6. 代码锚点

- `tea/selftest.py`  
- `.github/workflows/ci.yml`  
- `docs/tech/00-engineering-standards.md`  
- CLI：`tea selftest [--quiet]`

## 7. 验收标准

- [ ] `python -m tea selftest` 全绿  
- [ ] CI badge 对应工作流通过  
- [ ] 任何公式/阈值/门禁变更有对应断言增减  
- [ ] 不引入强制第三方运行时依赖  

## 8. 已知缺口 / 待迭代

- README 自测计数可能滞后于真实用例数，以命令输出为准  
- 打包（PyInstaller）属分发路径，需在目标 OS 本地构建  
