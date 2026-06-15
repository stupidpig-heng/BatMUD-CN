# BatMUD CN

BatMUD 的中文 Web 客户端 + 实时翻译引擎。在浏览器中玩 BatMUD，英文内容通过百度大模型 API 实时翻译为中文，同时完整保留游戏原有的 ANSI 色彩。

---

## 目录

- [效果预览](#效果预览)
- [架构](#架构)
- [快速开始](#快速开始)
- [配置说明](#配置说明)
- [翻译策略](#翻译策略)
- [ANSI 颜色保留](#ansi-颜色保留)
- [缓存系统](#缓存系统)
- [调试面板](#调试面板)
- [常见问题](#常见问题)
- [项目结构](#项目结构)

---

## 效果预览

服务端发来的原始文本：

```
You are walking on a path. Type [绿色]look[/绿色] to view the
current room. [蓝色粗体]Ghost of Pypo: hello[/蓝色粗体]
```

翻译后浏览器中显示：

> 你正走在一条小路上。输入 **[绿色]look(看)[/绿色]** 来查看当前房间。
> **[蓝色粗体]Ghost of Pypo: hello（皮波的幽灵：你好）[/蓝色粗体]**

**核心规则：**
- 默认白色文本 → 只显示中文翻译
- 有颜色的关键词/对话 → 显示 `英文(中文)`，颜色与服务端一致
- 颜色边界由 ANSI 码精确控制，不会溢出

---

## 架构

```
浏览器 (WebSocket)
    ↕
127.0.0.1:8080  Python aiohttp Web 服务器
    ↕                      ↕
静态文件服务          Telnet 客户端
(index.html)         127.0.0.1 → batmud.bat.org:23
                           ↕
                      百度千帆翻译 API
                   (fanyi-api.baidu.com)
```

**数据流：**

```
BatMUD 服务器 ──→ [Telnet 协商/MCCP 过滤] ──→ [按行解析]
                                                      ↓
                                          [提取 ANSI 码 + 纯文本]
                                                      ↓
                               ┌──── 需要翻译? ────┐
                               ↓ 是                 ↓ 否
                         [拆分 ANSI 段]         [直接 ANSI→HTML]
                               ↓
                         [逐段调用 API]
                               ↓
                    [按 ANSI 状态重建字节流]
                     白色段→中文  有色段→英文(中文)
                               ↓
                         [ANSI→HTML]
                               ↓
                     [WebSocket 推送到浏览器]
```

---

## 快速开始

### 环境要求

- **Python 3.9+**
- Windows / macOS / Linux

### 1. 获取百度翻译 API 凭据

访问 [百度翻译开放平台](https://fanyi-api.baidu.com/)，注册并开通 **「大模型文本翻译API」** 服务（免费，每月 100 万字符额度）。在控制台获取你的 **APP ID** 和 **密钥**。

> ⚠️ 注意是「大模型文本翻译」不是「通用文本翻译」。只有大模型翻译才能保证游戏文本的翻译质量。

### 2. 启动

**Windows:** 双击 `run.bat`

**macOS / Linux / 命令行:**

```bash
pip install -r requirements.txt
python -m src.main
```

**首次运行**会自动弹出交互式配置界面，输入你的 APP ID 和密钥即可，凭据会保存到 `config.yaml`。之后再次启动直接跳过，无需重复输入。

```
=======================================================
  首次运行 — 请配置百度大模型翻译 API 凭据
=======================================================
  开通地址: https://fanyi-api.baidu.com/
-------------------------------------------------------
  APP ID: 20260613002631286
  密钥 (Secret Key): JQgvMF3ipFGN5_pKeyYl
  凭据已保存到 config.yaml
=======================================================
```

### 3. 连接游戏

1. 浏览器打开 `http://127.0.0.1:8080`
2. 页面自动连接游戏服务器
3. 在底部输入框输入命令，回车发送

---

## 配置说明

完整 `config.yaml` 参考：

```yaml
# ---- 远程 BatMUD 服务器 ----
server:
  host: batmud.bat.org      # 服务器地址
  port: 23                  # Telnet 端口

# ---- Web 服务器 ----
web:
  host: 127.0.0.1           # 监听地址（仅本机）
  port: 8080                # 浏览器访问端口

# ---- 百度翻译 API ----
baidu:
  app_id: ""                # 百度 APP ID
  secret_key: ""            # 百度密钥
  model_type: "llm"         # llm=大模型翻译 | nmt=机器翻译
  timeout: 15               # API 超时（秒）

# ---- 翻译设置 ----
translation:
  enabled: true             # 是否启用翻译
  cache_size: 2000          # 内存缓存条目数
  cache_file: "translation_cache.db"  # 磁盘持久化缓存
  min_chars: 4              # 少于此字符数的行不翻译
  max_batch: 3              # 批翻译最大合并行数

# ---- 日志 ----
logging:
  level: INFO               # DEBUG | INFO | WARNING | ERROR
  file: ""                  # 日志文件路径（留空=仅控制台）
  show_translations: true   # 是否在控制台打印翻译内容
```

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `server.host` | BatMUD 服务器地址 | `batmud.bat.org` |
| `server.port` | BatMUD 服务器端口 | `23` |
| `web.host` | 本地 Web 监听地址 | `127.0.0.1` |
| `web.port` | 浏览器访问端口 | `8080` |
| `baidu.app_id` | 百度 APP ID | 必填 |
| `baidu.secret_key` | 百度密钥 | 必填 |
| `baidu.model_type` | 翻译模型 | `llm` |
| `baidu.timeout` | API 超时秒数 | `15` |
| `translation.enabled` | 启用翻译 | `true` |
| `translation.cache_size` | 内存 LRU 缓存条数 | `2000` |
| `translation.cache_file` | SQLite 磁盘缓存文件 | `translation_cache.db` |
| `translation.min_chars` | 最少翻译字符数 | `4` |
| `logging.level` | 日志级别 | `INFO` |

### 模型选择

| 模型 | 特点 | 适用场景 |
|------|------|----------|
| `llm` | 大模型翻译，质量最好，理解语境 | **推荐** |
| `nmt` | 神经机器翻译，速度快 | 追求速度时使用 |

---

## 翻译策略

### 智能跳过

以下内容**不会**被翻译，直接透传：

- **状态提示行 (Prompt):** `Hp:318/318 Sp:25/25 Ep:183/183 Exp:356 >` — 客户端需要解析
- **过短文本:** 少于 `min_chars` 个字符的行
- **Telnet 协商数据:** GMCP/MSDP 等协议数据
- **纯符号/数字:** ASCII 艺术画、分隔线等

### 颜色保留规则

| 文本类型 | 显示格式 | 示例 |
|----------|----------|------|
| 默认白色文本 | 只显示中文 | `你正走在林间小径上。` |
| 有色关键词(>2字符) | `英文(中文)` | `look(看)` |
| 短指令(≤2字符) | 保留英文原样 | `n`, `s`, `i` |
| 有色对话 | `英文(中文)` | `Ghost of Pypo: hello（皮波的幽灵：你好）` |

### 游戏指令识别

单字母或双字母组合（如 `n`, `s`, `e`, `w`, `i`, `eq`, `sc`）被识别为游戏指令，不翻译、不显示双语格式，保留原样。

---

## ANSI 颜色保留

### 工作原理

项目使用**逐段颜色边界保留**算法：

1. 原始字节流按 ANSI SGR 码（`\x1b[...m`）拆分为文本段
2. 遍历每段，维护当前颜色状态（前景色、背景色、粗体、下划线等）
3. 默认色文本段 → 翻译为中文
4. 有色文本段 → 显示 `英文(中文)`，ANSI 码原样保留
5. ANSI→HTML 转换器将最终字节流渲染为带 CSS class 的 HTML

```
原始: "type [绿]look[/绿] to view"
拆分: ["type ", ANSI绿, "look", ANSI重置, " to view"]
重建: ["输入 ", ANSI绿, "look(看)", ANSI重置, " 来查看"]
HTML: 输入 <span class="c32 b">look(看)</span> 来查看
```

### 色彩方案

项目内置 16 色 ANSI 调色板（标准 8 色 + 高亮 8 色），与 BatMUD 客户端色彩一致：

```
黑 红 绿 黄 蓝 品红 青 白        ← 标准色
暗灰 亮红 亮绿 亮黄 亮蓝 亮品红 亮青 亮白 ← 高亮色
```

---

## 缓存系统

采用**两级缓存**架构，兼顾速度与持久性：

```
翻译请求
  ↓
[一级] 内存 LRU 缓存（毫秒级）
  ├── 命中 → 直接返回
  └── 未命中 ↓
[二级] SQLite 磁盘缓存（毫秒级）
  ├── 命中 → 返回 + 回填内存
  └── 未命中 ↓
百度翻译 API（数百毫秒）
  └── 结果存入内存 + 磁盘
```

- **内存缓存**: LRU 淘汰策略，默认 2000 条，进程重启后清空
- **磁盘缓存**: SQLite 数据库 `translation_cache.db`，**重启 PC 后依然保留**

例如：首次遇到 `get ring` 时调用 API 翻译为「获取戒指」，结果存入 SQLite。下次重启程序后再遇到 `get ring`，直接从 SQLite 读取，无需消耗 API 额度。

---

## 调试面板

右下角 🐛 按钮可打开调试面板，显示每行的处理细节：

```
[翻译] ANSI:4 segs:9 trans:3 "example get ring..." → "..."
```

| 字段 | 含义 |
|------|------|
| `[翻译]` / `[透传]` | 是否经过翻译 |
| `ANSI:4` | 本行有 4 个 ANSI 颜色码 |
| `segs:9` | 拆分为 9 段（ANSI + 文本） |
| `trans:3` | 其中 3 段被翻译（其余为短指令或纯符号） |
| `"... → ..."` | 原文 → 译文预览 |

服务端控制台也会同步输出含 ANSI 码的行的调试日志（INFO 级别）。

---

## 常见问题

### Q: 启动后浏览器打不开？
A: 确认 `web.port` 未被占用。默认 8080，可在 `config.yaml` 中修改。

### Q: 翻译不生效？
A: 检查：
1. 首次运行时是否正确输入了 APP ID 和密钥？可删除 `config.yaml` 重新启动再次输入
2. 百度账户是否已开通「大模型文本翻译API」（不是通用翻译）
3. 控制台是否有 `翻译API错误` 日志

### Q: API 调用失败？
A: 翻译失败时程序会降级显示原文，游戏不中断。常见原因：
- 网络问题 → 检查网络连接
- 超时 → 增大 `baidu.timeout`
- QPS 超限 → 免费版约 5 QPS，正常游戏足够
- 账户欠费 → 登录百度控制台查看

### Q: 翻译速度慢？
A: 
- 增大 `cache_size` 提升命中率
- 首次进入新区域会慢一些，之后缓存命中就快了
- 磁盘缓存让重启后也能快速加载

### Q: 颜色出问题了（溢出或丢失）？
A: 点 🐛 打开调试面板，观察有颜色行的 `ANSI` 值和输出 HTML，反馈给开发者。

### Q: 想关闭翻译看原文？
A: 设置 `translation.enabled: false`，所有内容将以原始 ANSI 颜色透传。

### Q: 日志刷屏太吵？
A: 将 `logging.level` 设为 `WARNING` 或 `ERROR`。

### Q: 数据库文件越来越大？
A: 缓存文件 `translation_cache.db` 自动生成在项目目录下，正常使用数周后约几 MB。如需清理直接删除即可，下次启动自动重建。

---

## 项目结构

```
BatMUD_CN/
├── run.bat                  # Windows 一键启动脚本
├── config.yaml              # 配置文件（首次运行自动引导填写凭据）
├── requirements.txt         # Python 依赖 (aiohttp, pyyaml)
├── README.md
│
├── src/
│   ├── main.py              # 入口：初始化配置、翻译器、Web 服务器
│   ├── config_loader.py     # 配置读取 + 首次运行交互式凭据输入
│   ├── mud_client.py        # Telnet 客户端：连接游戏、过滤 MCCP、ANSI 逐段翻译
│   ├── translator.py        # 百度 API + LRU 内存缓存 + SQLite 磁盘缓存
│   ├── parser.py            # 字节流解析：Telnet IAC、ANSI 提取、Prompt 检测
│   ├── ansi_html.py         # ANSI 转义序列 → HTML 转换器
│   └── web_server.py        # aiohttp Web 服务器：静态文件 + WebSocket
│
└── static/
    ├── index.html           # 前端页面：终端 + 输入行 + 调试面板
    ├── app.js               # WebSocket 客户端 + 终端渲染 + 调试面板
    └── style.css            # 暗色终端主题 + 16 色 ANSI 样式
```

### 核心模块说明

| 模块 | 职责 |
|------|------|
| `main.py` | 组装所有组件，处理启动/关闭信号 |
| `config_loader.py` | 读取 `config.yaml`，首次运行交互式引导输入凭据 |
| `mud_client.py` | 管理与 BatMUD 服务器的 Telnet 连接，包含核心翻译+颜色保留逻辑 |
| `translator.py` | 封装百度翻译 API，提供内存+磁盘两级缓存 |
| `parser.py` | 解析 Telnet 字节流：提取 ANSI 码、检测 Prompt、识别 GMCP |
| `ansi_html.py` | 将 ANSI 转义序列转换为带 CSS class 的 HTML span |
| `web_server.py` | HTTP 静态文件服务 + WebSocket 实时双向通信 |
| `static/*` | 浏览器端：终端模拟、WebSocket 通信、调试面板 |

---

## 致谢

- BatMUD — 1990 年上线至今的传奇 MUD 游戏
- 百度翻译开放平台 — 大模型翻译 API
- aiohttp — Python 异步 HTTP 框架
