# jsx2html-mcp

MCP server that converts React/JSX artifacts into self-contained, fully offline HTML files.

## Features

- **单一工具 `jsx2html_convert`** — 2 轮即完成转换，无需取指令
- **100% 离线输出** — 所有依赖内联，兼容 `file://` 协议，无需本地服务器
- 覆盖 ~90% 常见 Artifact：`react`、`react-dom`、`tailwind`、`lucide-react`、`recharts`、`framer-motion`、`react-icons`、`clsx`、`date-fns`、`react-router-dom` 等 38 个包
- 转换耗时约 1 秒

## 环境要求

- Python 3.10+
- Node.js（JSX 编译所需）

## 安装

```bash
git clone https://github.com/peterzhangbo/jsx2html-mcp ~/tools/jsx2html-mcp
cd ~/tools/jsx2html-mcp
pip install -e .
```

## 配置

### Claude Desktop

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

### Claude Code

```bash
claude mcp add jsx2html python3 /path/to/jsx2html-mcp/server.py
```

## 工具：`jsx2html_convert`

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `jsx_code` | string | 是 | — | 完整 JSX/React 源码（不支持相对 import） |
| `output_path` | string | 否 | `~/Desktop/<title>.html` | 输出路径（支持 `~`） |
| `title` | string | 否 | `"React Artifact"` | HTML 标题及默认文件名 |
| `mode` | `"full"\|"fast"` | 否 | `"full"` | `full` = 完全离线；`fast` = 缺失依赖降级为 CDN |

**返回值：**

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

## 调试

```bash
mcp dev /path/to/jsx2html-mcp/server.py
```

## Project structure

```
jsx2html-mcp/
├── server.py         # MCP server entry point
├── pyproject.toml    # Python package manifest
├── scripts/
│   └── convert.py    # Core conversion engine
└── assets/
    ├── template.html # HTML shell template
    └── vendor/       # Pre-bundled UMD dependency cache (~7MB)
```

## License

MIT
