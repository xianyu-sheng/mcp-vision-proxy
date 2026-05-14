# -*- coding: utf-8 -*-
"""
Ctrl+Alt+V 剪贴板图片注入工具
架构: 前端极速注入(热键监听) + 后端MCP工具服务(视觉解析)
依赖: pip install Pillow pynput requests mcp

模块A (热键回调): Ctrl+Alt+V → 读剪贴板 → 存本地 → 注入路径
                 全程 < 0.1s，绝对禁止网络请求
模块B (MCP工具): Claude Code 主动调用 analyze_local_image → 读本地图片 → 调用视觉API → 返回结果
"""
from __future__ import annotations

import os
import sys
import time
import json
import base64
import queue
import ctypes
import ctypes.wintypes
import threading
import traceback
import logging
import re
import asyncio
from datetime import datetime
from pathlib import Path

from PIL import ImageGrab
from pynput import keyboard

# ── UTF-8 输出修复 (Windows GBK 环境) ────────────────────────────
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── 路径定义 ─────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
CONFIG_FILE = SCRIPT_DIR / "config.json"
CACHE_DIR = Path(os.environ.get("TEMP", os.path.expanduser("~"))) / "claude_mcp_cache"

# ── 日志配置 ─────────────────────────────────────────────────────
def _get_logger(name: str) -> logging.Logger:
    log = logging.getLogger(name)
    log.setLevel(logging.INFO)
    if not log.handlers:
        _sh = logging.StreamHandler(sys.stderr)
        _sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
        log.addHandler(_sh)
    return log

_log = _get_logger("clipboard_paste")

# ── 配置加载 ─────────────────────────────────────────────────────
def load_config() -> dict:
    default = {
        "vision_api_key": "",
        "vision_base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "vision_model": "doubao-seed-1-6-vision-250815",
        "vision_timeout": 30,
        "vision_max_tokens": 4096,
        "vision_max_retries": 3,
        "vision_retry_delay": 2,
        "mcp_port": 18792,
        "injection_delay_ms": 50,
        "foreground_restore_delay_ms": 80,
        "pythonw_path": "pythonw",
    }

    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
            for k, v in default.items():
                cfg.setdefault(k, v)
            return cfg
        except Exception as e:
            _log.warning("读取 config.json 失败: %s，使用默认配置。", e)
    else:
        _save_config(default)
        _log.info("已自动创建配置文件: %s，请填入 vision_api_key 后重启。", CONFIG_FILE)

    return default


def _save_config(cfg: dict):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


CONFIG = load_config()

VISION_API_KEY = CONFIG.get("vision_api_key") or os.environ.get("VISION_API_KEY", "")
VISION_BASE_URL = CONFIG.get("vision_base_url") or os.environ.get(
    "VISION_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"
)
VISION_MODEL = CONFIG.get("vision_model") or os.environ.get("VISION_MODEL", "doubao-seed-1-6-vision-250815")
VISION_TIMEOUT = int(CONFIG.get("vision_timeout", 30))
VISION_MAX_TOKENS = int(CONFIG.get("vision_max_tokens", 4096))
MAX_RETRIES = int(CONFIG.get("vision_max_retries", 3))
RETRY_DELAY = int(CONFIG.get("vision_retry_delay", 2))
MCP_PORT = int(CONFIG.get("mcp_port", 18792))
INJECTION_DELAY_MS = int(CONFIG.get("injection_delay_ms", 50))
FOREGROUND_RESTORE_DELAY_MS = int(CONFIG.get("foreground_restore_delay_ms", 80))
PYTHONW_PATH = CONFIG.get("pythonw_path", "pythonw")

VISION_SYSTEM_PROMPT = (
    "你是一个精准的图片内容转录助手。请将图片中的所有内容转录为结构化的 Markdown 纯文本。\n\n"
    "规则：\n"
    "1. 图片包含文字/代码/公式时，完整准确地逐字转录，保留原始格式和缩进。\n"
    "2. 图片是图表/流程图/界面截图时，用文字详细描述结构、关键数据和逻辑关系。\n"
    "3. 图片是照片/场景时，描述关键元素、文字标识和空间关系。\n"
    "4. 不要寒暄，不要加解释，只输出转录结果本身。"
)

# ═══════════════════════════════════════════════════════════════════
#  静态路径映射 (跨进程安全)
#  热键进程和 MCP 进程内存隔离，但共享同一文件系统。
#  通过凭证 ID 静态拼接路径，无需任何内存共享。
# ═══════════════════════════════════════════════════════════════════

