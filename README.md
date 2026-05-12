# Ctrl+Alt+V 图片粘贴助手

> 让 AI 看见你的屏幕截图。复制图片 → 按 `Ctrl+Alt+V` → AI 自动识别并返回文字描述。

**适用场景**：代码截图、图表数据、界面截图、照片等，AI 帮你转成文字直接分析。

---

## 一键安装（Windows）

双击运行即可，全程自动完成：

```powershell
powershell -ExecutionPolicy Bypass -File setup.ps1
```

脚本会依次完成：
1. 安装 Python 依赖（Pillow、pynput、requests、mcp）
2. 创建配置文件（首次需填入 API Key）
3. 注册开机自启动（登录 Windows 后自动运行）
4. 配置 MCP 工具（Cursor 可自动识别）

---

## 填入 API Key

打开 `config.json`，将 `vision_api_key` 改为你的 Key：

```json
{
  "vision_api_key": "你的API Key"
}
```

**获取方式**：[火山引擎 ARK 平台](https://console.volcengine.com/ark) → 创建 API Key → 选择视觉模型

> 免费额度足够个人日常使用。

---

## 使用方法

1. 登录 Windows 后程序自动在后台运行
2. 在 Cursor 中与 AI 对话
3. 复制任意图片（截图、照片、剪贴板图片）
4. 按 `Ctrl+Alt+V`，AI 自动识别并回复

---

## 手动命令（可选）

| 命令 | 说明 |
|------|------|
| `python main.py` | 手动启动热键服务 |
| `python main.py --install` | 注册开机自启动（复制到启动文件夹） |
| `python main.py --uninstall` | 取消开机自启动 |
| `python main.py --regen-vbs` | 重新生成启动脚本 |

**查看启动文件夹**：文件资源管理器地址栏输入 `shell:startup` 可直接打开。

---

## 常见问题

**Q: 按 Ctrl+Alt+V 没反应？**
> 检查任务管理器中是否有 `pythonw.exe` 进程在运行。如果没有，手动运行 `python main.py`。

**Q: Cursor 没有识别到 MCP 工具？**
> 重启 Cursor，确保 `.mcp.json` 在项目根目录。

**Q: 如何更换 API Key？**
> 直接编辑 `config.json` 中的 `vision_api_key`，重启服务即可。

---

## 架构说明

```
复制图片 → Ctrl+Alt+V → 保存到临时目录 → 注入凭证ID到终端
                                            ↓
AI 检测到凭证ID → 调用 MCP 工具 → 视觉API解析 → 返回文字
```

- 热键响应 < 100ms（无网络请求）
- 图片凭证不含路径/扩展名，绕过 AI 原生图片拦截
- 视觉能力由豆包模型提供，纯文本模型（如 DeepSeek）间接获得看图能力
