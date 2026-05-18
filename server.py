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
import urllib.parse
import zipfile
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


def _slugify(s: str) -> str:
    return re.sub(r"[^\w\-]", "_", s).strip("_") or "artifact"


def _dist_dir(base: Path) -> Path:
    """Return <base>/dist/, creating it if needed."""
    d = base / "dist"
    d.mkdir(parents=True, exist_ok=True)
    return d


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


def _fix_zip_name(result: dict, out_dir: Path, stem: str) -> None:
    """Rename batch zip to <stem>-design.zip and update result in-place."""
    raw = result.get("zip")
    if not raw:
        return
    actual = Path(raw)
    named = out_dir / f"{stem}-design.zip"
    if actual.exists() and actual != named:
        actual.rename(named)
    result["output_path"] = str(named)
    result["zip"] = str(named)


def _run_convert_zip(zip_path: Path, title: str, mode: str, base_dir: Path | None) -> dict:
    stem = zip_path.name.split(".")[0]
    out_dir = _dist_dir(base_dir or zip_path.parent)

    with tempfile.TemporaryDirectory(prefix="jsx2html_zip_") as tmpdir:
        extract_dir = Path(tmpdir) / "src"
        extract_dir.mkdir()

        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        children = list(extract_dir.iterdir())
        if len(children) == 1 and children[0].is_dir():
            extract_dir = children[0]

        entry, is_batch = _find_entry(extract_dir)
        out_path = out_dir if is_batch else out_dir / f"{stem}.html"
        result = _invoke_convert(entry, out_path, title, mode, batch=is_batch)
        _fix_zip_name(result, out_dir, stem)
        return result


def _run_convert_url(url: str, title: str, mode: str, base_dir: Path | None) -> dict:
    parsed = urllib.parse.urlparse(url)
    url_filename = Path(parsed.path).name or "download"

    with tempfile.TemporaryDirectory(prefix="jsx2html_url_") as tmpdir:
        download_path = Path(tmpdir) / url_filename
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                content_type = resp.headers.get("Content-Type", "")
                data = resp.read()
        except Exception as e:
            raise RuntimeError(f"jsx2html: failed to fetch {url}: {e}")

        download_path.write_bytes(data)

        is_zip = (
            "zip" in content_type
            or url_filename.endswith(".zip")
            or zipfile.is_zipfile(download_path)
        )
        is_tar = not is_zip and (
            "tar" in content_type
            or url_filename.endswith((".tar.gz", ".tgz", ".tar"))
            or tarfile.is_tarfile(download_path)
        )

        base = base_dir or Path.cwd()
        if is_zip:
            return _run_convert_zip(download_path, title, mode, base)
        elif is_tar:
            return _run_convert_tar(download_path, title, mode, base)
        else:
            stem = Path(url_filename).stem or "index"
            out_dir = _dist_dir(base)
            return _invoke_convert(download_path, out_dir / f"{stem}.html", title, mode, batch=False)


def _run_convert_file(file_path: Path, title: str, mode: str, base_dir: Path | None) -> dict:
    if zipfile.is_zipfile(file_path):
        return _run_convert_zip(file_path, title, mode, base_dir)
    if tarfile.is_tarfile(file_path):
        return _run_convert_tar(file_path, title, mode, base_dir)
    stem = file_path.stem
    out_dir = _dist_dir(base_dir or file_path.parent)
    return _invoke_convert(file_path, out_dir / f"{stem}.html", title, mode, batch=False)


def _run_convert(jsx_code: str, title: str, mode: str, base_dir: Path | None) -> dict:
    stem = _slugify(title)
    out_dir = _dist_dir(base_dir or Path.cwd())

    with tempfile.TemporaryDirectory(prefix="jsx2html_") as tmpdir:
        stripped = jsx_code.lstrip()
        ext = ".html" if stripped.lower().startswith(("<!doctype", "<html")) else ".jsx"
        input_file = Path(tmpdir) / f"input{ext}"
        input_file.write_text(jsx_code, encoding="utf-8")
        return _invoke_convert(input_file, out_dir / f"{stem}.html", title, mode, batch=False)