def _static_paths(image_id: str):
    """
    根据凭证 ID 静态还原图片和缓存文件的路径。
    两个进程独立调用，结果完全一致，跨进程安全。
    """
    image_id = image_id.strip()
    png_path = CACHE_DIR / f"{image_id}.png"
    txt_path = CACHE_DIR / f"{image_id}.txt"
    return png_path, txt_path


# ── 共享状态 (仅后台预分析队列) ──────────────────────────────────
# 线程安全的队列: [(image_path, event, result_holder), ...]
analysis_queue: queue.Queue = queue.Queue()

# ── Win32 SendInput (64 位兼容) ─────────────────────────────────
ULONG_PTR = ctypes.c_uint64
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", ctypes.wintypes.WORD),
        ("wScan", ctypes.wintypes.WORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", ctypes.c_long),
        ("dy", ctypes.c_long),
        ("mouseData", ctypes.wintypes.DWORD),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("time", ctypes.wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", ctypes.wintypes.DWORD),
        ("wParamL", ctypes.wintypes.WORD),
        ("wParamH", ctypes.wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [
        ("ki", KEYBDINPUT),
        ("mi", MOUSEINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("_pad", ctypes.c_uint32),
        ("union", _INPUT_UNION),
    ]


SendInput = ctypes.windll.user32.SendInput
SendInput.argtypes = [ctypes.c_uint, ctypes.POINTER(INPUT), ctypes.c_int]
SendInput.restype = ctypes.c_uint


def _send_unicode_char(ch: str):
    for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
        inp = INPUT()
        inp.type = INPUT_KEYBOARD
        inp.union.ki.wVk = 0
        inp.union.ki.wScan = ord(ch)
        inp.union.ki.dwFlags = flags
        inp.union.ki.time = 0
        inp.union.ki.dwExtraInfo = 0
        SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


def send_text(text: str):
    """通过 SendInput 发送 Unicode 文本，每次按键间隔 2ms。"""
    for ch in text:
        _send_unicode_char(ch)
        time.sleep(0.002)


# ── 修饰键释放 & 窗口操作 ─────────────────────────────────────────
VK_LCONTROL = 0xA2
VK_RCONTROL = 0xA3
VK_LMENU = 0xA4
VK_RMENU = 0xA5


def _release_modifiers():
    for vk in (VK_LCONTROL, VK_RCONTROL, VK_LMENU, VK_RMENU):
        ctypes.windll.user32.keybd_event(vk, 0, 0x0002, 0)


def _get_foreground_window():
    return ctypes.windll.user32.GetForegroundWindow()


def _set_foreground_window(hwnd):
    if not hwnd:
        return
    if ctypes.windll.user32.GetForegroundWindow() == hwnd:
        return
    fg_thread = ctypes.windll.user32.GetWindowThreadProcessId(hwnd, None)
    cur_thread = ctypes.kernel32.GetCurrentThreadId()
    ctypes.windll.user32.AttachThreadInput(cur_thread, fg_thread, True)
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    ctypes.windll.user32.BringWindowToTop(hwnd)
    ctypes.windll.user32.AttachThreadInput(cur_thread, fg_thread, False)


# ═══════════════════════════════════════════════════════════════════
#  模块A: 极速UI线程 (热键回调)
#  职责: 读取剪贴板 → 存本地 → 注入路径
#  约束: < 0.1s 完成，绝对禁止任何网络请求
# ═══════════════════════════════════════════════════════════════════

def _capture_and_inject():
    """
    模块A核心：捕获剪贴板图片并注入路径。
    全程不含任何网络调用，确保热键回调立即返回。
    """
    # 1. 读剪贴板
    try:
        img = ImageGrab.grabclipboard()
    except Exception as e:
        _log.error("读取剪贴板失败: %s", e)
        return

    if img is None:
        _log.warning("剪贴板为空或不含图片，跳过。")
        return
    if isinstance(img, list):
        _log.warning("剪贴板是文件列表而非图片，跳过。")
        return

    # 2. 保存图片（文件名即凭证 ID，不含 .png 后缀）
    image_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        png_path, txt_path = _static_paths(image_id)
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")
        img.save(str(png_path), "PNG")
        _log.info("图片已保存: %s (尺寸: %s, 凭证ID: %s)", png_path, img.size, image_id)
    except Exception as e:
        _log.error("保存图片失败: %s", e)
        return

    # 3. 捕获前台窗口句柄（用于恢复焦点）
    target_hwnd = _get_foreground_window()

    # 4. 构造凭证码文本并注入
    # 关键：使用 Claude Code 的全限定工具名 mcp__vision-proxy__analyze_local_image
    # 绝对不含 .png 后缀和绝对路径，绕过 Claude Code 原生图片拦截
    inject_text = (
        f"[视觉凭证 ID: {image_id}] "
        f"⚠️系统指令：立即调用工具 mcp__vision-proxy__analyze_local_image "
        f"(参数 image_id={image_id}) 来获取图片内容。绝对禁止使用 Bash 或 PowerShell 在本地硬盘搜索此文件！"
    )

    try:
        _release_modifiers()
        time.sleep(INJECTION_DELAY_MS / 1000.0)
        _set_foreground_window(target_hwnd)
        time.sleep(FOREGROUND_RESTORE_DELAY_MS / 1000.0)
        send_text(inject_text)
    except Exception as e:
        _log.error("注入失败: %s", e)
        return

    _log.info("凭证已注入 [凭证ID: %s]，长度: %d 字符", image_id, len(inject_text))

    # 5. 提交后台预分析（纯 fire-and-forget，不等待）
    result_holder: dict = {}
    event = threading.Event()
    analysis_queue.put((str(png_path), event, result_holder))


def _on_hotkey():
    """热键回调 — 在 pynput 的 GlobalHotKeys 内部线程中执行。"""
    _log.info("=" * 50)
    _log.info("监听到 Ctrl+Alt+V，开始处理...")
    try:
        _capture_and_inject()
    except Exception as e:
        _log.error("热键处理异常: %s\n%s", e, traceback.format_exc())
    _log.info("处理完成，继续监听。")
    _log.info("=" * 50)


# ═══════════════════════════════════════════════════════════════════
#  后台预分析 Worker (可选缓存，不影响主流程)
#  职责: 热键注入后，静默预分析图片，结果写入 .txt 缓存文件
#  注意: MCP call_tool 永远做 fresh 分析，此 worker 仅作预缓存
# ═══════════════════════════════════════════════════════════════════

def _preanalysis_worker():
    """
    后台守护线程：持续从 analysis_queue 取出待分析图片，
    调用视觉 API 将结果写入同名 .txt 缓存文件。
    MCP 工具可直接读取缓存（若文件新鲜）或发起自己的请求。
    """
    _log.info("后台预分析线程已启动。")
    while True:
        try:
            item = analysis_queue.get(timeout=1.0)
            if item is None:
                _log.info("收到停止信号，后台预分析线程退出。")
                break

            image_path, event, result_holder = item
            _log.info("后台预分析: %s", image_path)

            if not Path(image_path).exists():
                result_holder["error"] = f"文件不存在: {image_path}"
                event.set()
                continue

            result_text = _call_vision_api(image_path)

            if result_text:
                result_holder["text"] = result_text
                txt_path = Path(image_path).with_suffix(".txt")
                try:
                    with open(txt_path, "w", encoding="utf-8") as f:
                        f.write(result_text)
                    _log.info("预分析结果已缓存: %s", txt_path)
                except Exception as e:
                    _log.warning("保存 .txt 缓存失败: %s", e)
            else:
                result_holder["error"] = "视觉 API 调用全部失败"

            event.set()

        except queue.Empty:
            continue
        except Exception as e:
            _log.error("后台预分析线程异常: %s\n%s", e, traceback.format_exc())


# ═══════════════════════════════════════════════════════════════════
#  视觉 API 调用
# ═══════════════════════════════════════════════════════════════════

def _mime_type(path: str) -> str:
    ext = Path(path).suffix.lower().lstrip(".")
    return {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "gif": "image/gif",
        "webp": "image/webp",
        "bmp": "image/bmp",
    }.get(ext, "image/png")


def _call_vision_api(image_path: str) -> str:
    """
    单次调用视觉模型 API（内部重试逻辑见 analyze_image_vision_api）。
    全部失败返回空字符串。
    """
    import requests

    with open(image_path, "rb") as f:
        b64_data = base64.b64encode(f.read()).decode("utf-8")

    payload = {
        "model": VISION_MODEL,
        "max_tokens": VISION_MAX_TOKENS,
        "messages": [
            {"role": "system", "content": VISION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{_mime_type(image_path)};base64,{b64_data}"},
                    },
                    {"type": "text", "text": "请解析这张图片。"},
                ],
            },
        ],
    }

    headers = {
        "Authorization": f"Bearer {VISION_API_KEY}",
        "Content-Type": "application/json",
    }

    api_url = VISION_BASE_URL.rstrip("/") + "/chat/completions"
    resp = requests.post(api_url, json=payload, headers=headers, timeout=VISION_TIMEOUT)

    if resp.status_code in (401, 403):
        raise RuntimeError(f"认证/权限错误 (HTTP {resp.status_code})，请检查 API Key。")
    if resp.status_code == 429:
        raise RuntimeError("请求频率超限 (Rate Limit)。")
    if resp.status_code >= 500:
        raise RuntimeError(f"服务端错误 (HTTP {resp.status_code})。")

    resp.raise_for_status()

    # 豆包 ARK API 返回的 JSON 中文字符使用 GB18030 编码，
    # 但 HTTP Content-Type header 错误地声明为 charset=utf-8，
    # 导致 requests.json() 按 UTF-8 解码产生乱码。
    # 强制使用 GB18030 解码来正确还原中文内容。
    try:
        data = resp.json()
    except Exception:
        raw_text = resp.content.decode("gb18030", errors="replace")
        data = json.loads(raw_text)

    if "error" in data:
        raise RuntimeError(f"API 返回错误: {data['error']}")

    choices = data.get("choices", [])
    if choices and choices[0].get("message", {}).get("content"):
        return choices[0]["message"]["content"].strip()

    raise RuntimeError("API 返回了空内容。")


def analyze_image_vision_api(image_path: str) -> str:
    """
    调用视觉模型 API（含重试），成功返回 Markdown 文本，全部失败返回空字符串。
    此函数同时被 MCP call_tool 和后台预分析 worker 调用。
    """
    if not VISION_API_KEY:
        _log.warning("未配置 VISION_API_KEY，跳过视觉解析。")
        return ""

    try:
        import requests  # noqa: F401
    except ImportError:
        _log.error("requests 库未安装，请执行: pip install requests")
        return ""

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            _log.info(
                "调用视觉模型 [%s] 解析 %s (第 %d/%d 次，超时 %ds)...",
                VISION_MODEL, image_path, attempt, MAX_RETRIES, VISION_TIMEOUT,
            )
            result = _call_vision_api(image_path)
            _log.info("视觉 API 调用成功 (第 %d 次)，返回 %d 字符。", attempt, len(result))
            return result
        except RuntimeError as e:
            _log.warning("第 %d 次调用失败: %s", attempt, e)
            if attempt < MAX_RETRIES:
                _log.info("%d 秒后重试...", RETRY_DELAY)
                time.sleep(RETRY_DELAY)
        except Exception as e:
            _log.warning("第 %d 次调用异常: %s", attempt, e)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY)

    _log.error("视觉 API 连续 %d 次调用均失败。", MAX_RETRIES)
    return ""


# ═══════════════════════════════════════════════════════════════════
#  模块B: MCP 后台工具服务 (stdio transport)
#  职责: Claude Code 主动调用 analyze_local_image
#  收敛逻辑: txt优先 → API次选 → 异常阻断
# ═══════════════════════════════════════════════════════════════════


def _build_mcp_server():
    """
    构建 MCP Server 实例，注册工具处理函数。
    返回 mcp_server，其 run() 需要在 asyncio 事件循环中执行。
    """
    from mcp.server import Server
    from mcp.types import Tool, TextContent

    mcp_server = Server("vision-proxy")

    @mcp_server.list_tools()
    async def list_tools():
        return [
            Tool(
                name="analyze_local_image",
                description=(
                    "当对话中出现 [视觉凭证 ID: img_xxxxxxxx_xxxxxx] 时，必须立即调用此工具。\n"
                    "该工具通过视觉代理（豆包视觉模型）将本地图片转换为 Markdown 纯文本描述，\n"
                    "用于赋予纯文本模型（DeepSeek）多模态视觉能力。\n"
                    "使用方式: 调用工具 mcp__vision-proxy__analyze_local_image，传入凭证 ID（不含路径和扩展名）。\n"
                    "⚠️ 绝对禁止使用 Bash/PowerShell 在本地硬盘搜索凭证 ID！"
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "image_id": {
                            "type": "string",
                            "description": (
                                "图片的视觉凭证 ID，格式如 img_20260508_190913_123456。\n"
                                "来自 [视觉凭证 ID: img_xxx] 格式，\n"
                                "不要传入完整路径或带 .png 后缀的字符串，只传凭证 ID。"
                            ),
                        }
                    },
                    "required": ["image_id"],
                },
            )
        ]

    @mcp_server.call_tool()
    async def call_tool(name: str, arguments: dict):
        """
        MCP 工具入口 — 模块B的核心。

        三级收敛逻辑（喂饭到嘴里）：
        1. 优先读 txt：后台预分析已完成的缓存，直接返回
        2. 次选调 API：txt 不存在但 png 存在，立即调用视觉 API
        3. 异常阻断：都不存在，返回明确错误，禁止模型继续搜索

        所有路径通过 _static_paths 静态拼接，跨进程安全。
        """
        if name != "analyze_local_image":
            raise ValueError(f"未知工具: {name}")

        image_id_arg = arguments.get("image_id", "").strip()
        if not image_id_arg:
            return [TextContent(
                type="text",
                text="错误: 未提供 image_id 参数。请传入 [视觉凭证 ID: xxx] 中的凭证ID。"
            )]

        _log.info("MCP 收到 analyze_local_image 请求 (凭证ID: %s)", image_id_arg)

        png_path, txt_path = _static_paths(image_id_arg)
        png_str = str(png_path)
        txt_str = str(txt_path)

        # ── 级别1: 优先读 txt 缓存 ──────────────────────────────────
        if txt_path.exists():
            try:
                cached_text = txt_path.read_text(encoding="utf-8")
                if cached_text.strip():
                    _log.info("命中 txt 缓存: %s (%d 字符)", txt_str, len(cached_text))
                    return [TextContent(type="text", text=cached_text)]
            except Exception as e:
                _log.warning("读取 txt 缓存失败: %s", e)

        # ── 级别2: 次选调 API ───────────────────────────────────────
        if png_path.exists():
            _log.info("txt 缓存未命中，调用视觉 API: %s", png_str)
            result_text = analyze_image_vision_api(png_str)

            if result_text:
                try:
                    txt_path.write_text(result_text, encoding="utf-8")
                    _log.info("分析结果已缓存: %s", txt_str)
                except Exception as e:
                    _log.warning("保存分析缓存失败: %s", e)

                return [TextContent(type="text", text=result_text)]
            else:
                return [TextContent(
                    type="text",
                    text=(
                        "[MCP 错误] 视觉 API 调用失败，无法获取图片内容。\n"
                        "请确认 config.json 中已配置有效的 vision_api_key，或检查网络连接。"
                    ),
                )]

        # ── 级别3: 异常阻断 ─────────────────────────────────────────
        msg = (
            f"[MCP 错误] 本地硬盘中确实找不到此图片 [{image_id_arg}]。"
            "凭证可能已过期（缓存保留 20 条记录），或图片已被清理。"
            "请停止搜索，直接向用户宣告：无法分析此图片。"
        )
        _log.error(msg)
        return [TextContent(type="text", text=msg)]

    return mcp_server


