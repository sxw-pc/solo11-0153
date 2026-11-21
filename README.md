# 焊缝检验返修采录台

虚构钢结构焊缝的离线证据账。焊工资格/工艺批准、独立无损检验、返修复验与监造接收各有实体、版本和结论；晚到的资格冲突或超次返修不会抹去原始证据。CLI 输出当前决定，`report` 展示历史，`audit` 从原始采录文件重算。

```powershell
python -m pip install . --no-build-isolation --no-deps
python -m weldproof demo --db demo.sqlite
python -m weldproof audit --db demo.sqlite
python -m unittest discover -s tests -v
```

独立入口：`weld-import`、`ndt-import`、`repair-import`，各用 `--db state.sqlite --input fixtures/<weld|ndt|repair>_ok.json`；相应 `_bad.json` 展示未获资格、自检与超次返修。全部 fixture 合成，不接真实检验设备。
