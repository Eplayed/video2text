# video2text

抖音视频 → 标题 + 文案（ASR转写）+ AI优化，一键导出 Excel。

## 功能

- 批量采集抖音视频元数据（标题、作者、发布时间、封面、标签）
- ASR 转写视频口播文案（faster-whisper）
- AI 优化文案（可选）
- 处理结果自动写入 Excel，支持多 Sheet
- 自动维护视频内容索引（video_index.json）

## 快速开始

### 1. 配置 Cookie

复制配置文件并填入抖音 sessionid：

```bash
cp config/config.env config/config.env.local
# 编辑 config.env.local，填入 DOUYIN_SESSIONID
```

> sessionid 获取方式：浏览器登录抖音 → F12 → Application → Cookies → douyin.com → 找到 `sessionid`

### 2. 安装依赖

```bash
pip install openpyxl faster-whisper
```

### 3. 运行

```bash
# 处理 Excel 中所有未处理的链接
python main.py --excel "/path/to/抖音视频信息.xlsx" --cookie "sessionid=your_sessionid"

# 只处理指定 Sheet
python main.py --excel "/path/to/抖音视频信息.xlsx" --sheet "AI面试" --cookie "sessionid=your_sessionid"

# 指定行处理
python main.py --excel "/path/to/抖音视频信息.xlsx" --row 3 --cookie "sessionid=your_sessionid"
```

## 参数说明

| 参数 | 说明 |
|------|------|
| `--excel` | Excel 文件路径（必填） |
| `--sheet` | Sheet 名称，默认 `抖音视频数据` |
| `--cookie` | 完整 cookie 字符串或 `sessionid=xxx` |
| `--cookie-file` | 从文件读取 cookie |
| `--asr-model` | Whisper 模型：`tiny`/`base`/`small`，默认 `base` |
| `--ai-method` | AI 优化方式：`skip`/`openai_api`/`deepseek_api` |
| `--ai-key` | API Key（也可写在 config.env.local） |
| `--update-index` | 处理完成后自动更新 video_index.json（默认开启） |

## Excel 格式

每行一个视频，列含义：

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

## 输出文件

所有输出文件（处理完成的 Excel、生成的报告 Word 等）统一放在  目录，不提交 Git。



## 项目结构

```
video2text/
├── main.py              # 主程序
├── src/                 # 核心模块
│   ├── link_resolver.py  # 抖音短链接解析
│   ├── video_extractor.py # 视频信息提取
│   ├── asr.py           # ASR 转写
│   └── ai_optimizer.py  # AI 文案优化
├── config/
│   └── config.env        # 配置模板（已 gitignore）
├── logs/                 # 日志（已 gitignore）
├── output/               # 输出目录（已 gitignore）
├── temp/                 # 临时文件（已 gitignore）
└── video_index.json      # 视频索引（已 gitignore）
```

## 视频索引

处理完视频后会自动更新 `video_index.json`，包含标题和描述，用于快速检索。配合 `douyin-search` skill 使用。

## 注意事项

- 请勿将 config.env.local / *.xlsx / video_index.json 提交到 Git
- 抖音 Cookie 有时效性，过期后需重新获取
- ASR 使用 CPU 推理，建议 base 模型（Mac 实测可用）