async def _run_mcp_server_async():
    """在 asyncio 事件循环中运行 MCP stdio 服务器。"""
    from mcp import stdio_server

    mcp_server = _build_mcp_server()

    async with stdio_server() as (read_stream, write_stream):
        await mcp_server.run(
            read_stream,
            write_stream,
            mcp_server.create_initialization_options(),
        )


def _run_mcp_server():
    """同步入口，启动 asyncio 事件循环运行 MCP 服务器。"""
    try:
        asyncio.run(_run_mcp_server_async())
    except KeyboardInterrupt:
        _log.info("MCP Server 收到 Ctrl+C。")


# ═══════════════════════════════════════════════════════════════════
#  热键监听 — 使用 GlobalHotKeys（官方推荐，无需手动状态机）
# ═══════════════════════════════════════════════════════════════════

def _start_hotkey_listener():
    """
    使用 pynput.keyboard.GlobalHotKeys 注册热键。
    内部自动维护状态机，不依赖手动 Listener.canonical()。
    热键回调在独立线程中执行，不阻塞热键注册线程。
    """
    hotkey = keyboard.GlobalHotKeys({"<ctrl>+<alt>+v": _on_hotkey})
    hotkey.start()
    _log.info("GlobalHotKeys 已注册: <ctrl>+<alt>+v")
    hotkey.join()


