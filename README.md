# TeamSpeak 3 游戏陪玩 Bot

一个功能丰富的 TeamSpeak 3 机器人，支持音乐播放、AI 聊天、自动欢迎等功能。

## 功能特性

- **音乐播放**: 支持网易云音乐搜索和播放，使用 FFmpeg 进行音频处理
- **AI 聊天**: 集成 OpenAI API，提供智能对话能力
- **自动欢迎**: 新用户加入时自动发送欢迎消息
- **命令系统**: 完整的命令支持（`!help`, `!play`, `!stop`, `!volume` 等）
- **跨平台支持**: 可在 macOS、Linux 和 Docker 环境中运行

## 快速开始

### 环境要求

- Python >= 3.12
- FFmpeg（音频处理）
- Docker 和 Docker Compose（可选，用于容器化部署）
- TeamSpeak 3 服务器访问权限

### 方式一：本地运行（macOS/Linux）

#### 1. 安装依赖

```bash
# macOS
brew install ffmpeg

# Linux (Ubuntu/Debian)
sudo apt update && sudo apt install -y ffmpeg

# 或使用你的包管理器安装 FFmpeg
```

#### 2. 克隆项目并安装 Python 依赖

```bash
cd Teamspeak3_Bot
pip install -r requirements.txt
```

#### 3. 配置环境变量

创建 `.env` 文件（参考 `.env.example`）：

```bash
TS3_HOST=your_ts3_server_host
TS3_PASSWORD=your_serveradmin_password
OPENAI_API_KEY=your_openai_api_key
OPENAI_API_BASE=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini
```

#### 4. 修改配置文件（可选）

编辑 `config/config.yaml`，根据需求调整配置：

```yaml
audio:
  ffmpeg_path: null  # null 表示自动检测，或设置为具体路径

chat:
  model: "gpt-4o-mini"
  temperature: 0.8
```

#### 5. 启动 Bot

```bash
python -m bot
```

### 方式二：Docker 部署（推荐）

#### 1. 构建镜像

```bash
docker compose build
```

#### 2. 配置环境变量

创建 `.env` 文件：

```bash
TS3_HOST=your_ts3_server_host
TS3_PASSWORD=your_serveradmin_password
OPENAI_API_KEY=your_openai_api_key
WEBHOOK_PORT=8080
```

#### 3. 启动服务

```bash
docker compose up -d
```

#### 4. 查看日志

```bash
docker compose logs -f ts3bot
```

#### 5. 停止服务

```bash
docker compose down
```

## 配置说明

### 环境变量（.env）

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `TS3_HOST` | TeamSpeak 3 服务器地址 | 必填 |
| `TS3_PASSWORD` | ServerAdmin 密码 | 必填 |
| `OPENAI_API_KEY` | OpenAI API 密钥 | 必填 |
| `OPENAI_API_BASE` | OpenAI API 地址 | `https://api.openai.com/v1` |
| `OPENAI_MODEL` | 使用的模型 | `gpt-4o-mini` |
| `WEBHOOK_PORT` | Webhook 端口 | `8080` |

### 配置文件（config/config.yaml）

主要配置项：

- **ts3**: TeamSpeak 3 连接配置（地址、端口、昵称等）
- **audio**: 音频配置（音量、FFmpeg 路径、缓存目录）
- **netease**: 网易云音乐配置（搜索限制、音质）
- **chat**: AI 聊天配置（API、模型、温度等）
- **automation**: 自动化功能（欢迎消息、跟随模式等）
- **webhook**: Webhook 服务配置
- **logging**: 日志配置

## 可用命令

| 命令 | 说明 |
|------|------|
| `!help` | 显示帮助信息 |
| `!play <歌曲名>` | 搜索并播放音乐 |
| `!stop` | 停止播放 |
| `!pause` | 暂停播放 |
| `!resume` | 继续播放 |
| `!volume <0-100>` | 调整音量 |
| `!skip` | 跳过当前歌曲 |
| `!queue` | 查看播放队列 |

## 项目结构

```
Teamspeak3_Bot/
├── bot/                    # 主程序
│   ├── __main__.py        # 入口文件
│   ├── app.py             # 应用主逻辑
│   ├── config.py          # 配置管理
│   └── core/              # 核心模块
│       ├── audio/         # 音频处理（FFmpeg 控制器）
│       ├── serverquery/   # TS3 ServerQuery 客户端
│       └── ...
├── config/
│   └── config.yaml        # 运行时配置文件
├── docker/
│   ├── entrypoint.sh      # Docker 入口脚本
│   ├── supervisord.conf   # 进程管理配置
│   ├── pulseaudio/        # PulseAudio 配置
│   └── ts3client/         # TS3 客户端相关
├── Dockerfile             # Docker 镜像构建文件
├── docker-compose.yml     # Docker Compose 配置
├── requirements.txt       # Python 依赖
└── pyproject.toml         # 项目配置
```

## 跨平台支持

### macOS

- FFmpeg 通过 Homebrew 安装，自动检测路径
- 音频输出使用 `audiotoolbox` 格式
- 适合本地开发和测试

### Linux

- FFmpeg 路径通常为 `/usr/bin/ffmpeg`
- 音频输出使用 PulseAudio
- 适合生产环境直接部署

### Docker

- 内置 FFmpeg、PulseAudio、Xvfb 和 TS3 客户端
- 使用 Supervisor 管理多个进程
- 完整的虚拟音频和显示环境
- **推荐用于生产环境**

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

## 常见问题

### FFmpeg 路径错误

如果看到 `FileNotFoundError: '/usr/bin/ffmpeg'`：

- **macOS**: 设置 `ffmpeg_path: null` 启用自动检测
- **Linux**: 确保 FFmpeg 已安装，或设置 `ffmpeg_path: "/usr/bin/ffmpeg"`
- **Docker**: 无需配置，镜像已包含 FFmpeg

### 音频无法播放

- **macOS**: 确保使用 `audiotoolbox` 输出格式（已自动处理）
- **Linux**: 确保 PulseAudio 运行正常
- **Docker**: 检查 PulseAudio 日志：`docker compose logs -f ts3bot | grep pulse`

### 连接失败

- 检查 `.env` 中的 `TS3_HOST` 和 `TS3_PASSWORD` 是否正确
- 确认 TS3 服务器的 ServerQuery 端口（默认 10011）可访问
- 查看日志获取详细错误信息

## 许可证

本项目仅供学习和个人使用。

## 贡献

欢迎提交 Issue 和 Pull Request！
