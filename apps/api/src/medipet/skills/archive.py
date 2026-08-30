from __future__ import annotations

import io
import json
import re
import stat
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

MAX_ARCHIVE_SIZE = 2 * 1024 * 1024
MAX_FILE_SIZE = 256 * 1024
MAX_TOTAL_SIZE = 1024 * 1024
MAX_FILES = 64

SAFE_RESOURCE_TYPES = {
    "references": {
        ".md": "text/markdown",
        ".txt": "text/plain",
    },
    "schemas": {
        ".json": "application/schema+json",
    },
    "templates": {
        ".json": "application/json",
        ".md": "text/markdown",
        ".txt": "text/plain",
    },
}
EXECUTABLE_SUFFIXES = {
    ".bat",
    ".cmd",
    ".com",
    ".dll",
    ".exe",
    ".js",
    ".mjs",
    ".ps1",
    ".py",
    ".sh",
    ".so",
}
BINARY_SUFFIXES = {
    ".bin",
    ".class",
    ".gif",
    ".jar",
    ".jpeg",
    ".jpg",
    ".pdf",
    ".png",
    ".wasm",
    ".zip",
}


class SkillArchiveError(ValueError):
    pass


@dataclass(frozen=True)
class SkillResource:
    path: str
    media_type: str
    content: bytes

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "media_type": self.media_type,
            "size": len(self.content),
        }


@dataclass(frozen=True)
class ParsedSkillPackage:
    slug: str
    name: str
    description: str
    instructions: str
    change_note: str
    governance: dict[str, object]
    resources: tuple[SkillResource, ...]
    quarantine_reasons: tuple[str, ...]
    publish_blockers: tuple[str, ...]


def parse_skill_archive(payload: bytes) -> ParsedSkillPackage:
    if not payload:
        raise SkillArchiveError("Skill 归档不能为空")
    if len(payload) > MAX_ARCHIVE_SIZE:
        raise SkillArchiveError("Skill 归档压缩后超过大小限制")

    try:
        archive = zipfile.ZipFile(io.BytesIO(payload))
    except zipfile.BadZipFile as error:
        raise SkillArchiveError("Skill 归档不是有效的 ZIP 文件") from error

    with archive:
        all_entries = archive.infolist()
        if len(all_entries) > MAX_FILES:
            raise SkillArchiveError("Skill 归档文件数量超过限制")
        for entry in all_entries:
            _validated_path(entry.filename)
        entries = [entry for entry in all_entries if not entry.is_dir()]
        total_size = sum(entry.file_size for entry in entries)
        if total_size > MAX_TOTAL_SIZE:
            raise SkillArchiveError("Skill 归档解压后超过总大小限制")

        seen: set[str] = set()
        files: dict[str, bytes] = {}
        resource_types: dict[str, str] = {}
        quarantine_reasons: list[str] = []
        for entry in entries:
            path = _validated_path(entry.filename)
            folded = path.casefold()
            if folded in seen:
                raise SkillArchiveError(f"Skill 归档包含重复路径：{path}")
            seen.add(folded)
            if entry.flag_bits & 0x1:
                raise SkillArchiveError(f"Skill 归档不能包含加密文件：{path}")
            if entry.file_size > MAX_FILE_SIZE:
                raise SkillArchiveError(f"Skill 文件超过大小限制：{path}")

            media_type, executable = _classify_path(path, entry)
            try:
                content = archive.read(entry)
            except (RuntimeError, zipfile.BadZipFile) as error:
                raise SkillArchiveError("Skill 归档内容损坏") from error
            if len(content) != entry.file_size:
                raise SkillArchiveError(f"Skill 文件大小不一致：{path}")
            if executable:
                quarantine_reasons.append(f"包包含二进制或可执行内容：{path}")
            elif media_type.startswith("text/") or media_type.endswith("json"):
                _decode_text(content, path)
            files[path] = content
            resource_types[path] = media_type

    try:
        manifest_bytes = files.pop("SKILL.md")
    except KeyError:
        raise SkillArchiveError("Skill 归档根目录缺少 SKILL.md") from None
    manifest = _decode_text(manifest_bytes, "SKILL.md")
    slug, description, instructions = _parse_manifest(manifest)
    metadata = _parse_metadata(files.pop("medipet.json", b"{}"))
    display_name = _metadata_text(metadata, "display_name", slug)
    change_note = _metadata_text(metadata, "change_note", "导入 Skill 归档")
    governance = {
        "format_version": metadata.get("format_version", 1),
        "display_name": display_name,
        "change_note": change_note,
        "risk_level": metadata.get("risk_level", "standard"),
        "required_approvals": metadata.get("required_approvals", 0),
    }
    _validate_governance(governance)

    resources = tuple(
        SkillResource(path=path, media_type=resource_types[path], content=content)
        for path, content in sorted(files.items())
    )
    for resource in resources:
        if resource.media_type in {"application/json", "application/schema+json"}:
            try:
                document = json.loads(resource.content)
            except json.JSONDecodeError as error:
                kind = "Schema" if resource.media_type == "application/schema+json" else "JSON"
                raise SkillArchiveError(
                    f"Skill {kind} 不是有效的 JSON：{resource.path}"
                ) from error
            if resource.media_type == "application/schema+json" and not isinstance(
                document, dict
            ):
                raise SkillArchiveError(f"Skill Schema 必须是 JSON 对象：{resource.path}")
    text_files = {"SKILL.md": instructions}
    text_files.update(
        {
            resource.path: resource.content.decode("utf-8")
            for resource in resources
            if resource.media_type.startswith("text/") or resource.media_type.endswith("json")
        }
    )
    return ParsedSkillPackage(
        slug=slug,
        name=display_name,
        description=description,
        instructions=instructions,
        change_note=change_note,
        governance=governance,
        resources=resources,
        quarantine_reasons=tuple(quarantine_reasons),
        publish_blockers=static_publish_blockers(text_files),
    )