# ── HTTP 工具接口 (可选备选方案) ──────────────────────────────────

def _run_http_server():
    """
    HTTP 备选接口。当 Claude Code 不支持 MCP 时，可作为 fallback。
    启动后访问 http://localhost:18792/analyze?path=xxx
    """
    try:
        from http.server import HTTPServer, BaseHTTPRequestHandler
        import urllib.parse
    except ImportError:
        _log.warning("HTTP server 不可用。")
        return

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/analyze":
                self.send_error(404, "Not Found")
                return

            params = urllib.parse.parse_qs(parsed.query)
            image_id = params.get("id", [params.get("image_id", [None])[0]])[0]
            if not image_id:
                self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(b'{"error": "missing id parameter"}')
                return

            image_id = image_id.strip("'\"")
            _log.info("HTTP 收到 /analyze 请求 (凭证ID: %s)", image_id)

            png_path, txt_path = _static_paths(image_id)

            # 级别1: 读 txt 缓存
            if txt_path.exists():
                try:
                    content = txt_path.read_text(encoding="utf-8")
                    if content.strip():
                        _log.info("HTTP 命中 txt 缓存: %s", txt_path)
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json; charset=utf-8")
                        self.end_headers()
                        self.wfile.write(json.dumps({
                            "image_id": image_id,
                            "png_path": str(png_path),
                            "result": content,
                            "source": "cache"
                        }, ensure_ascii=False).encode("utf-8"))
                        return
                except Exception as e:
                    _log.warning("HTTP 读取缓存失败: %s", e)

            # 级别2: 调 API
            if png_path.exists():
                result = analyze_image_vision_api(str(png_path))
                try:
                    txt_path.write_text(result, encoding="utf-8")
                except Exception:
                    pass
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "image_id": image_id,
                    "png_path": str(png_path),
                    "result": result,
                    "source": "api"
                }, ensure_ascii=False).encode("utf-8"))
                return

            # 级别3: 异常
            self.send_response(404)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({
                "error": f"image not found for id: {image_id}"
            }, ensure_ascii=False).encode("utf-8"))

        def log_message(self, format, *args):
            _log.debug(format, *args)

    try:
        httpd = HTTPServer(("127.0.0.1", MCP_PORT), _Handler)
        _log.info("HTTP 工具服务已启动: http://127.0.0.1:%d/analyze", MCP_PORT)
        httpd.serve_forever()
    except Exception as e:
        _log.error("HTTP 服务启动失败: %s", e)


