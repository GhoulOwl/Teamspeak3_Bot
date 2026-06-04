# TeamSpeak 3 游戏陪玩 Bot

一个功能丰富的 TeamSpeak 3 智能机器人，基于 AI 驱动的游戏陪玩体验。支持多平台音乐播放、智能对话、自动欢迎、房间跟随等功能，通过 Docker 容器化部署实现开箱即用。

## 功能特性

- **多平台音乐播放** — 通过 yt-dlp 提取音频直链，支持网易云音乐、~~YouTube~~、Bilibili、~~SoundCloud~~ 等平台，使用 FFmpeg + PulseAudio 双 sink 架构将音频注入 TS3 频道
- **AI 智能聊天** — 集成 OpenAI 兼容 API，内置多套中文人设（陪玩群友 / 游戏研究员 / 懒散群友），按频道隔离对话上下文
- **自动化功能** — 自动欢迎新成员、自动分配身份组、房间跟随模式、定时提醒（APScheduler）、Webhook 外部通知
- **完整命令系统** — 22 个可用命令，涵盖播放控制、队列管理、AI 交互、管理员操作等
- **跨平台支持** — 可在 macOS（开发）、Linux（本地部署）和 Docker（生产环境）中运行

## 目录

- [环境要求](#环境要求)
- [部署指南](#部署指南)
  - [方式一：Docker 容器化部署（推荐）](#方式一docker-容器化部署推荐)
  - [方式二：Linux 本地部署](#方式二linux-本地部署)
- [配置参考](#配置参考)
- [可用命令](#可用命令)
- [项目结构](#项目结构)
- [开发](#开发)
- [常见问题](#常见问题)
- [许可证](#许可证)

---

## 环境要求

| 依赖项 | 版本要求 | 说明 |
|--------|---------|------|
| Python | >= 3.12 | 仅本地部署需要 |
| FFmpeg | 最新稳定版 | 音频解码处理 |
| Docker + Docker Compose | Docker 20+, Compose V2 | 仅 Docker 部署需要 |
| TeamSpeak 3 服务器 | — | 需要 ServerAdmin 权限 |

---

## 部署指南

### 方式一：Docker 容器化部署（推荐）

Docker 部署是生产环境的推荐方式。镜像内已集成完整的运行环境：Xvfb 虚拟显示、PulseAudio 虚拟声卡、Openbox 窗口管理器、TS3 Linux 客户端和 FFmpeg，无需额外安装任何系统级依赖。

#### 1. 前置准备：下载 TS3 客户端安装包

构建镜像前，需手动下载 TeamSpeak 3 Linux 客户端的 `.run` 安装文件并放置于**项目根目录**：

```bash
# 下载 TS3 客户端安装包（以 3.6.2 为例）
wget https://files.teamspeak-services.com/releases/client/3.6.2/TeamSpeak3-Client-linux_amd64-3.6.2.run
```

> **注意**：该文件约 100MB，已被 `.gitignore` 排除，不会提交到仓库。Dockerfile 中的 `TS3_CLIENT_VERSION` 构建参数需与实际下载的版本一致（默认 `3.6.2`）。

#### 2. 配置环境变量

复制 `.env.example` 为 `.env` 并填写实际值：

```bash
cp .env.example .env
```

编辑 `.env` 文件：

```ini
# ── TeamSpeak 3 服务器 ──────────────────────────
TS3_HOST=your-ts3-server.com        # TS3 服务器地址（必填）
TS3_QUERY_PORT=10011                # ServerQuery 端口（默认 10011）
TS3_VOICE_PORT=9987                 # 语音端口（默认 9987）
TS3_USERNAME=serveradmin            # ServerQuery 用户名
TS3_PASSWORD=your-password          # ServerAdmin 密码（必填）
TS3_VIRTUAL_SERVER_ID=1             # 虚拟服务器 ID
TS3_NICKNAME=MusicBot               # Bot 在 TS3 中显示的昵称
TS3_DEFAULT_CHANNEL_ID=0            # 默认频道 ID（0 为根频道）
TS3_COMMAND_PREFIX=!                # 命令前缀

# ── AI 聊天（OpenAI 兼容 API）──────────────────
OPENAI_API_KEY=your-api-key         # API 密钥（必填）
OPENAI_API_BASE=https://api.openai.com/v1  # API 地址（支持自定义）
OPENAI_MODEL=gpt-4o-mini            # 使用的模型

# ── Webhook ────────────────────────────────────
WEBHOOK_ENABLED=false               # 是否启用 Webhook
WEBHOOK_PORT=8080                   # Webhook 监听端口
WEBHOOK_SECRET=your-webhook-secret  # Webhook 验证密钥
```

#### 3. 构建镜像

```bash
docker compose build
```

构建过程会自动完成以下操作：
- 安装系统依赖（Xvfb、PulseAudio、FFmpeg、TS3 客户端运行时库等）
- 解压并安装 TS3 Linux 客户端到 `/opt/ts3client`
- 安装 Python 依赖
- 复制应用代码

> **提示**：如需指定 TS3 客户端版本，可通过构建参数覆盖：
> ```bash
> docker compose build --build-arg TS3_CLIENT_VERSION=3.6.2
> ```

#### 4. 启动服务

```bash
docker compose up -d
```

容器启动时，`entrypoint.sh` 会自动按顺序执行：
1. 初始化运行目录和 PulseAudio 配置
2. 初始化 TS3 客户端身份（首次运行自动生成）
3. 配置双 sink 音频隔离架构（`ts3bot_music` + `ts3bot_playback`）
4. 屏蔽 TS3 客户端的许可证/更新/CDN 服务器
5. 启动 Xvfb 虚拟显示和 Openbox 窗口管理器
6. 启动 PulseAudio 并加载虚拟声卡模块
7. 通过 iptables 阻止 TS3 客户端的出站 HTTP/HTTPS（仅放行 Python Bot）
8. 启动 TS3 客户端并通过 ClientQuery 自动连接到服务器
9. 启动 Python Bot 主进程

#### 5. 查看日志

```bash
# 查看容器实时日志
docker compose logs -f ts3bot

# 查看最近 100 行日志
docker compose logs --tail=100 ts3bot
```

#### 6. 停止服务

```bash
# 停止容器（保留数据卷）
docker compose down

# 停止并删除数据卷（清除缓存、身份等数据）
docker compose down -v
```

#### 数据卷说明

| 卷名 | 挂载路径 | 用途 |
|------|---------|------|
| `ts3bot-data` | `/data` | 音频缓存、日志、cookie 文件 |
| `ts3bot-identity` | `/home/ts3bot/.ts3client` | TS3 客户端身份和设置数据库 |

> `config/` 目录以只读方式挂载到 `/opt/bot/config`，用于提供 `config.yaml` 和 `cookies.txt`。

---

### 方式二：Linux 本地部署

适用于开发调试或无法使用 Docker 的场景。需手动安装系统级依赖。

#### 1. 安装系统依赖

```bash
# Ubuntu / Debian
sudo apt update
sudo apt install -y python3.12 python3.12-venv python3-pip ffmpeg

# CentOS / RHEL / Fedora
sudo dnf install -y python3.12 python3-pip ffmpeg

# macOS（仅开发环境）
brew install python@3.12 ffmpeg
```

验证安装：

```bash
python3 --version   # 需输出 Python 3.12+
ffmpeg -version     # 需输出 ffmpeg version
```

#### 2. 克隆项目并创建虚拟环境

```bash
git clone <repository-url>
cd Teamspeak3_Bot

# 创建 Python 虚拟环境
python3 -m venv .venv

# 激活虚拟环境
source .venv/bin/activate

# 安装 Python 依赖
pip install -r requirements.txt
```

#### 3. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env` 文件，填写 TS3 服务器信息和 API 密钥（各字段说明参见 [Docker 部署 - 配置环境变量](#2-配置环境变量) 部分）。

#### 4. 修改运行时配置

编辑 `config/config.yaml`，根据本地环境调整以下关键配置：

```yaml
# FFmpeg 路径：设为 null 启用自动检测，或指定具体路径
audio:
  ffmpeg_path: null           # macOS/Linux 自动检测；也可设为 /usr/bin/ffmpeg
  cache_dir: "/data/cache"    # 本地部署可改为 ./cache 等本地路径
  cache_max_mb: 500

# 日志输出路径
logging:
  file: "./logs/bot.log"      # 本地部署建议使用本地路径
```

#### 5. 启动 Bot

```bash
# 确保虚拟环境已激活
source .venv/bin/activate

# 启动 Bot
python -m bot
```

> **注意**：本地部署模式下，音频通过系统声卡输出（macOS 使用 `audiotoolbox`，Linux 使用 PulseAudio）。由于没有 TS3 Linux 客户端运行，**音频不会注入到 TS3 频道**——此模式仅适用于开发和命令逻辑调试。如需完整的音乐播放功能，请使用 Docker 部署。

---

## 配置参考

### 环境变量（.env）

| 变量名 | 说明 | 默认值 | 必填 |
|--------|------|--------|:----:|
| `TS3_HOST` | TS3 服务器地址 | — | 是 |
| `TS3_QUERY_PORT` | ServerQuery 端口 | `10011` | 否 |
| `TS3_VOICE_PORT` | 语音端口 | `9987` | 否 |
| `TS3_USERNAME` | ServerQuery 用户名 | `serveradmin` | 否 |
| `TS3_PASSWORD` | ServerAdmin 密码 | — | 是 |
| `TS3_VIRTUAL_SERVER_ID` | 虚拟服务器 ID | `1` | 否 |
| `TS3_NICKNAME` | Bot 昵称 | `MusicBot` | 否 |
| `TS3_DEFAULT_CHANNEL_ID` | 默认频道 ID | `0` | 否 |
| `TS3_COMMAND_PREFIX` | 命令前缀 | `!` | 否 |
| `OPENAI_API_KEY` | OpenAI 兼容 API 密钥 | — | 是 |
| `OPENAI_API_BASE` | API 地址 | `https://api.openai.com/v1` | 否 |
| `OPENAI_MODEL` | 使用的模型 | `gpt-4o-mini` | 否 |
| `WEBHOOK_ENABLED` | 是否启用 Webhook | `false` | 否 |
| `WEBHOOK_PORT` | Webhook 端口 | `8080` | 否 |
| `WEBHOOK_SECRET` | Webhook 验证密钥 | — | 否 |

### 运行时配置（config/config.yaml）

`config.yaml` 支持 `${ENV_VAR}` 语法引用环境变量。主要配置模块：

| 模块 | 说明 |
|------|------|
| `ts3` | TS3 服务器连接配置（地址、端口、昵称、命令前缀等） |
| `audio` | 音频管道配置（音量、淡入淡出、FFmpeg 路径、缓存策略、PulseAudio sink 名称） |
| `netease` | 音乐搜索配置（搜索数量限制、音质、cookie 文件路径） |
| `chat` | AI 聊天配置（API 地址、模型、温度、上下文窗口、人设、频道隔离） |
| `automation` | 自动化功能（欢迎消息、身份组分配、房间跟随模式） |
| `scheduler` | 定时任务（cron 表达式触发频道消息） |
| `webhook` | Webhook 服务配置（监听地址、端口、验证密钥） |
| `logging` | 日志配置（级别、格式、文件路径、轮转策略） |

### yt-dlp Cookie 配置

部分平台（网易云音乐灰色歌曲、Bilibili 大会员内容、YouTube 等）需要登录态才能获取高品质音频。通过配置 cookie 文件解决：

**获取 Cookie 文件：**

1. 安装浏览器扩展：Chrome/Edge 推荐 [Get cookies.txt LOCALLY](https://chromewebstore.google.com/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)，Firefox 推荐 [cookies.txt](https://addons.mozilla.org/en-US/firefox/addon/cookies-txt/)
2. 在浏览器中登录目标平台（网易云音乐 / Bilibili / YouTube）
3. 使用扩展导出 cookie，保存为 Netscape 格式的 `cookies.txt`
4. 将文件放置到安全目录（如 `config/cookies.txt`）

> 一个 cookie 文件可包含多个平台的 cookie，yt-dlp 会按域名自动匹配。

**配置路径：**

编辑 `config/config.yaml`：

```yaml
netease:
  cookie_file: "/data/cookies.txt"   # 指向 cookie 文件实际路径
```

**Docker 部署时**，将 cookie 文件挂载到容器中：

```yaml
# docker-compose.yml — volumes 部分
services:
  ts3bot:
    volumes:
      - ./config/cookies.txt:/data/cookies.txt:ro
```

容器启动时 `entrypoint.sh` 会自动将 `config/cookies.txt` 复制到可写的 `/data/cookies.txt`（yt-dlp 需要回写 cookie）。

> **提示**：`cookie_file` 默认为 `null`（不使用 cookie）。Cookie 文件会过期，如之前正常但突然无法播放，请重新导出。

---

## 可用命令

所有命令使用 `!` 前缀（可通过 `TS3_COMMAND_PREFIX` 自定义）。支持中文别名。

### 音乐播放

| 命令 | 别名 | 说明 | 权限 |
|------|------|------|:----:|
| `!play <歌曲名/链接>` | `!p`, `!播放`, `!点歌` | 搜索并播放歌曲，支持网易云/YouTube/B站链接 | 所有 |
| `!skip` | `!s`, `!切歌`, `!下一首` | 投票跳过当前歌曲 | 所有 |
| `!pause` | `!暂停` | 暂停播放 | 所有 |
| `!resume` | `!继续` | 继续播放 | 所有 |
| `!stop` | `!停止` | 停止播放 | 管理员 |
| `!queue` | `!q`, `!队列` | 显示播放队列 | 所有 |
| `!np` | `!正在播放`, `!当前` | 显示当前播放信息 | 所有 |
| `!lyrics` | `!lrc`, `!歌词` | 显示当前歌词 | 所有 |
| `!clear` | `!清空` | 清空播放队列 | 管理员 |
| `!shuffle` | `!随机` | 随机打乱队列 | 所有 |
| `!repeat` | `!r`, `!重复`, `!循环` | 切换重复模式（关闭/单曲/列表） | 所有 |
| `!remove <序号>` | `!rm`, `!移除`, `!删除` | 从队列移除第 N 首 | 所有 |
| `!volume <0-100>` | `!vol`, `!v`, `!音量` | 查看或调节音量 | 所有 |

### AI 聊天

| 命令 | 别名 | 说明 | 权限 |
|------|------|------|:----:|
| `!chat <消息>` | `!ask`, `!ai`, `!聊天`, `!问` | 和 AI 聊天 | 所有 |
| `!persona <名称>` | `!personality`, `!人设`, `!角色` | 切换 AI 人设 | 所有 |
| `!clearctx` | `!清上下文`, `!清记录` | 清除当前频道 AI 聊天上下文 | 所有 |

### 管理与工具

| 命令 | 别名 | 说明 | 权限 |
|------|------|------|:----:|
| `!help` | `!h`, `!帮助` | 显示帮助信息 | 所有 |
| `!ping` | — | 响应测试 | 所有 |
| `!status` | `!状态` | 显示 Bot 状态信息 | 所有 |
| `!follow` | `!跟随` | 开启/关闭房间跟随模式 | 管理员 |
| `!remind <分钟> <消息>` | `!提醒` | 设置定时提醒 | 所有 |
| `!welcome <消息>` | `!欢迎` | 设置欢迎消息 | 管理员 |

---

## 项目结构

```
Teamspeak3_Bot/
├── bot/                            # 主程序包
│   ├── __main__.py                 # 入口文件（python -m bot）
│   ├── app.py                      # 应用主逻辑（生命周期管理）
│   ├── config.py                   # 配置管理（Pydantic Settings + YAML）
│   ├── core/
│   │   ├── audio/
│   │   │   ├── controller.py       # 音频播放控制器
│   │   │   ├── ffmpeg.py           # FFmpeg 进程管理
│   │   │   └── volume.py           # 音量控制
│   │   ├── commands/
│   │   │   ├── context.py          # 命令上下文
│   │   │   ├── parser.py           # 命令解析器
│   │   │   ├── registry.py         # 命令注册表（装饰器模式）
│   │   │   └── handlers/           # 命令处理器
│   │   │       ├── music.py        #   音乐播放命令
│   │   │       ├── chat.py         #   AI 聊天命令
│   │   │       ├── volume.py       #   音量命令
│   │   │       ├── admin.py        #   管理员命令
│   │   │       └── debug.py        #   调试命令
│   │   └── serverquery/
│   │       ├── client.py           # TS3 ServerQuery 客户端
│   │       ├── protocol.py         # ServerQuery 协议实现
│   │       └── events.py           # 事件处理
│   ├── services/
│   │   ├── automation/             # 自动化服务
│   │   │   ├── welcome.py          #   欢迎消息
│   │   │   ├── groups.py           #   身份组分配
│   │   │   └── follow.py           #   房间跟随
│   │   ├── chat/                   # AI 聊天服务
│   │   │   ├── service.py          #   聊天核心
│   │   │   ├── context.py          #   上下文管理
│   │   │   └── personas.py         #   人设定义
│   │   ├── netease/                # 音乐服务
│   │   │   ├── client.py           #   网易云搜索 API
│   │   │   ├── ytdlp.py            #   yt-dlp 音频提取
│   │   │   ├── cache.py            #   缓存管理
│   │   │   └── models.py           #   数据模型
│   │   ├── queue/                  # 播放队列管理
│   │   ├── scheduler/              # 定时任务
│   │   └── tracking/               # 语音频道追踪
│   ├── utils/                      # 工具函数
│   └── web/                        # Webhook 服务
├── config/
│   ├── config.yaml                 # 运行时配置文件
│   └── cookies.txt                 # yt-dlp cookie（需自行添加）
├── docker/
│   ├── entrypoint.sh               # Docker 入口脚本（进程编排）
│   ├── supervisord.conf            # Supervisor 配置（遗留/参考）
│   ├── pulseaudio/
│   │   └── default.pa              # PulseAudio 双 sink 配置
│   └── ts3client/
│       ├── init_identity.py        # TS3 客户端身份初始化
│       └── generate_identity.py    # 身份生成工具
├── tests/                          # 测试套件
│   ├── conftest.py
│   ├── test_parser.py
│   ├── test_protocol.py
│   ├── test_queue.py
│   └── test_chat_context.py
├── Dockerfile                      # Docker 镜像构建文件
├── docker-compose.yml              # Docker Compose 服务定义
├── requirements.txt                # Python 依赖
├── pyproject.toml                  # 项目元数据与工具配置
└── .env.example                    # 环境变量模板
```

---

## 开发

### 安装开发依赖

```bash
pip install -e ".[dev]"
```

### 代码检查

```bash
ruff check .
```

### 运行测试

```bash
pytest
```

---

## 常见问题

### FFmpeg 路径错误

如果出现 `FileNotFoundError: '/usr/bin/ffmpeg'`：

- **macOS**：在 `config/config.yaml` 中设置 `ffmpeg_path: null` 启用自动检测
- **Linux**：确保 FFmpeg 已安装（`sudo apt install ffmpeg`），或设置 `ffmpeg_path: "/usr/bin/ffmpeg"`
- **Docker**：无需配置，镜像已内置 FFmpeg

### 音频无法播放

- **macOS**：音频输出使用 `audiotoolbox` 格式（已自动处理）
- **Linux**：确保 PulseAudio 正在运行
- **Docker**：检查 PulseAudio 日志：`docker compose logs -f ts3bot | grep pulse`

### TS3 连接失败

- 检查 `.env` 中的 `TS3_HOST` 和 `TS3_PASSWORD` 是否正确
- 确认 TS3 服务器的 ServerQuery 端口（默认 10011）可访问
- 查看容器日志获取详细错误信息：`docker compose logs -f ts3bot`

### Docker 构建失败：找不到 TS3 客户端文件

确保已将 `TeamSpeak3-Client-linux_amd64-3.6.2.run` 下载到**项目根目录**，文件名需与 Dockerfile 中的 `TS3_CLIENT_VERSION` 参数匹配。

### 音乐播放失败 / 需要登录

部分平台的音乐需要登录态才能获取（如网易云灰色歌曲、Bilibili 大会员内容）：

1. 在浏览器中登录对应平台
2. 使用浏览器扩展导出 Netscape 格式的 cookie 文件
3. 将文件放到 `config/cookies.txt`
4. 在 `config/config.yaml` 中确认 `netease.cookie_file` 路径正确
5. 重启 Bot

> Cookie 文件会过期，如之前正常但突然无法播放，请重新导出。

---

## 许可证

本项目仅供学习和个人使用。

## 贡献

欢迎提交 Issue 和 Pull Request！