def export_skill_archive(
    *,
    slug: str,
    description: str,
    instructions: str,
    governance: dict[str, object],
    resources: tuple[SkillResource, ...],
) -> bytes:
    manifest = _manifest_content(slug, description, instructions).encode()
    metadata = json.dumps(
        governance, ensure_ascii=False, indent=2, sort_keys=True
    ).encode()
    validate_exportable_skill(manifest, metadata, resources)
    package = io.BytesIO()
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        _write_file(archive, "SKILL.md", manifest)
        _write_file(archive, "medipet.json", metadata)
        for resource in sorted(resources, key=lambda item: item.path):
            _write_file(
                archive,
                resource.path,
                resource.content,
                executable=resource.media_type == "application/octet-stream",
            )
    return package.getvalue()


def validate_exportable_skill(
    manifest: bytes,
    metadata: bytes,
    resources: tuple[SkillResource, ...],
) -> None:
    files = (("SKILL.md", manifest), ("medipet.json", metadata)) + tuple(
        (resource.path, resource.content) for resource in resources
    )
    if len(files) > MAX_FILES:
        raise SkillArchiveError("Skill 归档文件数量超过限制")
    for path, content in files:
        _validated_path(path)
        if len(content) > MAX_FILE_SIZE:
            raise SkillArchiveError(f"Skill 文件超过大小限制：{path}")
    if sum(len(content) for _, content in files) > MAX_TOTAL_SIZE:
        raise SkillArchiveError("Skill 归档解压后超过总大小限制")


def validate_skill_content(
    *,
    slug: str,
    description: str,
    instructions: str,
    governance: dict[str, object],
    resources: tuple[SkillResource, ...] = (),
) -> None:
    validate_exportable_skill(
        _manifest_content(slug, description, instructions).encode(),
        json.dumps(governance, ensure_ascii=False, indent=2, sort_keys=True).encode(),
        resources,
    )


def static_publish_blockers(files: dict[str, str]) -> tuple[str, ...]:
    checks = (
        (
            re.compile(
                r"(?:ignore|disregard|override|bypass).{0,40}(?:previous|system|platform|safety).{0,20}(?:instruction|policy|rule)?",
                re.IGNORECASE,
            ),
            "包含试图覆盖上级指令的内容",
        ),
        (
            re.compile(
                r"(?:reveal|expose|print|return).{0,40}(?:secret|token|password|system prompt)",
                re.IGNORECASE,
            ),
            "包含请求泄露敏感信息的内容",
        ),
        (
            re.compile(r"(?:忽略|绕过|覆盖).{0,20}(?:系统|平台|安全).{0,10}(?:指令|策略|规则)"),
            "包含试图覆盖上级指令的内容",
        ),
        (
            re.compile(r"(?:泄露|显示|输出|返回).{0,20}(?:密钥|令牌|密码|系统提示词)"),
            "包含请求泄露敏感信息的内容",
        ),
    )
    blockers: list[str] = []
    for path, content in files.items():
        for pattern, message in checks:
            blocker = f"{path} {message}"
            if pattern.search(content) and blocker not in blockers:
                blockers.append(blocker)
    return tuple(blockers)


def _validated_path(raw_path: str) -> str:
    if "\\" in raw_path:
        raise SkillArchiveError(f"Skill 资源路径无效：{raw_path}")
    path = PurePosixPath(raw_path)
    unsafe_part = any(
        part in {"", ".", ".."}
        or ":" in part
        or part.endswith((" ", "."))
        or any(ord(character) < 32 for character in part)
        for part in path.parts
    )
    if path.is_absolute() or not path.parts or unsafe_part:
        raise SkillArchiveError(f"Skill 资源路径无效：{raw_path}")
    return path.as_posix()


