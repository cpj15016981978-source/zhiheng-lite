# live eval 记录

时间：2026-10-30

## 已跑

`20261030_live.json`

```
prompt   用一句话说明你是谁
model    MiniMax-M3.1-Flash
cost     0.00091 元
tokens   694
ok       true
```

## 复跑

```powershell
$env:PAI_MODEL_PROVIDER="openai"
$env:PAI_MODEL_NAME="MiniMax-M3.1-Flash"
$env:PAI_OPENAI_BASE_URL="https://api.minimaxi.com/v1"
$env:OPENAI_API_KEY=$env:MINIMAX_API_KEY
python tools/eval_live.py --max-cost 0.01 --record evals/live/last.json
```

## 约束

- 临时库
- 单轮
- 硬预算
- 不写 `data/memory.db`
- 失败（空回答/超预算）退出 1
