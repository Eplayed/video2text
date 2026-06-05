# video2text

抖音视频 -> 标题 + 文案（ASR转写）+ AI优化，一键导出 Excel，并生成可供文章选题使用的视频内容索引。

## 功能

- 批量采集抖音视频元数据（标题、作者、发布时间、封面、标签）
- ASR 转写视频口播文案（faster-whisper）
- AI 优化文案（可选）
- 处理结果自动写入 Excel，支持多 Sheet
- 自动维护 `video_index.json`，用于后续筛选文章题材

## 快速开始

### 1. 准备 douyin_parse

本项目依赖 `douyin_parse` 解析抖音视频信息。默认读取 `/tmp/douyin_parse`，也可以通过环境变量或命令参数指定：

```bash
export DOUYIN_PARSE_DIR="/path/to/douyin_parse"
# 或运行时使用 --parser-dir "/path/to/douyin_parse"
```

### 2. 配置 Cookie

复制配置文件并填入抖音 `sessionid`：

```bash
cp config/config.env config/config.env.local
# 编辑 config.env.local，填入 DOUYIN_SESSIONID
```

`sessionid` 获取方式：浏览器登录抖音 -> F12 -> Application -> Cookies -> douyin.com -> 找到 `sessionid`

### 3. 安装依赖

```bash
pip install openpyxl faster-whisper
```

### 4. 运行

```bash
# 处理 Excel 中所有未处理的链接
python main.py --excel "/path/to/抖音视频信息.xlsx" --cookie "sessionid=your_sessionid"

# 只处理指定 Sheet
python main.py --excel "/path/to/抖音视频信息.xlsx" --sheet "AI面试" --cookie "sessionid=your_sessionid"

# 指定行处理
python main.py --excel "/path/to/抖音视频信息.xlsx" --row 3 --cookie "sessionid=your_sessionid"

# 使用 config/config.env.local 中的 DOUYIN_SESSIONID
python main.py --excel "output/抖音视频信息.xlsx"

# 不更新索引
python main.py --excel "output/抖音视频信息.xlsx" --no-update-index

# 查看适合整理成文章的视频候选
python scripts/list_candidates.py --topic "流放2攻略"
```

## 参数说明

| 参数 | 说明 |
|------|------|
| `--excel` | Excel 文件路径 |
| `--sheet` | Sheet 名称，默认 `抖音视频数据` |
| `--config` | 本地配置文件，默认 `config/config.env.local` |
| `--parser-dir` | `douyin_parse` 项目目录，默认读取 `DOUYIN_PARSE_DIR` 或 `/tmp/douyin_parse` |
| `--cookie` | 完整 cookie 字符串或 `sessionid=xxx` |
| `--cookie-file` | 从文件读取 cookie |
| `--asr-model` | Whisper 模型：`tiny`/`base`/`small`/`medium`/`large`，默认 `base` |
| `--ai-method` | AI 优化方式：`skip`/`openai`/`deepseek`，兼容旧写法 `openai_api`/`deepseek_api` |
| `--ai-key` | API Key（也可写在 `config.env.local`） |
| `--ai-base` | OpenAI 兼容接口 `base_url` |
| `--ai-model` | 模型名称 |
| `--update-index` / `--no-update-index` | 是否处理完成后更新 `video_index.json`，默认开启 |

## Excel 格式

每行一个视频，基础列含义：

| 列 | 字段 |
|----|------|
| A | 原始链接 |
| B | 处理状态 |
| C | 视频ID |
| D | 作者 |
| E | 发布时间 |
| F | 标题 |
| G | 视频文案（ASR） |
| H | 标签 |
| I | 封面URL |
| J | 视频链接 |
| K | 口播原文（ASR） |
| L | AI优化文案 |
| M | AI备选标题 |
| N | 关键词摘要 |
| O | 备注 |

为了让索引直接服务文章生产，可以继续追加这些列，脚本会自动识别：

| 字段 | 用途 |
|------|------|
| 选题等级 | A/B/C/已转文章 |
| 适合平台 | 头条号/公众号/双平台/不建议 |
| 文章角度 | 后续二创文章的切入角度 |
| 事实风险 | 需要联网核查或人工确认的问题 |
| Word文档路径 | 已生成文章的本地 Word 路径 |
| 是否已发布 | 发布状态 |

## 输出文件

所有输出文件（处理完成的 Excel、生成的报告 Word 等）统一放在 `output/` 目录，不提交 Git。

## 项目结构

```text
video2text/
├── main.py               # 主程序
├── scripts/
│   └── list_candidates.py # 从索引中列出文章候选
├── src/                  # 核心模块
│   ├── link_resolver.py  # 抖音短链接解析
│   ├── video_extractor.py # 视频信息提取
│   ├── asr.py            # ASR 转写
│   └── ai_optimizer.py   # AI 文案优化
├── config/
│   └── config.env        # 配置模板
├── logs/                 # 日志（已 gitignore）
├── output/               # 输出目录（已 gitignore）
├── temp/                 # 临时文件（已 gitignore）
└── video_index.json      # 视频索引（已 gitignore）
```

## 视频索引

处理完视频后会自动更新 `video_index.json`。索引面向自媒体写稿，除了标题和摘要，还会记录：

- 视频来源、封面、作者、发布时间
- 粗分类：流放2攻略、暗黑4攻略、AI技术教程等
- 选题等级：A/B/C/已转文章
- 平台建议、文章角度、事实风险
- ASR 片段长度和前 800 字
- 已生成 Word 文档路径

后续写文章时，优先从 `video_index.json` 中筛选高价值视频，再联网核查关键事实，最后生成头条号或公众号文章。

## 注意事项

- 请勿将 `config.env.local`、`output/`、`*.xlsx`、`*.docx`、`video_index.json` 提交到 Git
- 抖音 Cookie 有时效性，过期后需重新获取
- ASR 使用 CPU 推理，建议 base 模型（Mac 实测可用）
- 游戏攻略类视频 ASR 容易把专有名词识别错，写稿前必须人工或联网核查关键机制、数值和版本时间