def _run_convert_tar(tar_gz_path: Path, title: str, mode: str, base_dir: Path | None) -> dict:
    stem = tar_gz_path.name.split(".")[0]
    out_dir = _dist_dir(base_dir or tar_gz_path.parent)

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
        out_path = out_dir if is_batch else out_dir / f"{stem}.html"
        result = _invoke_convert(entry, out_path, title, mode, batch=is_batch)
        _fix_zip_name(result, out_dir, stem)
        return result


TOOL_CONVERT = Tool(
    name="jsx2html_convert",
    description=(
        "将 React/JSX 源码、本地文件、远程 URL 或 tar.gz handoff 包转换为完全自包含的离线产物，写入磁盘后返回输出路径和元数据。"
        "单文件输出 .html；tar.gz 含多个 HTML 时批量转换并打包为 .zip。"
        "支持 React、Tailwind、lucide-react、recharts、framer-motion 等 38 个常用包的完整内联。"
        "所有外部字体（@import、<link> stylesheet）在 full 模式下均抓取内联，输出兼容 file:// 协议。"
        "输入四选一优先级：file_path > url > tar_gz_path > jsx_code。"
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "本地文件路径（支持 ~）：HTML/JSX 文件直接转换；.zip 或 .tar.gz 压缩包自动解压后走多文件流程。输出到文件所在目录的 dist/ 下",
            },
            "url": {
                "type": "string",
                "description": "远程文件的 URL（http/https）。tar.gz 压缩包自动走多文件转换流程；HTML 文件直接转换。输出到当前工作目录的 dist/ 下",
            },
            "tar_gz_path": {
                "type": "string",
                "description": "tar.gz handoff 包的本地路径（支持 ~）。包内可含多个文件，自动探测入口并转换",
            },
            "jsx_code": {
                "type": "string",
                "description": "JSX/React 源代码或 HTML 文档字符串。仅用于文件尚未落盘的场景（如模型刚生成的代码）。文件已在磁盘上时必须用 file_path，禁止读取文件内容后通过此参数传入。",
            },
            "output_path": {
                "type": "string",
                "description": "输出基础目录（支持 ~）。工具在此目录下建 dist/ 子文件夹存放产物。默认：file_path/tar_gz_path 用输入文件所在目录，url/jsx_code 用当前工作目录",
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
    file_path = arguments.get("file_path", "")
    url = arguments.get("url", "")
    if not any([jsx_code.strip(), tar_gz_path.strip(), file_path.strip(), url.strip()]):
        raise ValueError("file_path、url、tar_gz_path、jsx_code 必须提供其中一个")

    title = arguments.get("title", "React Artifact")
    mode = arguments.get("mode", "full")
    if mode not in ("full", "fast"):
        raise ValueError("mode must be 'full' or 'fast'")

    raw_output = arguments.get("output_path")
    base_dir = Path(raw_output).expanduser().resolve() if raw_output else None

    loop = asyncio.get_running_loop()
    if file_path.strip():
        resolved_file = Path(file_path).expanduser().resolve()
        result = await loop.run_in_executor(
            None, _run_convert_file, resolved_file, title, mode, base_dir
        )
    elif url.strip():
        result = await loop.run_in_executor(
            None, _run_convert_url, url.strip(), title, mode, base_dir
        )
    elif tar_gz_path.strip():
        resolved_tar = Path(tar_gz_path).expanduser().resolve()
        result = await loop.run_in_executor(
            None, _run_convert_tar, resolved_tar, title, mode, base_dir
        )
    else:
        result = await loop.run_in_executor(
            None, _run_convert, jsx_code, title, mode, base_dir
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
