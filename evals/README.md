# harness evals v0

时间：2026-10-30

## 这是什么

不是单元测试，是 **端到端行为 eval**：

- 起真实 `webapp.start(port=0, db_path=临时库)`
- 把 `Orchestrator` 换成记录器
- 用 HTTP 打真实入口
- 检查 blocked / blocked_secret / fake 调用次数

零模型、零真库、零联网。

## 跑

```powershell
python tools/run_eval.py
python tools/run_eval.py --json
```

退出码：

```
0  全过
1  有失败
```

## 用例

`evals/cases.json`：

- health 200
- /m1 200
- write → blocked + requires_approval
- risk → blocked
- 贴密钥 → blocked_secret
- chat → 放行到 fake
- 未知接口 → 404
- approvals 无 token → available=false

## 不做什么

- 不调真模型
- 不打开 `data/memory.db`
- 不碰 agentd
- 不写两棵树

## 下一步

- trace replay
- 预算上限
- 失败分类
- 红队用例