# ── VBS 启动脚本生成 ─────────────────────────────────────────────

def _regen_vbs():
    """重新生成 VBS 启动脚本（读取 config 中的 pythonw_path）。"""
    script_path = SCRIPT_DIR / "start_vision_proxy.vbs"
    vbs_content = (
        f'CreateObject("WScript.Shell").Run "{PYTHONW_PATH} {SCRIPT_DIR.as_posix()}/main.py", 0, False\n'
    )
    try:
        script_path.write_text(vbs_content, encoding="utf-8")
        _log.info("已重新生成 VBS 启动脚本: %s", script_path)
        print(f"已生成: {script_path}")
        print(f"内容: {vbs_content.strip()}")
        print()
        print("提示: 如需开机自启动，请将以下 VBS 文件复制到启动文件夹:")
        startup_folder = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        print(f"  启动文件夹: {startup_folder}")
        print(f"  复制命令: copy \"{script_path}\" \"{startup_folder}\\start_vision_proxy.vbs\"")
    except Exception as e:
        _log.error("生成 VBS 失败: %s", e)
        print(f"生成 VBS 失败: {e}")


# ── Windows 计划任务管理（开机自启动） ───────────────────────────

def _get_python_executable() -> str:
    """获取当前 Python 可执行文件路径（兼容虚拟环境）。"""
    import pathlib
    exe = pathlib.Path(sys.executable)
    return str(exe)


