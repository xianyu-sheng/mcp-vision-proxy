# mcp-vision-proxy

> A lightweight MCP proxy that gives blind LLMs like DeepSeek real vision capabilities in Claude Code CLI.

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-1.1+-purple.svg)](https://modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

<!-- omit in toc -->
---

**中文** | [English](#english)

---

## 目录

- [核心卖点](#核心卖点)
- [为什么存在这个项目](#为什么存在这个项目)
- [架构亮点](#架构亮点)
- [安装](#安装)
- [配置](#配置)
- [使用](#使用)
- [工具使用规则](#工具使用规则)
- [命令行参数](#命令行参数)
- [端到端实测报告](#端到端实测报告)
- [测试截图](#测试截图)

---

## 核心卖点

- **让盲模型看见**：DeepSeek 等纯文本模型通过 MCP 工具按需调用外部视觉 API（豆包），获得完整的图片理解能力
- **零延迟注入**：热键监听响应 < 100ms，全程不含任何网络请求
- **绕过 CLI 图片拦截**：独创的 ID 凭证码机制，瞒天过海，欺骗 Claude Code CLI
- **跨进程安全**：热键进程与 MCP 服务进程内存完全隔离，通过文件系统通信

---

## 为什么存在这个项目

纯文本大模型（如 DeepSeek-v4-pro）在 Claude Code CLI 环境中有一个致命缺陷：**无法读图**。

当你复制一张截图并按下热键时，Claude Code CLI 会原生拦截带有 `.png` 扩展名的绝对路径，试图将图片二进制直接附加到 API 请求中。这会导致两种失败：

1. **纯文本模型崩溃**：DeepSeek 等不支持多模态输入的模型收到图片附件时，API 直接报错 `不支持图像/视觉输入`
2. **工具调用被绕过**：Claude Code 的原生拦截在模型看到凭证前就拦截了请求

**解决方案**：凭证码机制。

```
Claude Code CLI 拦截: [past 1 photo: D:/temp/img_20260508_190913.png]  ← 被拦截！
我们的注入文本:      [视觉凭证 ID: img_20260508_190913]            ← 纯文本，安全过境
```

Claude Code CLI 无法将 `img_20260508_190913` 识别为图片文件，只能将其作为普通文本处理，从而触发 MCP 工具调用。

---

## 架构亮点

### 独创的 ID 凭证码机制 (Vision Proxy ID)

```
┌─────────────────────────────────────────────────────────────┐
│  进程 1: python main.py (热键监听)                         │
│                                                             │
│  Ctrl+Alt+V → 读剪贴板 → 保存图片                          │
│  → 生成凭证 ID: img_20260508_190913_123456                 │
│  → 写入 CACHE_DIR/{image_id}.png                           │
│  → 注入凭证码文本到终端（不含任何路径/扩展名）              │
│                                                             │
│  ⚡ 响应时间 < 100ms | 零网络请求                          │
└─────────────────────────────────────────────────────────────┘
                         ↓
        [视觉凭证 ID: img_20260508_190913_123456]
          ⚠️系统指令：立即调用工具
          mcp__vision-proxy__analyze_local_image
          (参数 image_id=img_20260508_190913_123456)
                         ↓
┌─────────────────────────────────────────────────────────────┐
│  Claude Code CLI                                            │
│  检测到凭证码 → 调用 MCP 工具 mcp__vision-proxy__analyze   │
└─────────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│  进程 2: python main.py --mcp (MCP Stdio 服务)            │
│                                                             │
│  收到 image_id → 静态拼接路径:                              │
│  CACHE_DIR/{image_id}.png / .txt                           │
│                                                             │
│  三级收敛逻辑:                                              │
│  1. 优先读 .txt 缓存（后台预分析已完成）                   │
│  2. 次选调视觉 API（豆包）→ 返回 Markdown                  │
│  3. 异常明确阻断（禁止模型继续搜索）                       │
└─────────────────────────────────────────────────────────────┘
```

### 跨进程内存隔离

热键监听进程和 MCP 服务进程运行在不同的 Python 进程中，内存不共享。旧方案使用内存字典做凭证映射，在不同进程中完全失效。

**解决方案**：静态路径重组。图片保存路径由凭证 ID 唯一确定，MCP 服务通过 `_static_paths(image_id)` 静态拼接，还原出完全相同的路径。两个进程独立计算，无需任何内存共享。

---

## 安装

### 前置依赖

- Python 3.8+
- Windows 10/11（热键监听使用 `pynput` + Win32 API）

### 安装步骤

```bash
# 克隆仓库
git clone https://github.com/xianyu-sheng/mcp-vision-proxy.git
cd mcp-vision-proxy

# 安装依赖
pip install -r requirements.txt
```

---

## 配置

### 第一步：复制配置文件

```bash
cp config.example.json config.json
```

### 第二步：填入你的视觉模型 API

编辑 `config.json`，填入你的 API Key：

```json
{
  "vision_api_key": "your_api_key_here",
  "vision_base_url": "https://ark.cn-beijing.volces.com/api/v3",
  "vision_model": "doubao-seed-vision-250328",
  "vision_timeout": 30,
  "vision_max_tokens": 4096,
  "vision_max_retries": 3,
  "vision_retry_delay": 2,
  "mcp_port": 18792,
  "injection_delay_ms": 50,
  "foreground_restore_delay_ms": 80
}
```

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `vision_api_key` | 视觉模型 API Key | **必填** |
| `vision_base_url` | 视觉 API 地址 | 豆包 ARK API |
| `vision_model` | 视觉模型名称 | `doubao-seed-vision-250328` |
| `vision_timeout` | API 超时（秒） | 30 |
| `vision_max_tokens` | 最大输出 token 数 | 4096 |

> **安全提示**：`config.json` 已加入 `.gitignore`，不会上传到 GitHub。

---

## 使用

### 步骤 1：启动热键监听（终端 1）

```bash
python main.py
```

此进程监听 `Ctrl+Alt+V`，负责捕获剪贴板图片并注入凭证码。始终保持运行。

### 步骤 2：在 Claude Code 中注册 MCP 工具

在 Claude Code 项目中执行（只需执行一次）：

```bash
claude mcp add vision-proxy python /path/to/main.py --mcp
```

或者手动修改 `~/.claude/settings.json`（或项目根目录的 `.claude.json`）：

```json
{
  "mcpServers": {
    "vision-proxy": {
      "command": "python",
      "args": ["d:/path/to/main.py", "--mcp"]
    }
  }
}
```

> Windows 用户建议使用绝对路径：`d:/CLI_paste_photo/main.py`

### 步骤 3：使用热键注入

1. 复制任意图片到剪贴板（截图、网页图片、文档图片均可）
2. 按下 `Ctrl+Alt+V`
3. 终端自动注入凭证码，Claude Code 自动调用视觉 API
4. 结果以 Markdown 文本形式返回给 DeepSeek 进行推理

---

## 工具使用规则

Claude Code 中的 `mcp__vision-proxy__analyze_local_image` 工具遵循以下规则：

### 触发条件

当对话中出现 `[视觉凭证 ID: img_xxxxxxxx_xxxxxx]` 时，**必须立即调用**此工具。

### 铁律

1. **绝对不要**尝试直接读取或解释 `[视觉凭证 ID: xxx]` 格式的文本。这不是图片文件路径。
2. **必须**通过 `mcp__vision-proxy__analyze_local_image` 工具获取图片内容。
3. 工具返回的是 **Markdown 纯文本**，可直接作为对话上下文使用。
4. 如果图片中包含代码，完整转录代码块（保留缩进）。
5. **禁止任何文件搜索**：凭证 ID 是虚拟凭证，物理硬盘上不存在同名文件。你**绝对不可以使用** Bash、PowerShell、`cat`、`type`、`dir`、`glob` 或任何工具去搜索它。
6. 如果工具返回"MCP 错误: 本地硬盘中确实找不到此图片"，**必须立即向用户宣告无法分析此图片**，禁止继续搜索、猜测或编造内容。

---

## 命令行参数

| 参数 | 功能 |
|------|------|
| 无参数 | 启动热键监听（双模式：热键 + MCP Stdio） |
| `--mcp` | 仅启动 MCP Stdio 服务器（Claude Code 集成专用） |
| `--http` | 启动 HTTP REST API（备选方案：`http://localhost:18792/analyze?id=xxx`） |

---

## 端到端实测报告

> 以下为 2026-05-08 在真实 Claude Code + DeepSeek 环境中运行的端到端测试结果。

### 总体结果

| 指标 | 数值 |
|------|------|
| 总测试用例 | 26 |
| 通过 | 26 |
| 失败 | 0 |
| 通过率 | **100%** |

### 测试场景覆盖

| 场景 | 描述 | 状态 |
|------|------|------|
| C++ Raft RPC 代码分析 | 1707 字符代码图片，DeepSeek 正确识别并分析逻辑 Bug | ✅ |
| 物理实验图表数据 | Tracker 自由落体图表，DeepSeek 提取数据并计算 g ≈ 9.754 m/s² | ✅ |
| 损坏图片文件 | HTTP 400 错误，DeepSeek 收到优雅降级提示，不崩溃 | ✅ |
| 无效 API Key | HTTP 401 认证错误，DeepSeek 收到优雅降级提示 | ✅ |

### 视觉模型输出示例（C++ Raft 代码）

```cpp
// Raft RPC - AppendEntries Handler (题目)
// @B @@@@@ leader A @@@@@@@@@@

enum OpCode { OP_NOOP = 0, OP_PUT = 1, OP_DELETE = 2 };

struct LogEntry {
    int term;
    OpCode opcode;
    std::string key;
    std::string value;
    int64_t timestamp;
};

struct AppendEntriesArgs {
    int term;
    int leaderId;
    int prevLogIndex;
    int prevLogTerm;
    std::vector<LogEntry> entries;
    int leaderCommit;
};
```

### DeepSeek 基于视觉结果的推理输出

> 好的，我已经获取到图片中的代码内容。下面我来详细分析这段 **Raft AppendEntries RPC 处理函数** 中的逻辑问题：

**核心 Bug：`commitIndex` 更新未限制在日志长度范围内**

```cpp
if (args.leaderCommit > commitIndex) {
    commitIndex = args.leaderCommit;  // ← 错误点
    applyMessages();
}
```

**问题**：代码直接将 `commitIndex` 设置为 `args.leaderCommit`，**没有与当前节点日志长度取最小值**。根据 Raft 论文（Figure 2）的规则：`commitIndex = min(leaderCommit, index of last new entry)`

---

## 测试截图

以下是完整实测流程截图：

### 截图 1: C++ Raft RPC 代码图片

![C++ Raft 代码分析](image/%E6%B5%8B%E8%AF%95/step1_hotkey_injection.png)

### 截图 2: 物理实验图表数据

![物理实验图表分析](image/%E6%B5%8B%E8%AF%95/step2_mcp_tool_call.png)

### 截图 3: 损坏图片异常处理

![损坏图片异常阻断](image/%E6%B5%8B%E8%AF%95/step3_analysis_result.png)


---
## Test Screenshots

### Screenshot 1: C++ Raft RPC Code Image

![C++ Raft Code Analysis](image/%E6%B5%8B%E8%AF%95/step1_hotkey_injection.png)

### Screenshot 2: Physics Experiment Chart

![Physics Experiment Chart Analysis](image/%E6%B5%8B%E8%AF%95/step2_mcp_tool_call.png)

### Screenshot 3: Corrupted Image Error Handling

![Corrupted Image Error Handling](image/%E6%B5%8B%E8%AF%95/step3_analysis_result.png)

---

## License

MIT License — 可自由使用、修改和分发。

---

<!-- ============================================================================= -->

<a id="english"></a>

# mcp-vision-proxy (English)

> A lightweight MCP proxy that gives blind LLMs like DeepSeek real vision capabilities in Claude Code CLI.

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-1.1+-purple.svg)](https://modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

---

## Table of Contents

- [Core Features](#core-features)
- [Why This Exists](#why-this-exists)
- [Architecture](#architecture)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage](#usage)
- [Tool Usage Rules](#tool-usage-rules)
- [Command Line Arguments](#command-line-arguments)
- [E2E Test Report](#e2e-test-report)
- [Test Screenshots](#test-screenshots)

---

## Core Features

- **Give blind LLMs vision**: DeepSeek and other text-only models gain image understanding via on-demand MCP tool calls to external vision APIs (Doubao)
- **Zero-latency injection**: Hotkey listener responds in < 100ms with zero network requests
- **Bypass CLI image interception**: Original Vision Proxy ID mechanism outsmarts Claude Code CLI
- **Cross-process safe**: Hotkey process and MCP service are fully memory-isolated, communicating via filesystem

---

## Why This Exists

Text-only LLMs (e.g., DeepSeek-v4-pro) have a critical flaw in Claude Code CLI: **they cannot see images**.

When you paste a screenshot and press the hotkey, Claude Code CLI natively intercepts absolute paths with `.png` extensions and tries to attach image binaries directly to API requests. This causes two failures:

1. **Pure-text model crashes**: Models that don't support multimodal input receive image attachments and the API returns `vision input not supported`
2. **Tool call is bypassed**: Claude Code's native interception happens before the model even sees the prompt

**Solution**: Vision Proxy ID mechanism.

```
Claude Code CLI intercepts: [past 1 photo: D:/temp/img_20260508_190913.png]  ← BLOCKED!
Our injected text:           [Vision Credential ID: img_20260508_190913]     ← Safe passage
```

Claude Code CLI cannot recognize `img_20260508_190913` as an image file, treats it as plain text, and triggers the MCP tool call instead.

---

## Architecture

### Vision Proxy ID + MCP Tool Calling

```
┌─────────────────────────────────────────────────────────────┐
│  Process 1: python main.py (Hotkey Listener)               │
│                                                             │
│  Ctrl+Alt+V → Read clipboard → Save image                   │
│  → Generate credential ID: img_20260508_190913_123456       │
│  → Write to CACHE_DIR/{image_id}.png                       │
│  → Inject credential text (no path/extension)               │
│                                                             │
│  ⚡ Response < 100ms | Zero network requests                │
└─────────────────────────────────────────────────────────────┘
                         ↓
        [Vision Credential ID: img_20260508_190913_123456]
          ⚠️System Instruction: Immediately call
          mcp__vision-proxy__analyze_local_image
          (param image_id=img_20260508_190913_123456)
                         ↓
┌─────────────────────────────────────────────────────────────┐
│  Claude Code CLI                                            │
│  Detects credential → Calls MCP tool                        │
└─────────────────────────────────────────────────────────────┘
                         ↓
┌─────────────────────────────────────────────────────────────┐
│  Process 2: python main.py --mcp (MCP Stdio Server)       │
│                                                             │
│  Receives image_id → Static path reconstruction:            │
│  CACHE_DIR/{image_id}.png / .txt                           │
│                                                             │
│  Three-tier fallback:                                       │
│  1. Read .txt cache (pre-analysis done)                   │
│  2. Call vision API → return Markdown                      │
│  3. Graceful error (model must not search further)         │
└─────────────────────────────────────────────────────────────┘
```

### Cross-Process Memory Isolation

The hotkey listener and MCP service run in separate Python processes with isolated memory. Static path reconstruction ensures both processes compute the exact same file paths without any shared state.

---

## Installation

```bash
# Clone the repo
git clone https://github.com/xianyu-sheng/mcp-vision-proxy.git
cd mcp-vision-proxy

# Install dependencies
pip install -r requirements.txt
```

---

## Configuration

```bash
cp config.example.json config.json
```

Edit `config.json` with your vision API credentials:

```json
{
  "vision_api_key": "your_api_key_here",
  "vision_base_url": "https://ark.cn-beijing.volces.com/api/v3",
  "vision_model": "doubao-seed-vision-250328",
  "vision_timeout": 30,
  "vision_max_tokens": 4096
}
```

> **Security**: `config.json` is in `.gitignore` and will never be committed.

---

## Usage

### Step 1: Start the hotkey listener (Terminal 1)

```bash
python main.py
```

This process listens for `Ctrl+Alt+V` and injects credentials. Keep it running.

### Step 2: Register MCP tool in Claude Code

Run once in your Claude Code project:

```bash
claude mcp add vision-proxy python /path/to/main.py --mcp
```

Or manually edit `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "vision-proxy": {
      "command": "python",
      "args": ["/absolute/path/to/main.py", "--mcp"]
    }
  }
}
```

### Step 3: Use the hotkey

1. Copy any image to clipboard (screenshot, web image, document image)
2. Press `Ctrl+Alt+V`
3. Credential is injected, Claude Code auto-calls the vision API
4. Result returns as Markdown text for DeepSeek to reason about

---

## Tool Usage Rules

The `mcp__vision-proxy__analyze_local_image` tool follows these rules:

1. **Never** try to read `[Vision Credential ID: xxx]` as a file path — it is not.
2. **Must** use the MCP tool to get image content when credential IDs appear.
3. The tool returns **Markdown plain text** — use it directly as context.
4. **No file searching**: credential IDs are virtual. Do not use Bash, PowerShell, `cat`, `dir`, or any tool to search for them.
5. If the tool returns "MCP Error: image not found", **tell the user directly** — do not search further.

---

## Command Line Arguments

| Argument | Description |
|----------|-------------|
| (none) | Start hotkey listener (dual mode: hotkey + MCP Stdio) |
| `--mcp` | MCP Stdio server only (for Claude Code integration) |
| `--http` | HTTP REST API fallback (`http://localhost:18792/analyze?id=xxx`) |

---

## E2E Test Report

> Run on 2026-05-08 in real Claude Code + DeepSeek environment.

### Overall Results

| Metric | Value |
|--------|-------|
| Total test cases | 26 |
| Passed | 26 |
| Failed | 0 |
| Pass rate | **100%** |

### Test Coverage

| Scenario | Description | Status |
|----------|-------------|--------|
| C++ Raft RPC Code Analysis | 1707-char code image, DeepSeek correctly identifies logical bug | ✅ |
| Physics Experiment Chart | Tracker free-fall chart, DeepSeek extracts data, computes g ≈ 9.754 m/s² | ✅ |
| Corrupted Image File | HTTP 400 error, DeepSeek receives graceful degradation | ✅ |
| Invalid API Key | HTTP 401 auth error, DeepSeek receives graceful degradation | ✅ |

### Vision Model Output Sample (C++ Raft Code)

```cpp
struct AppendEntriesArgs {
    int term;
    int leaderId;
    int prevLogIndex;
    int prevLogTerm;
    std::vector<LogEntry> entries;
    int leaderCommit;
};
```

### DeepSeek's Reasoning Based on Vision Output

> Good, I've retrieved the code content. Let me analyze the logical issue in this **Raft AppendEntries RPC handler**:

**Core Bug: `commitIndex` not bounded by log length**

```cpp
if (args.leaderCommit > commitIndex) {
    commitIndex = args.leaderCommit;  // ← Error point
    applyMessages();
}
```

The code directly sets `commitIndex` to `args.leaderCommit` **without taking the minimum with current log length**. Per Raft paper (Figure 2): `commitIndex = min(leaderCommit, index of last new entry)`

---


## License

MIT License — free to use, modify, and distribute.
