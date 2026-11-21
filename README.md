# 焊缝检验返修采录台

虚构钢结构焊缝的离线证据账。焊工资格/工艺批准、独立无损检验、返修复验与监造接收各有实体、版本和结论；晚到的资格冲突或超次返修不会抹去原始证据。CLI 输出当前决定，`report` 展示历史，`audit` 从原始采录文件重算。

```powershell
python -m pip install . --no-build-isolation --no-deps
python -m weldproof demo --db demo.sqlite
python -m weldproof audit --db demo.sqlite
python -m unittest discover -s tests -v
```

独立入口：`weld-import`、`ndt-import`、`repair-import`，各用 `--db state.sqlite --input fixtures/<weld|ndt|repair>_ok.json`；相应 `_bad.json` 展示未获资格、自检与超次返修。全部 fixture 合成，不接真实检验设备。

## 工牌号跨册独立性

车间工牌(W-*)与实验室牌(I-*)是两套编号，`badge-import` 采录合成原件：门禁候选全集、见证非同一、版次内固定配对、换班重发半开区间、两种实名核证委托（全班名册 / 单报告联合见证回执）及回执撤回。求解器枚举同一班内全部相容解释——未记录候选全集时只判待核、绝不补猜；重发时刻未知时所有报告共享同一时序世界；权威原件连一个共同解释都没有时按报告分组给出互不相容的原件。

- 双方实名核证（`kind=roster`，允许名册出口）需全班完整覆盖才可承接跨报告推导；
- 单报告联合见证（`kind=witness`，数据出口只放行回执）只签限该报告的身份关系，不能借给另一份报告，且该委托下拒绝名册原件落库；
- 声明外部名册的检验原件（带 `roster_epoch`/`roster_doc`）以独立性凭据替代原字符串比较，凭据缺失即挂起；无跨册约定的老报告沿用原入口，技术检测的长度、阈值与缺陷定位不变。

```powershell
python -m weldproof badge-import --db state.sqlite --input fixtures/badge_edition.json
python -m weldproof badge-query --db state.sqlite --epoch night-shift --doc D1
```

一班限定两次发放、五张牌、三个报告；不使用密码学或网络服务，角色与签认全部由合成原件明确。