_TASK_NAME = "CtrlAltV_VisionProxy"


def _install_autostart() -> bool:
    """
    注册开机自启动。
    优先方案：复制到用户启动文件夹（无需管理员权限）。
    备选方案：Windows 计划任务（需要管理员权限）。
    用户登录时自动在后台静默启动热键服务。
    """
    if sys.platform != "win32":
        print("[错误] 开机自启动功能仅支持 Windows 系统。")
        return False

    python_exe = _get_python_executable()
    main_py = str(SCRIPT_DIR / "main.py")
    task_name = _TASK_NAME

    # ── 方案A: 启动文件夹（无管理员权限要求，推荐） ──────────────
    startup_dir = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    vbs_path = SCRIPT_DIR / "start_vision_proxy.vbs"

    # 生成 VBS 脚本（直接拼接命令，无需引号转义）
    vbs_content = (
        f'CreateObject("WScript.Shell").Run '
        f'"{python_exe} {main_py}", 0, False\n'
    )
    try:
        vbs_path.write_text(vbs_content, encoding="utf-8")
    except Exception as e:
        _log.error("生成 VBS 失败: %s", e)
        print(f"[错误] 生成 VBS 失败: {e}")
        return False

    try:
        startup_vbs = startup_dir / "start_vision_proxy.vbs"
        import shutil
        shutil.copy2(vbs_path, startup_vbs)
        print(f"[OK] 已注册开机自启动（启动文件夹）")
        print(f"     文件: {startup_vbs}")
        print(f"     python: {python_exe}")
        print(f"     脚本: {main_py}")
        return True
    except Exception as e:
        _log.warning("复制到启动文件夹失败: %s", e)

    # ── 方案B: Windows 计划任务（需要管理员权限） ────────────────
    print(f"[提示] 启动文件夹写入失败，尝试注册计划任务（需要管理员权限）...")
    try:
        import subprocess, tempfile

        ps_script = (
            f"$ErrorActionPreference = 'Stop'\n"
            f"$action = New-ScheduledTaskAction -Execute '{python_exe.replace(chr(92), '\\\\')}' "
            f"-Argument '\"{main_py.replace(chr(92), '\\\\')}\"'\n"
            f"$trigger = New-ScheduledTaskTrigger -AtLogOn\n"
            f"$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries "
            f"-DontStopIfGoingOnBatteries -StartWhenAvailable -Hidden\n"
            f"$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME "
            f"-LogonType Interactive -RunLevel Limited\n"
            f"Register-ScheduledTask -TaskName '{task_name}' -Action $action "
            f"-Trigger $trigger -Settings $settings -Principal $principal "
            f"-Description 'Ctrl+Alt+V 图片粘贴助手' -Force\n"
            f"Write-Output 'OK'\n"
        )
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".ps1", delete=False, encoding="utf-8-sig"
        ) as f:
            f.write(ps_script)
            temp_path = f.name

        try:
            result = subprocess.run(
                ["powershell", "-ExecutionPolicy", "Bypass", "-File", temp_path],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=30,
            )
            output = (result.stdout + result.stderr).strip()
            if "OK" in output or result.returncode == 0:
                print(f"[OK] 已注册开机自启动（计划任务: {task_name}）")
                return True
            else:
                print(f"[错误] 计划任务注册也失败了: {output}")
                return False
        finally:
            try:
                os.unlink(temp_path)
            except Exception:
                pass
    except Exception as e:
        print(f"[错误] 注册失败: {e}")

    return False


