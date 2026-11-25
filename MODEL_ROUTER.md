# ModelRouter · 角色化模型接入

日期：2026-11-12
位置：`personal-ai-system/pai/model_router.py`

## 角色

```text
chat     对话/推理      model_gateway
embed    向量            embedding.py
rerank   重排            rerank.py
asr      语音转文字      asr.py
tts      朗读            voice.py
vision   看图            HTTP（未配置）
guard    轻量闸门        规则
judge    评审            chat
```

## 状态

```powershell
cd C:\Users\Administrator\Documents\Qoder\2026-10-19\14868c32\personal-ai-system
python tools\roles.py status
```

当前本机：

```text
chat    openai（MiniMax，启动脚本注入）
embed   http（BGE-M3 / Ollama）
rerank  http（BGE-M3 余弦）
asr     vosk（本地，可用）
tts     windows-sapi
vision  none
guard   rules
judge   chat
```

## BGE 服务

```powershell
cd C:\Users\Administrator\Documents\Qoder\2026-10-19\14868c32\personal-ai-system
python -m pai.bge_service --port 8780
```

`start_all.py` 会自动拉起 8780。

服务端点：

```text
GET  /health
POST /v1/embeddings
POST /v1/rerank
```

默认模型：

```text
Ollama bge-m3
1024 维
```

注意：

```text
rerank 用的是 BGE-M3 向量余弦
不是 bge-reranker 交叉编码器
```

## 桌面 UI

```text
吹雪菜单 → 角色状态
托盘 → 角色状态
```

每个角色一个开关：

```text
勾上：ModelRouter 允许调用
关掉：ModelRouter 直接拒绝，不静默降级
```

设置落盘：

```text
data/role_settings.json
```

工作台：

```text
优先走 ModelRouter.chat
只有 chat 角色是 mock 时，才回退本机 /api/chat
chat 角色关闭时直接报错
```

## 用

```powershell
python tools\roles.py embed "工厂对账"
python tools\roles.py rerank "对账" "报价单" "工厂对账流程"
python tools\roles.py asr D:\path\audio.wav
```

## 接真模型

### Embedding

```text
PAI_EMBED_BASE_URL=http://127.0.0.1:11434/v1
PAI_EMBED_MODEL=bge-m3
PAI_EMBED_API_KEY=...
```

### Rerank

```text
PAI_RERANK_BASE_URL=http://127.0.0.1:port/v1
PAI_RERANK_MODEL=bge-reranker-v2-m3
PAI_RERANK_API_KEY=...
```

### ASR

```text
PAI_ASR_BASE_URL=http://127.0.0.1:port/v1
PAI_ASR_MODEL=whisper-1
PAI_ASR_API_KEY=...
```

或本地：

```text
PAI_VOSK_MODEL=D:\DSH\x6_vosk\vosk-model-small-cn
```

## 诚实边界

```text
local-hash 不是语义 embedding
lexical 不是神经 rerank
vision 未配置
guard 只是规则，不是权限系统
judge 仍是 chat
```

要质量，必须接真 embedding/rerank 服务；兜底只保证流程不断。

## 测试

```text
tests/test_model_router.py 5/5
```

## 一句话

> **ModelRouter 把八个角色分开接；Embedding/Rerank/ASR 已有可跑实现与兜底，但兜底不等于语义质量，真上线要接 BGE / Whisper 服务。**
