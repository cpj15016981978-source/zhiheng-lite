# Runtime Manager

一个入口启动：

```text
知衡.cmd
→ runtime_manager.py
→ Ollama（需要时）
→ spine 后端
→ WebView2 桌面
```

## 负责

```text
用户数据目录
后端端口
健康检查
版本握手
后端崩溃重启
桌面关闭后停止后端
模型存在性检查
```

## 验收

```text
health.version = 1.0.1
db = %LOCALAPPDATA%\Zhiheng\data\memory.db
runtime.log 有启动/就绪/桌面
```

旧入口保留为 `知衡_旧启动.cmd`。