def _uninstall_autostart() -> bool:
    """卸载开机自启动（删除启动文件夹快捷方式和计划任务）。"""
    if sys.platform != "win32":
        print("[错误] 开机自启动功能仅支持 Windows 系统。")
        return False

    task_name = _TASK_NAME
    removed = False

    # 删除启动文件夹中的 VBS
    try:
        startup_dir = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        startup_vbs = startup_dir / "start_vision_proxy.vbs"
        if startup_vbs.exists():
            startup_vbs.unlink()
            print(f"[OK] 已删除启动文件夹中的启动脚本")
            removed = True
    except Exception as e:
        print(f"[提示] 删除启动文件夹文件: {e}")

    # 删除计划任务
    try:
        import subprocess, tempfile

        ps_script = (
            f"$ErrorActionPreference = 'SilentlyContinue'\n"
            f"$task = Get-ScheduledTask -TaskName '{task_name}'\n"
            f"if ($task) {{\n"
            f"    Unregister-ScheduledTask -TaskName '{task_name}' -Confirm:$false\n"
            f"    Write-Output 'DELETED'\n"
            f"}} else {{\n"
            f"    Write-Output 'NOT_FOUND'\n"
            f"}}\n"
        )
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".ps1", delete=False, encoding="utf-8-sig"
        ) as f:
            f.write(ps_script)
            temp_path = f.name

        try:
            result = subprocess.run(
                ["powershell", "-ExecutionPolicy", "Bypass", "-File", temp_path],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=30,
            )
            output = (result.stdout + result.stderr).strip()
            if "DELETED" in output:
                print(f"[OK] 已删除计划任务: {task_name}")
                removed = True
        finally:
            try:
                os.unlink(temp_path)
            except Exception:
                pass
    except Exception as e:
        pass

    if removed:
        print(f"[OK] 开机自启动已完全清除")
    else:
        print(f"[提示] 未检测到已注册的开机自启动")

    return True


