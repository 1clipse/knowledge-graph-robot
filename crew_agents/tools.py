"""
CrewAI 工具：UTF-8 安全文件读取。

CrewAI 官方 FileReadTool 在 Windows 上使用 open(file_path, "r")，会走系统默认
GBK 编码，读取 UTF-8 中文/emoji 文件时容易报 codec can't decode。这个工具显式
使用 UTF-8，并在必要时回退到 UTF-8-SIG / GB18030。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from crewai.tools import BaseTool
from pydantic import BaseModel, Field


class Utf8FileReadToolSchema(BaseModel):
    """UTF-8 文件读取工具参数。"""

    file_path: str = Field(..., description="要读取的项目相对路径或绝对路径")
    start_line: int | None = Field(1, description="起始行号，1-indexed")
    line_count: int | None = Field(None, description="读取行数；为空则读取到文件末尾")


class Utf8FileReadTool(BaseTool):
    """读取项目文件，显式处理 UTF-8 中文内容。"""

    name: str = "read_project_file_utf8"
    description: str = (
        "读取项目文件内容。优先传项目相对路径，例如 README.md、crew_agents/config.py。"
        "支持 start_line 和 line_count。工具使用 UTF-8/UTF-8-SIG/GB18030 解码，适合中文项目文件。"
    )
    args_schema: type[BaseModel] = Utf8FileReadToolSchema
    project_root: str

    def __init__(self, project_root: str | Path, **kwargs: Any) -> None:
        super().__init__(project_root=str(Path(project_root).resolve()), **kwargs)

    def _resolve_path(self, file_path: str) -> Path:
        root = Path(self.project_root).resolve()
        raw = Path(file_path)
        target = raw if raw.is_absolute() else root / raw
        target = target.resolve()

        # 限制读取在项目目录内，避免 Agent 随意读系统文件。
        try:
            target.relative_to(root)
        except ValueError:
            raise ValueError(f"文件不在项目目录内: {target}")
        return target

    @staticmethod
    def _read_text(path: Path) -> str:
        last_error: Exception | None = None
        for encoding in ("utf-8", "utf-8-sig", "gb18030"):
            try:
                return path.read_text(encoding=encoding)
            except UnicodeDecodeError as exc:
                last_error = exc
        raise UnicodeDecodeError(
            last_error.encoding if last_error else "utf-8",
            last_error.object if last_error else b"",
            last_error.start if last_error else 0,
            last_error.end if last_error else 0,
            f"无法使用 utf-8/utf-8-sig/gb18030 解码: {path}",
        )

    def _run(
        self,
        file_path: str,
        start_line: int | None = 1,
        line_count: int | None = None,
    ) -> str:
        try:
            path = self._resolve_path(file_path)
            if not path.exists():
                return f"Error: File not found at path: {path}"
            if not path.is_file():
                return f"Error: Path is not a file: {path}"

            text = self._read_text(path)
            lines = text.splitlines(keepends=True)
            start_line = start_line or 1
            if start_line < 1:
                return "Error: start_line must be >= 1"

            start_idx = start_line - 1
            if start_idx >= len(lines):
                return f"Error: Start line {start_line} exceeds the number of lines in the file."

            default_line_limit = 240
            was_limited = False
            if line_count is None:
                end_idx = min(len(lines), start_idx + default_line_limit)
                selected = lines[start_idx:end_idx]
                was_limited = end_idx < len(lines)
            else:
                if line_count < 0:
                    return "Error: line_count must be >= 0"
                end_idx = start_idx + line_count
                selected = lines[start_idx:end_idx]
                was_limited = end_idx < len(lines)

            output = "".join(selected)
            if was_limited:
                output += (
                    f"\n\n[读取已截断：共 {len(lines)} 行，本次返回 "
                    f"{start_idx + 1}-{start_idx + len(selected)} 行。"
                    "如需继续，请传 start_line 和 line_count。]"
                )
            return output
        except PermissionError:
            return f"Error: Permission denied when trying to read file: {file_path}"
        except Exception as exc:
            return f"Error: Failed to read file {file_path}. {exc}"
