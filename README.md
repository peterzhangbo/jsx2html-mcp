# jsx2html-full

A Claude Code skill that converts React Artifacts (JSX) into self-contained, fully offline HTML files.

## Features

- **100% offline output** — every dependency is inlined, no CDN fallback
- **`file://` compatible** — open directly in any browser, no server needed
- Covers ~90% of typical Artifacts: `react`, `react-dom`, `tailwind`, `lucide-react`, `recharts`, `framer-motion`, `react-icons`, `clsx`, `date-fns`, `react-router-dom`
- Single-file and batch (project directory) modes
- Runs in ~1 second

## Installation

Install via Claude Code skill manager, or clone this repo into your skills directory:

```bash
git clone https://github.com/peterzhangbo/jsx2html-full \
  ~/.claude/skills/jsx2html-full
```

## Usage

Trigger phrases (English or Chinese):

> "转 HTML" · "归档 Artifact" · "导出原型" · "convert react artifact" · "jsx2html" · "save/archive/share a React Artifact"

Claude will automatically run the conversion and return the output file path.

### Manual CLI usage

**Single file** (JSX or HTML):

```bash
python3 scripts/convert.py /path/to/input.html -o ./dist/input.html --mode full
```

**Project directory** (auto-zips if >1 output file):

```bash
python3 scripts/convert.py /path/to/project -o ./dist --mode full --batch
```

Output is always written to `./dist/`.

## How it works

1. The skill receives your React/JSX source (from a Claude Artifact or a file)
2. `scripts/convert.py` resolves all imports, inlines UMD builds from the local `vendor/` cache, compiles JSX via Babel standalone, and wraps everything in a single `<html>` file
3. The resulting file has zero external dependencies and works offline

## MCP 工具

除 Claude Code 技能外，本项目同时提供一个 **MCP 服务**，让 Claude Desktop、Cursor 等任何兼容 MCP 的客户端都能直接调用转换功能，无需经过技能机制。

### 优势

模型可在 **2 轮**内完成转换（技能方式需要 3 轮）：

```
① 调用 jsx2html_convert(jsx_code="...", title="MyApp")
   → {"output_path": "~/Desktop/MyApp.html", "size_kb": 812, ...}
② 告知用户文件已保存
```

### 环境要求

- Python 3.10+
- Node.js（JSX 编译所需）

### 安装

```bash
# 克隆仓库
git clone https://github.com/peterzhangbo/jsx2html-full ~/tools/jsx2html-mcp
cd ~/tools/jsx2html-mcp

# 安装依赖（选其一）
pip install -e .
# 或
uv pip install -e .
```

### 配置 Claude Desktop

编辑 `~/Library/Application Support/Claude/claude_desktop_config.json`：

```json
{
  "mcpServers": {
    "jsx2html": {
      "command": "python3",
      "args": ["/Users/你的用户名/tools/jsx2html-mcp/server.py"]
    }
  }
}
```

使用 `uv run`（无需 install）：

```json
{
  "mcpServers": {
    "jsx2html": {
      "command": "uv",
      "args": ["run", "--with", "mcp", "/Users/你的用户名/tools/jsx2html-mcp/server.py"]
    }
  }
}
```

### 工具：`jsx2html_convert`

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `jsx_code` | string | 是 | — | 完整 JSX/React 源码 |
| `output_path` | string | 否 | `~/Desktop/<title>.html` | 输出路径（支持 `~`） |
| `title` | string | 否 | `"React Artifact"` | HTML 标题及默认文件名 |
| `mode` | `"full"\|"fast"` | 否 | `"full"` | full = 完全离线；fast = 缺失依赖降级为 CDN |

**返回值**：

```json
{
  "output_path": "/Users/saul/Desktop/MyApp.html",
  "size_kb": 812,
  "fully_offline": true,
  "tailwind": true,
  "inlined_deps": ["lucide-react", "recharts"],
  "degraded_deps": []
}
```

### 验证安装

```bash
# 启动 MCP Inspector 进行交互测试
mcp dev /path/to/jsx2html-mcp/server.py
```

## Project structure

```
jsx2html-full/
├── SKILL.md          # Skill definition read by Claude Code
├── server.py         # MCP server entry point
├── pyproject.toml    # Python package manifest
├── scripts/
│   └── convert.py    # Core conversion script
└── assets/
    ├── template.html # HTML shell template
    └── vendor/       # Pre-bundled UMD dependency cache
```

## License

MIT