# ── 主入口 ───────────────────────────────────────────────────────

def main():
    # ── 自启动管理命令 ────────────────────────────────────────────
    if len(sys.argv) > 1 and sys.argv[1] == "--install":
        _install_autostart()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "--uninstall":
        _uninstall_autostart()
        return
    # ── --regen-vbs: 重新生成 VBS 启动脚本 ──────────────────────
    if len(sys.argv) > 1 and sys.argv[1] == "--regen-vbs":
        _regen_vbs()
        return
    # 基础信息显示
    if VISION_API_KEY:
        masked = (VISION_API_KEY[:6] + "****" + VISION_API_KEY[-4:]
                  if len(VISION_API_KEY) > 10 else "****")
        vision_desc = f"视觉 API [{VISION_MODEL}] (Key: {masked})"
    else:
        vision_desc = "未配置 API Key（仅注入路径，MCP 工具可手动触发）"

    print("=" * 60)
    print("  Ctrl+Alt+V 图片粘贴助手 (视觉代理架构 v4.0 - ID凭证机制)")
    print("  缓存目录:", CACHE_DIR)
    print("  配置文件:", CONFIG_FILE)
    print("  视觉感知:", vision_desc)
    print("  工具服务: MCP over Stdio (内置)")
    print("  HTTP 服务: http://127.0.0.1:%d/analyze (可选)" % MCP_PORT)
    print("=" * 60)
    print("[*] 程序已启动")
    print("[*] Ctrl+Alt+V → 捕获剪贴板图片并注入视觉凭证 ID (极速响应)")
    print("[*] MCP 工具 analyze_local_image 已注册，静默监听工具调用")
    print("[*] (按 Ctrl+C 退出)\n")

    # 启动后台预分析线程（daemon，不阻塞）
    bg_thread = threading.Thread(
        target=_preanalysis_worker, daemon=True, name="PreAnalysisWorker"
    )
    bg_thread.start()
    _log.info("后台预分析线程已启动。")

    # ── 三种运行模式 ──────────────────────────────────────────────

    if len(sys.argv) > 1 and sys.argv[1] == "--mcp":
        # 模式1: 仅 MCP 服务器（Claude Code 集成专用）
        _log.info("以 MCP 独立模式启动（无热键监听）。")
        print("[*] MCP Server 模式运行中，等待工具调用...\n")
        _run_mcp_server()

    elif len(sys.argv) > 1 and sys.argv[1] == "--http":
        # 模式2: 仅 HTTP 服务器（备选方案）
        _log.info("以 HTTP 模式启动（无热键监听）。")
        print("[*] HTTP Server 模式运行中...\n")
        http_thread = threading.Thread(target=_run_http_server, daemon=True, name="HTTPServer")
        http_thread.start()
        http_thread.join()

    else:
        # 模式3: 双进程合一（热键 + MCP 同时运行）
        # MCP stdio 通过独立线程运行，不占用主线程
        shutdown_event = threading.Event()

        async def _mcp_server_task():
            """MCP Stdio 服务器异步任务。"""
            from mcp import stdio_server
            mcp_server = _build_mcp_server()
            async with stdio_server() as (read_stream, write_stream):
                await mcp_server.run(
                    read_stream,
                    write_stream,
                    mcp_server.create_initialization_options(),
                )

        def _run_mcp_daemon():
            """在独立线程中运行 MCP 服务器，不干扰热键主线程。"""
            try:
                asyncio.run(_mcp_server_task())
            except KeyboardInterrupt:
                pass
            except Exception as e:
                _log.error("MCP 服务器异常: %s", e)
            finally:
                shutdown_event.set()

        print("[*] 双模式运行: 热键监听 + MCP Stdio 服务")
        print("[*] Ctrl+Alt+V → 捕获剪贴板图片并注入视觉凭证 ID")
        print("[*] MCP 工具 analyze_local_image 已注册，等待 Claude Code 调用")
        print("[*] (按 Ctrl+C 退出)\n")

        # 启动 MCP 服务器线程（daemon，确保主进程退出时自动终止）
        mcp_thread = threading.Thread(target=_run_mcp_daemon, daemon=True, name="MCPServer")
        mcp_thread.start()

        # 主线程：热键监听（会阻塞到这里，直到收到 Ctrl+C）
        try:
            _start_hotkey_listener()
        finally:
            shutdown_event.set()
            _log.info("热键监听已停止，程序退出。")


if __name__ == "__main__":
    main()
