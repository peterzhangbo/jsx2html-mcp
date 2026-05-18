#!/usr/bin/env python3
"""jsx2html MCP server — exposes jsx2html as a native MCP tool."""

import asyncio
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

PROJECT_ROOT = Path(__file__).parent.resolve()
CONVERT_SCRIPT = PROJECT_ROOT / "scripts" / "convert.py"


def _assert_prerequisites() -> None:
    missing = []
    if not CONVERT_SCRIPT.exists():
        missing.append(str(CONVERT_SCRIPT))
    babel = PROJECT_ROOT / "assets" / "vendor" / "babel.min.js"
    if not babel.exists():
        missing.append(str(babel))
    if missing:
        raise RuntimeError("jsx2html: missing files: " + ", ".join(missing))
    if not shutil.which("node"):
        raise RuntimeError("jsx2html: 'node' not found in PATH — Node.js is required")


def _default_output_path(title: str) -> Path:
    slug = re.sub(r"[^\w\-]", "_", title).strip("_") or "artifact"
    return Path("~/Desktop").expanduser() / f"{slug}.html"


def _run_convert(jsx_code: str, output_path: Path, title: str, mode: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="jsx2html_") as tmpdir:
        input_file = Path(tmpdir) / "input.jsx"
        input_file.write_text(jsx_code, encoding="utf-8")

        output_path.parent.mkdir(parents=True, exist_ok=True)

        cmd = [
            sys.executable,
            str(CONVERT_SCRIPT),
            str(input_file),
            "-o", str(output_path),
            "--mode", mode,
            "--title", title,
        ]

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            raise RuntimeError("jsx2html: conversion timed out after 120 seconds")

        if proc.returncode != 0:
            detail = (proc.stderr + "\n" + proc.stdout).strip()
            raise RuntimeError(f"jsx2html conversion failed:\n{detail}")

        metadata = {}
        try:
            metadata = json.loads(proc.stdout)
        except (json.JSONDecodeError, ValueError):
            pass

        return {
            "output_path": str(output_path),
            "size_kb": metadata.get("size_kb", output_path.stat().st_size // 1024),
            "fully_offline": metadata.get("fully_offline", True),
            "tailwind": metadata.get("tailwind", False),
            "inlined_deps": metadata.get("inlined_deps", []),
            "degraded_deps": metadata.get("degraded_deps", []),
        }


TOOL_CONVERT = Tool(
    name="jsx2html_convert",
    description=(
        "将 React/JSX 源码转换为完全自包含的离线 HTML 文件，写入磁盘后返回输出路径和元数据。"
        "支持 React、Tailwind、lucide-react、recharts、framer-motion 等 38 个常用包的完整内联。"
        "输出文件兼容 file:// 协议，无需网络即可打开。"
    ),
    inputSchema={
        "type": "object",
        "required": ["jsx_code"],
        "properties": {
            "jsx_code": {
                "type": "string",
                "description": "完整的 JSX/React 源代码（不支持相对 import，请先合并为单文件）",
            },
            "output_path": {
                "type": "string",
                "description": "输出 HTML 文件路径（支持 ~），默认为 ~/Desktop/<title>.html",
            },
            "title": {
                "type": "string",
                "description": "HTML 页面标题，也用作默认文件名。默认：React Artifact",
                "default": "React Artifact",
            },
            "mode": {
                "type": "string",
                "enum": ["full", "fast"],
                "description": "full（默认）：所有依赖在转换时内联，保证完全离线；fast：缺失依赖降级为 CDN script 标签",
                "default": "full",
            },
        },
    },
)

app = Server("jsx2html")


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [TOOL_CONVERT]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name != "jsx2html_convert":
        raise ValueError(f"Unknown tool: {name}")

    jsx_code = arguments.get("jsx_code", "")
    if not jsx_code.strip():
        raise ValueError("jsx_code must not be empty")

    title = arguments.get("title", "React Artifact")
    mode = arguments.get("mode", "full")
    if mode not in ("full", "fast"):
        raise ValueError("mode must be 'full' or 'fast'")

    raw_output = arguments.get("output_path")
    output_path = (
        Path(raw_output).expanduser().resolve()
        if raw_output
        else _default_output_path(title)
    )

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(
        None, _run_convert, jsx_code, output_path, title, mode
    )

    return [TextContent(type="text", text=json.dumps(result, ensure_ascii=False))]


async def main() -> None:
    _assert_prerequisites()
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


def main_sync() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    main_sync()
