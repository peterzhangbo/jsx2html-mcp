#!/usr/bin/env python3
"""jsx2html MCP server — exposes jsx2html as a native MCP tool."""

import asyncio
import json
import re
import shutil
import subprocess
import sys
import tarfile
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


def _find_entry(directory: Path) -> tuple[Path, bool]:
    """Return (entry_path, is_batch). Raises RuntimeError if nothing usable found."""
    html_files = sorted(directory.rglob("*.html"))
    if len(html_files) > 1:
        return directory, True
    if len(html_files) == 1:
        return html_files[0], False
    # Fall back to JSX/JS entry points
    for name in ("App.jsx", "index.jsx", "main.jsx", "App.js", "index.js", "main.js"):
        for f in directory.rglob(name):
            return f, False
    jsx_files = sorted(directory.rglob("*.jsx")) + sorted(directory.rglob("*.js"))
    if jsx_files:
        return jsx_files[0], False
    raise RuntimeError("jsx2html: no HTML or JSX entry point found in the package")


def _invoke_convert(input_path: Path, output_path: Path, title: str, mode: str, batch: bool) -> dict:
    """Run convert.py and return the result dict."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable, str(CONVERT_SCRIPT),
        str(input_path),
        "-o", str(output_path),
        "--mode", mode,
        "--title", title,
    ]
    if batch:
        cmd.append("--batch")

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

    size_kb = metadata.get("size_kb")
    if size_kb is None and output_path.exists():
        size_kb = output_path.stat().st_size // 1024

    return {
        "output_path": str(output_path),
        "size_kb": size_kb or 0,
        "fully_offline": metadata.get("fully_offline", True),
        "tailwind": metadata.get("tailwind", False),
        "inlined_deps": metadata.get("inlined_deps", []),
        "degraded_deps": metadata.get("degraded_deps", []),
    }


def _run_convert(jsx_code: str, output_path: Path, title: str, mode: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="jsx2html_") as tmpdir:
        input_file = Path(tmpdir) / "input.jsx"
        input_file.write_text(jsx_code, encoding="utf-8")
        return _invoke_convert(input_file, output_path, title, mode, batch=False)


def _run_convert_tar(tar_gz_path: Path, output_path: Path, title: str, mode: str) -> dict:
    with tempfile.TemporaryDirectory(prefix="jsx2html_tar_") as tmpdir:
        extract_dir = Path(tmpdir) / "src"
        extract_dir.mkdir()

        if not tarfile.is_tarfile(tar_gz_path):
            raise RuntimeError(f"jsx2html: not a valid tar file: {tar_gz_path}")

        with tarfile.open(tar_gz_path, "r:*") as tf:
            tf.extractall(extract_dir)

        # Unwrap single top-level directory (common in handoff packages)
        children = list(extract_dir.iterdir())
        if len(children) == 1 and children[0].is_dir():
            extract_dir = children[0]

        entry, is_batch = _find_entry(extract_dir)
        return _invoke_convert(entry, output_path, title, mode, batch=is_batch)


TOOL_CONVERT = Tool(
    name="jsx2html_convert",
    description=(
        "将 React/JSX 源码或 tar.gz handoff 包转换为完全自包含的离线 HTML 文件，写入磁盘后返回输出路径和元数据。"
        "支持 React、Tailwind、lucide-react、recharts、framer-motion 等 38 个常用包的完整内联。"
        "输出文件兼容 file:// 协议，无需网络即可打开。"
        "jsx_code 与 tar_gz_path 二选一，tar_gz_path 优先。"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "jsx_code": {
                "type": "string",
                "description": "完整的单文件 JSX/React 源代码（不支持相对 import）",
            },
            "tar_gz_path": {
                "type": "string",
                "description": "tar.gz handoff 包的本地路径（支持 ~）。包内可含多个文件，自动探测入口并转换",
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
    tar_gz_path = arguments.get("tar_gz_path", "")
    if not jsx_code.strip() and not tar_gz_path.strip():
        raise ValueError("jsx_code 或 tar_gz_path 必须提供其中一个")

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
    if tar_gz_path.strip():
        resolved_tar = Path(tar_gz_path).expanduser().resolve()
        result = await loop.run_in_executor(
            None, _run_convert_tar, resolved_tar, output_path, title, mode
        )
    else:
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
