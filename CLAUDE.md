# Claude Code — 图片粘贴助手配置

## 架构说明

本工具实现**视觉代理 (Vision Proxy)** 架构：用不具备多模态能力的纯文本推理模型 (DeepSeek)，通过 MCP 工具按需调用外部轻量视觉 API (豆包)，赋予其完整的视觉理解能力。

```
剪贴板图片
    ↓
Ctrl+Alt+V (热键)
    ↓
保存图片至 CACHE_DIR/{image_id}.png，生成凭证 ID (不含路径不含扩展名)
    ↓
注入 `[视觉凭证 ID: img_20260508_190913_123456] ⚠️系统指令：立即调用工具 mcp__vision-proxy__analyze_local_image (参数 image_id=img_20260508_190913_123456) 来获取图片内容。绝对禁止使用 Bash 或 PowerShell 在本地硬盘搜索此文件！`
    ↓
Claude Code (DeepSeek) 检测到凭证码 → 调用 mcp__vision-proxy__analyze_local_image
    ↓
MCP 服务通过静态路径拼接还原: CACHE_DIR/{image_id}.png / .txt
    ↓
逻辑收敛: txt缓存优先读 → API次选调用 → 异常明确阻断
    ↓
返回图片 Markdown 描述，DeepSeek 基于此进行推理
```

### 设计核心 1：绕过 Claude Code 原生图片拦截

Claude Code CLI 会对终端文本中的 `.png` 路径做原生拦截，试图将图片二进制附加到 LLM 请求中，导致纯文本模型 (DeepSeek) API 报错 "不支持图像输入"。

**解决方案**：凭证码机制。热键注入的文本不含任何文件路径或扩展名，纯文本模型只会将其当作普通指令文本处理，从而触发 MCP 工具调用。

### 设计核心 2：跨进程内存隔离

热键监听进程和 MCP 服务进程运行在不同的 Python 进程中，内存不共享。旧方案使用内存字典 `image_registry` 做凭证映射，在不同进程中完全失效。

**解决方案**：静态路径重组。图片保存路径由凭证 ID 唯一确定（MCP 通过 `_static_paths(image_id)` 静态拼接），两个进程独立计算路径完全一致，无需任何内存共享。

## MCP 工具配置

请在 `~/.claude/settings.json`（或项目根目录的 `.claude.json`）中添加：

### Windows

```json
{
  "mcpServers": {
    "vision-proxy": {
      "command": "python",
      "args": ["d:/CLI_paste_photo/main.py", "--mcp"]
    }
  }
}
```

### macOS / Linux

```json
{
  "mcpServers": {
    "vision-proxy": {
      "command": "python3",
      "args": ["/path/to/main.py", "--mcp"]
    }
  }
}
```

> **重要**: `--mcp` 参数告诉脚本只启动 MCP 服务器，不启动热键监听。两个功能需要在不同进程中运行。

### MCP 工具注册

在 Claude Code 项目中执行以下命令，将视觉代理工具永久注册到当前项目：

```bash
claude mcp add vision-proxy python d:/CLI_paste_photo/main.py --mcp
```

注册成功后，Claude Code 会自动加载 `mcp__vision-proxy__analyze_local_image` 工具。确认注册状态：

```bash
claude mcp list
```

应看到 `vision-proxy` 条目。注册命令只需执行一次，配置会持久化。

## 工作流

### Step 1: 启动热键服务（终端 1）

```bash
python main.py
```

此进程监听 `Ctrl+Alt+V`，负责图片捕获与凭证 ID 注入。

### Step 2: 启动 MCP 服务（终端 2）

```bash
python main.py --mcp
```

此进程负责响应 Claude Code 的 `mcp__vision-proxy__analyze_local_image` 工具调用。

### Step 3: 使用

1. 复制任意图片到剪贴板。
2. 按下 `Ctrl+Alt+V`。
3. 终端自动注入类似以下文本：

