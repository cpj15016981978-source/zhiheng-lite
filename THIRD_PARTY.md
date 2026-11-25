# Third-party components

知衡 source is licensed under Apache-2.0. The following runtime dependencies
and optional components keep their own licenses; check the upstream projects
before redistribution.

| Component | Use | License |
|---|---|---|
| Python 3.13 | runtime | PSF License |
| SQLite | local databases | Public Domain |
| pypdf | PDF text extraction | BSD-3-Clause |
| python-docx | Word documents | MIT |
| openpyxl | Excel workbooks | MIT |
| zhconv | Chinese conversion | MIT |
| vosk | optional ASR | Apache-2.0 |
| apscheduler | scheduling | MIT |
| qwen-agent | optional engine | Apache-2.0 |
| soundfile | audio I/O | BSD-3-Clause |
| Ollama | optional local model runtime | MIT |
| Qwen models | optional local models | Apache-2.0 (model card) |
| DeepSeek / OpenAI-compatible APIs | optional cloud providers | provider terms |
| Tkinter / WebView2 | desktop UI | PSF / Microsoft terms |

The bundled installer may include a Python runtime and model files. Their
licenses are distributed with those components and are not relicensed by this
project.
