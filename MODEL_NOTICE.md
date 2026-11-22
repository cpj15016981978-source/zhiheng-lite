# Bundled model and runtime notices

## Core model

- Name: Qwen3-4B-Instruct (quantized GGUF, Q4)
- Upstream: https://huggingface.co/Qwen/Qwen3-4B-Instruct
- License: Apache License 2.0
- This repository ships one quantized copy as `models/core.gguf`.

The model is bundled for local inference only. If you redistribute a modified
build, keep the upstream license and attribution.

## Runtime

- llama.cpp / llama-server, build b12631, Windows x86_64 CPU
- Upstream: https://github.com/ggml-org/llama.cpp
- License: MIT (see `runtime/LICENSE-LLAMA.CPP`)

## What is not bundled

- No API keys
- No cloud credentials
- No user memory or chat database
- No additional models