```
[视觉凭证 ID: img_20260508_190913_123456] ⚠️系统指令：立即调用工具 mcp__vision-proxy__analyze_local_image (参数 image_id=img_20260508_190913_123456) 来获取图片内容。绝对禁止使用 Bash 或 PowerShell 在本地硬盘搜索此文件！
```

4. Claude Code (DeepSeek) 检测到凭证码，自动调用全限定工具名 `mcp__vision-proxy__analyze_local_image`。
5. 豆包视觉 API 解析图片，结果返回给 DeepSeek 进行推理。

## 工具使用规则

### 4. `mcp__vision-proxy__analyze_local_image`

- **工具名称**: `mcp__vision-proxy__analyze_local_image`（Claude Code 命名空间前缀不可省略）
- **触发条件**: 当对话中出现 `[视觉凭证 ID: img_xxxxxxxx_xxxxxx]` 时，**必须立即调用**此工具。
- **入参**: `image_id` — 凭证 ID（格式：`img_xxxxxxxx_xxxxxx`，不含路径和扩展名）。
- **返回值**: 图片内容的 Markdown 纯文本描述（文字、代码、图表、界面截图等）。
- **返回值格式**: 直接将描述文本作为对话上下文继续推理，**不要**在终端中打印或回显工具调用的原始结果。

### 指令规则

1. **绝对不要**尝试直接读取或解释 `[视觉凭证 ID: xxx]` 格式的文本。这不是图片文件路径。
2. **必须**通过 `mcp__vision-proxy__analyze_local_image` 工具获取图片内容，将返回的 Markdown 描述作为推理依据。
3. 工具返回的是 **Markdown 纯文本**，可直接作为对话上下文使用。
4. 如果图片中包含代码，完整转录代码块（保留缩进）。
5. **铁律：绝对禁止任何文件搜索**。凭证 ID 是虚拟凭证，物理硬盘上**不存在**同名文件，也**不存在**同名 `.txt` 文件（缓存由 MCP 服务内部管理）。你**绝对不可以使用** Bash、PowerShell、`cat`、`type`、`Get-Content`、`dir`、`ls`、`glob`、或任何文件搜索/读取工具去搜索任何凭证 ID 或对应的 `.txt` 文件。你唯一的合法操作就是调用 `mcp__vision-proxy__analyze_local_image` 工具。
6. 如果 `mcp__vision-proxy__analyze_local_image` 返回"MCP 错误: 本地硬盘中确实找不到此图片"，说明凭证已过期或文件已被清理。**必须立即直接向用户宣告"无法分析此图片"**，禁止继续搜索、猜测或编造内容。
7. 你（DeepSeek）是纯文本推理模型，视觉能力完全依赖 `mcp__vision-proxy__analyze_local_image` 工具。不要声称自己具备看图能力。

## 配置文件

所有配置位于 `config.json`（与 `main.py` 同目录）：

```json
{
  "vision_api_key": "your-api-key-here",
  "vision_base_url": "https://ark.cn-beijing.volces.com/api/v3",
  "vision_model": "doubao-seed-1-6-vision-250815",
  "vision_timeout": 30,
  "vision_max_tokens": 4096,
  "vision_max_retries": 3,
  "vision_retry_delay": 2,
  "mcp_port": 18792,
  "injection_delay_ms": 50,
  "foreground_restore_delay_ms": 80
}
```

## 常见问题

**Q: Ctrl+Alt+V 注入了凭证但工具没有被调用？**
A: 确保 MCP 服务器已启动（`python main.py --mcp`），并且 Claude Code 已正确加载 `mcpServers` 配置。

**Q: 视觉解析失败？**
A: 检查 `config.json` 中的 `vision_api_key` 是否有效，`vision_base_url` 是否可访问。

**Q: 凭证 ID 找不到图片？**
A: 凭证注册表有效期为 20 条最新记录。重启 `python main.py` 后旧凭证会失效。

**Q: 如何禁用后台视觉预解析（只注入凭证）？**
A: 将 `vision_api_key` 留空即可。
