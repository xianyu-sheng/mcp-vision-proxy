# DeepSeek E2E 端到端实测报告

**测试时间**: 2026-05-08 14:37:48
**DeepSeek 模型**: `deepseek-chat`
**DeepSeek API**: `https://api.deepseek.com/anthropic`
**视觉模型**: `doubao-seed-2-0-code-preview-260215`
**视觉 API**: `https://ark.cn-beijing.volces.com/api/v3`

## 总体结果

| 指标 | 数值 |
|------|------|
| 总测试用例 | 26 |
| 通过 | 26 |
| 失败 | 0 |
| 跳过 | 0 |
| 通过率 | 100.0% |

## MCP 工具 Schema 合规性

- [PASS] T-SCHEMA-1 工具名称正确
- [PASS] T-SCHEMA-2 工具 description 完整
- [PASS] T-SCHEMA-3 inputSchema 格式正确
- [PASS] T-SCHEMA-4 image_path 参数定义正确
- [PASS] T-SCHEMA-5 required 字段正确
- [PASS] T-SCHEMA-6 路径解析逻辑存在
- [PASS] T-SCHEMA-7 错误处理返回优雅降级

## Round 1: C++ Raft RPC 代码分析

- [PASS] R1-V1 视觉API解析代码图片 — 1707 字符
- [PASS] R1-V2 视觉输出包含代码关键标识 — ['AppendEntries', 'prevLogIndex', 'LogEntry', 'raft', 'commitIndex', 'leaderCommit']
- [PASS] R1-D1 DeepSeek 识别并调用工具 — 1 次调用
- [PASS] R1-D2 DeepSeek 最终回答非空 — 3589 字符
- [PASS] R1-D3 DeepSeek 回答包含代码相关内容 — ['prevLogIndex', 'commitIndex', 'AppendEntries', 'log', '越界', '边界', 'index', 'term', 'leader']

**视觉模型输出摘要** (前 500 字符):
```
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
    int prevLogIndex;  // @B @@@ A @@@@@@@@@@@@@
    int prevLogTerm;   // prevLogIndex @@@@@@ term
    std::vector<LogEntry> entries;
    int leaderCommit;  // leader @@@@@@
};

Ap
... (截断)
```

**DeepSeek 最终回复摘要** (前 500 字符):
```
好的，我已经获取到图片中的代码内容。下面我来详细分析这段 **Raft AppendEntries RPC 处理函数** 中的逻辑问题：

---

## 🔍 逻辑问题分析

### 1️⃣ **核心 Bug：`commitIndex` 更新未限制在日志长度范围内**

```cpp
if (args.leaderCommit > commitIndex) {
    commitIndex = args.leaderCommit;  // ← 错误点
    applyMessages();
}
```

**问题描述：**
- 代码直接将 `commitIndex` 设置为 `args.leaderCommit`，**没有与当前节点日志长度取最小值**。
- 根据 Raft 论文（Figure 2）的规则：  
  > `commitIndex = min(leaderCommit, index of last new entry)`

**后果：**
- 如果 `args.leaderCommit` 大于当前节点 `log.size()`（例如 leader 已提交到索引 10，
... (截断)
```

## Round 2: 物理实验图表数据读取

- [PASS] R2-V1 视觉API解析图表 — 670 字符
- [PASS] R2-V2 视觉输出包含图表数据标识 — ['s (m)', 't (s)', 'm', 's', 'free fall', 'tracker', 'displacement', 'time', '0.0', '0.1', '0.5', '1.0', 'g', 'free', 'fall', 'data']
- [PASS] R2-D1 DeepSeek 识别并调用工具 — 1 次调用
- [PASS] R2-D2 DeepSeek 给出了物理推导 — ['g', '重力', '加速度', '9.', 's =', '0.5', '加速度']
- [PASS] R2-D3 DeepSeek 给出了 g 值估算 — g ≈ 9.754

**视觉模型输出摘要** (前 500 字符):
```
# Tracker Video Analysis — Free Fall Experiment

## 图表
- **横轴**：Time t (s)，范围0.0 - 1.0  
- **纵轴**：Displacement s (m)，范围0.0 - 5.0  
- **图例**：  
  - 蓝色点：Measured data  
  - 橙色线：Theoretical fit  


## 测量数据表（Measured Data）
| t (s) | s (m)  | v (m/s) |
|-------|--------|---------|
| 0.0   | 0.011  | 0.00    |
| 0.1   | 0.011  | 0.11    |
| 0.2   | 0.178  | 0.89    |
| 0.3   | 0.419  | 1.40    |
| 0.4   | 0.803  | 2.01    |
| 0.5   | 1.239  | 2.48    |
| 0.6   | 1.795  | 2.98    |
| 0.7   | 2.368  | 3
... (截断)
```

**DeepSeek 最终回复摘要** (前 500 字符):
```
好的，我已经获取了图表数据。现在让我用 **s = 0.5 × g × t²** 模型来计算重力加速度的实验值。

## 计算过程

根据模型公式：
$$s = \frac{1}{2} g t^2$$

我们可以通过拟合数据来求 g。使用最小二乘法，将公式改写为：
$$s = k \cdot t^2 \quad \text{其中} \quad k = \frac{g}{2}$$

### 方法一：利用最后一点（t=1.0s, s=4.877m）估算

$$g = \frac{2s}{t^2} = \frac{2 \times 4.877}{1.0^2} = 9.754 \ \text{m/s}^2$$

### 方法二：利用多个数据点进行线性拟合

将 s 对 t² 做线性回归：

| t² (s²) | s (m) |
|---------|-------|
| 0.00    | 0.011 |
| 0.01    | 0.011 |
| 0.04    | 0.178 |
| 0.09    | 0.419 |
| 0.16    | 0.803 |
| 0.25    | 1.
... (截断)
```

## Round 3: 边界与异常阻断

- [PASS] R3-2-V1 无效API Key触发认证错误 — 认证/权限错误 (HTTP 401)
- [PASS] R3-2-V2 错误码符合预期 (401/403) — 认证/权限错误 (HTTP 401)
- [PASS] R3-2-D1 DeepSeek 处理无效 Key 不崩溃 — 正常响应
- [PASS] R3-2-D2 DeepSeek 尝试调用工具 — 1 次调用
- [PASS] R3-2-D3 DeepSeek 收到优雅降级提示 — ['无法', '错误', '不支持', '无法获取']
  ### 子测试: 损坏图片文件
  - [PASS] R3-1-V1 视觉API处理损坏图片返回错误 — 连续 3 次调用均失败: HTTP 错误 400: 400 Client Error: Bad Request for url: https://ark.cn-beijing.volces.com/api/v3/chat/completions
  - [PASS] R3-1-D1 DeepSeek 处理损坏图片不崩溃 — 正常响应
  - [PASS] R3-1-D2 DeepSeek 尝试调用工具 — 1 次调用
  - [PASS] R3-1-D3 DeepSeek 收到降级提示 — ['无法', '错误', '无法获取', '不支持']

## 结论

**全部测试通过！** 工具链在 DeepSeek 环境下运行正常。