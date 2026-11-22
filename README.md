# 焊缝检验返修采录台

虚构钢结构焊缝的离线证据账。焊工资格/工艺批准、独立无损检验、返修复验与监造接收各有实体、版本和结论；晚到的资格冲突或超次返修不会抹去原始证据。CLI 输出当前决定，`report` 展示历史，`audit` 从原始采录文件重算。

```powershell
python -m pip install . --no-build-isolation --no-deps
python -m weldproof demo --db demo.sqlite
python -m weldproof audit --db demo.sqlite
python -m unittest discover -s tests -v
```

独立入口：`weld-import`、`ndt-import`、`repair-import`，各用 `--db state.sqlite --input fixtures/<weld|ndt|repair>_ok.json`；相应 `_bad.json` 展示未获资格、自检与超次返修。全部 fixture 合成，不接真实检验设备。

焊材来源入口：`lot-import` 采录封签货箱（`fixtures/lot_supplier_box.json`：一箱三件、有限件数联合约束、两条事前授权）与现场见证验收（`fixtures/lot_witness_acceptance.json`：一次剪签失联恢复、台面“A/B”记录核对、有见证内芯刻印、真实签领）；`lot-status` 给出当前所有联合解释、各卷仍可执行的观察与可续发的证明。确切炉批只来自原装卷盘刻印证据链，验池结论不能补写炉批；`trace_only` 状态下只采集来源做追踪，不能继续新领料。`audit` 重放含焊材采录的全部文件并比对账态。
