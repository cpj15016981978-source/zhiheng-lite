# harness 总闸

时间：2026-10-30

## 启动前

```powershell
python tools/preflight.py --startup
```

只跑 eval + redteam，零模型、零真库。

`打开界面.bat` 已接入。

## 提交前

```powershell
python tools/preflight.py --commit
```

跑全量单测 + eval + redteam。

已放 `.githooks/pre-commit`；若仓库启用 git：

```powershell
git config core.hooksPath .githooks
```

## 全量

```powershell
python tools/check_harness.py
python tools/check_harness.py --quick
python tools/check_harness.py --json
```

步骤：

```
unittest discover -s tests
python tools/run_eval.py --json
python tools/run_eval.py --cases evals/redteam.json --json
python tools/eval_world.py
python tools/eval_world_shadow.py
```

## 真实模型 eval（小额、手动）

```powershell
python tools/eval_live.py --max-cost 0.01
python tools/eval_live.py --record evals/live/last.json
```

- 临时库
- 单轮
- 硬预算
- 不写 `data/memory.db`

## trace 持久化 + replay

eval 层：

```powershell
python tools/run_eval.py --record evals/records/eval.json
python tools/run_eval.py --replay evals/records/eval.json
```

记录 status + 稳定 body；replay 比对响应漂移。

生产层（opt-in）：

```powershell
$env:PAI_TRACE_STORE="on"
python webapp.py
python tools/replay_trace.py data/traces/trace-YYYYMMDD.jsonl
```

- 写 `data/traces/`
- 不写数据库
- 密钥拦截的 prompt 不存原文
- 回放只重跑入口闸，不调模型

## 判定

- 任一步 rc != 0 → RED，退出 1
- 全部 rc=0 → GREEN，退出 0

## 约束

- 零模型调用（除 `eval_live.py`）
- 零真库写入
- 临时库
- 只读代码与 eval 用例