def _classify_path(path: str, entry: zipfile.ZipInfo) -> tuple[str, bool]:
    pure_path = PurePosixPath(path)
    mode = entry.external_attr >> 16
    executable = (
        pure_path.parts[0].casefold() == "scripts"
        or pure_path.suffix.casefold() in EXECUTABLE_SUFFIXES
        or pure_path.suffix.casefold() in BINARY_SUFFIXES
        or stat.S_IFMT(mode) == stat.S_IFLNK
        or bool(mode & 0o111)
    )
    if executable:
        return "application/octet-stream", True
    if path in {"SKILL.md", "medipet.json"}:
        return ("text/markdown" if path.endswith(".md") else "application/json"), False
    if len(pure_path.parts) < 2:
        raise SkillArchiveError(f"Skill 包含不支持的文件：{path}")
    directory = pure_path.parts[0]
    media_type = SAFE_RESOURCE_TYPES.get(directory, {}).get(pure_path.suffix.casefold())
    if media_type is None:
        raise SkillArchiveError(f"Skill 包含不支持的文件：{path}")
    return media_type, False


def _decode_text(content: bytes, path: str) -> str:
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise SkillArchiveError(f"Skill 文本文件必须使用 UTF-8：{path}") from error


def _parse_manifest(manifest: str) -> tuple[str, str, str]:
    match = re.fullmatch(r"---\r?\n(.*?)\r?\n---\r?\n(.*)", manifest, re.DOTALL)
    if match is None:
        raise SkillArchiveError("SKILL.md 必须包含 YAML frontmatter")
    metadata: dict[str, str] = {}
    for line in match.group(1).splitlines():
        key, separator, value = line.partition(":")
        if separator and key.strip() in {"name", "description"}:
            normalized_key = key.strip()
            if normalized_key in metadata:
                raise SkillArchiveError(
                    f"SKILL.md frontmatter 包含重复字段：{normalized_key}"
                )
            metadata[normalized_key] = _plain_yaml_string(value.strip())
    slug = metadata.get("name", "").strip()
    description = metadata.get("description", "").strip()
    instructions = match.group(2).strip()
    if not slug:
        raise SkillArchiveError("SKILL.md frontmatter 缺少 name")
    if not description:
        raise SkillArchiveError("SKILL.md frontmatter 缺少 description")
    if not instructions:
        raise SkillArchiveError("SKILL.md 指令不能为空")
    return slug, description, instructions


def _plain_yaml_string(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise SkillArchiveError("SKILL.md frontmatter 字符串无效") from error
        if isinstance(parsed, str):
            return parsed
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def _parse_metadata(content: bytes) -> dict[str, Any]:
    try:
        metadata = json.loads(_decode_text(content, "medipet.json"))
    except json.JSONDecodeError as error:
        raise SkillArchiveError("medipet.json 不是有效的 JSON") from error
    if not isinstance(metadata, dict):
        raise SkillArchiveError("medipet.json 必须是 JSON 对象")
    allowed = {
        "format_version",
        "display_name",
        "change_note",
        "risk_level",
        "required_approvals",
    }
    unknown = sorted(set(metadata) - allowed)
    if unknown:
        raise SkillArchiveError(f"medipet.json 包含未知字段：{', '.join(unknown)}")
    return metadata


def _metadata_text(metadata: dict[str, Any], field: str, default: str) -> str:
    value = metadata.get(field, default)
    if not isinstance(value, str) or not value.strip():
        raise SkillArchiveError(f"medipet.json 的 {field} 必须是非空字符串")
    return value.strip()


def _validate_governance(governance: dict[str, object]) -> None:
    if governance["format_version"] != 1:
        raise SkillArchiveError("medipet.json 的 format_version 不受支持")
    if governance["risk_level"] not in {"low", "standard", "high"}:
        raise SkillArchiveError("medipet.json 的 risk_level 无效")
    approvals = governance["required_approvals"]
    if not isinstance(approvals, int) or isinstance(approvals, bool) or not 0 <= approvals <= 2:
        raise SkillArchiveError("medipet.json 的 required_approvals 无效")


def _write_file(
    archive: zipfile.ZipFile, path: str, content: bytes, *, executable: bool = False
) -> None:
    entry = zipfile.ZipInfo(path, date_time=(1980, 1, 1, 0, 0, 0))
    entry.compress_type = zipfile.ZIP_DEFLATED
    entry.external_attr = (0o700 if executable else 0o600) << 16
    archive.writestr(entry, content)


def _manifest_content(slug: str, description: str, instructions: str) -> str:
    quoted_description = json.dumps(description, ensure_ascii=False)
    return (
        f"---\nname: {slug}\ndescription: {quoted_description}\n---\n\n"
        f"{instructions.rstrip()}\n"
    )
