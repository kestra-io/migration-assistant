"""
Byte-level readers for the three vRO container formats.

Format facts verified in the earlier evaluation and re-verified by the unit
tests in tests/test_parse.py:

  .package                A ZIP. elements/<uuid>/{info,data,categories,
                          content-signature,tags?,input_form_?}, mirrored under
                          signatures/, plus certificates/ and dunes-meta-inf.
  elements/<uuid>/info    UTF-8 Java properties XML. Carries only `type`, `id`
                          and `signature-owner`. NOT the name, NOT the category.
  elements/<uuid>/data    UTF-16BE with an FE FF BOM, and an XML declaration
                          that wrongly says encoding="UTF-8". Decode by BOM.
  elements/<uuid>/categories  The folder path, as <category name="..."> elements.
  ResourceElement data    A nested ZIP; content under VSO-RESOURCE-INF/.
  single .workflow export A ZIP with workflow-info (properties), workflow-content
                          (UTF-16BE XML) and an optional input_form_.
  raw XML                 UTF-8 on disk in git, with an optional sibling
                          <name>.element_info.xml holding name and categoryPath.
"""
from __future__ import annotations

import os
import re
import zipfile
from dataclasses import dataclass, field

from .. import outdir, safety

# --------------------------------------------------------------------------
# decoding
# --------------------------------------------------------------------------

def decode_bytes(raw: bytes) -> str:
    """Decode by BOM first. The XML declaration inside a .package lies."""
    if raw[:2] == b"\xfe\xff":
        return raw[2:].decode("utf-16-be")
    if raw[:2] == b"\xff\xfe":
        return raw[2:].decode("utf-16-le")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8")
    # No BOM. UTF-16 without a BOM shows up as interleaved NULs.
    if len(raw) > 4 and raw[1:2] == b"\x00" and raw[3:4] == b"\x00":
        return raw.decode("utf-16-le")
    if len(raw) > 4 and raw[0:1] == b"\x00" and raw[2:3] == b"\x00":
        return raw.decode("utf-16-be")
    for enc in ("utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    return raw.decode("utf-8", "replace")


def strip_xml_declaration(text: str) -> str:
    """Remove the declaration so ElementTree cannot act on its false encoding."""
    return re.sub(r"^\s*<\?xml[^>]*\?>", "", text, count=1).lstrip("﻿").lstrip()


def read_xml_text(raw: bytes) -> str:
    return strip_xml_declaration(decode_bytes(raw))


# --------------------------------------------------------------------------
# element records
# --------------------------------------------------------------------------

@dataclass
class Element:
    """One vRO element, however it was packaged."""
    kind: str                     # Workflow | ScriptModule | ConfigurationElement | ResourceElement | PolicyTemplate | ...
    uuid: str = ""
    name: str = ""
    category_path: str = ""       # dotted, as vRO folders read top down
    xml: str = ""                 # decoded XML text, declaration stripped
    raw: bytes = b""              # original bytes, for resource elements
    source_path: str = ""         # where it came from, for the report
    container_format: str = ""    # package | workflow-export | raw-xml
    input_form: str | None = None # the input_form_ payload if the package had one
    file_stem: str = ""           # filename without .xml, for raw trees; the
                                  # earlier evaluation keyed its results on this
    mime: str = ""                # a resource element's mime type, where the export records one
    warnings: list[str] = field(default_factory=list)


INFO_TYPE_RE = re.compile(r'<entry key="type">([^<]+)</entry>')
INFO_ID_RE = re.compile(r'<entry key="id">([^<]+)</entry>')
# The categories file quotes the name attribute with SINGLE quotes, unlike the
# rest of the package's XML. Accept either.
CATEGORY_NAME_RE = re.compile(r"""<category[^>]*\bname=(?:"([^"]*)"|'([^']*)')""")
ENTRY_RE = re.compile(r'<entry key="([^"]+)">([^<]*)</entry>')
DISPLAY_NAME_RE = re.compile(
    r"<display-name>\s*(?:<!\[CDATA\[(.*?)\]\]>|([^<]*))\s*</display-name>", re.S
)
MODULE_NAME_RE = re.compile(r'<dunes-script-module[^>]*\bname="([^"]*)"')


def _display_name(xml: str) -> str:
    m = DISPLAY_NAME_RE.search(xml)
    if not m:
        return ""
    return (m.group(1) or m.group(2) or "").strip()


def _categories_to_path(raw: bytes) -> str:
    """The categories file lists folders outermost first."""
    text = read_xml_text(raw)
    names = [(a or b).strip() for a, b in CATEGORY_NAME_RE.findall(text)]
    return ".".join(n for n in names if n)


# --------------------------------------------------------------------------
# .package
# --------------------------------------------------------------------------

def read_package(path: str) -> tuple[list[Element], dict]:
    """Read a signed vRO .package. Returns (elements, package summary)."""
    elements: list[Element] = []
    summary: dict = {
        "source_path": path,
        "format": "package",
        "used_plugins": [],
        "certificates": 0,
        "element_counts": {},
        "signed": False,
    }
    with safety.open_zip(path) as z:
        names = z.namelist()
        summary["signed"] = any(n.startswith("signatures/") for n in names)
        summary["certificates"] = len([n for n in names if n.startswith("certificates/")])

        # dunes-meta-inf lists the plugins the package was built against.
        for n in names:
            if n.startswith("dunes-meta-inf") and not n.endswith("/"):
                try:
                    meta = read_xml_text(safety.read_member(z, n))
                    summary["used_plugins"] = sorted(set(
                        re.findall(r'<entry key="used-plugin-([^"]+)">', meta)
                        or re.findall(r'name="([^"]+)"[^>]*type="plugin"', meta)
                        or re.findall(r'<entry key="([^"]*plugin[^"]*)">', meta)
                    ))
                except Exception as e:
                    summary.setdefault("warnings", []).append(f"dunes-meta-inf: {e}")

        # Only elements/, never the signatures/ mirror.
        uuids = sorted({
            n.split("/")[1] for n in names
            if n.startswith("elements/") and n.count("/") >= 2
        })

        for uuid in uuids:
            base = f"elements/{uuid}"
            try:
                info = decode_bytes(safety.read_member(z, f"{base}/info"))
            except KeyError:
                continue
            tm = INFO_TYPE_RE.search(info)
            if not tm:
                continue
            kind = tm.group(1)
            im = INFO_ID_RE.search(info)

            cat = ""
            try:
                cat = _categories_to_path(safety.read_member(z, f"{base}/categories"))
            except KeyError:
                pass

            form = None
            for n in names:
                if n.startswith(f"{base}/input_form_"):
                    try:
                        form = decode_bytes(safety.read_member(z, n))
                    except (KeyError, UnicodeError, safety.UnsafeInput):
                        pass
                    break

            try:
                data = safety.read_member(z, f"{base}/data")
            except KeyError:
                continue
            if len(data) > safety.MAX_ELEMENT_BYTES:
                summary.setdefault("warnings", []).append(
                    f"element {uuid} refused: {len(data)} bytes, over {safety.MAX_ELEMENT_BYTES}")
                continue

            el = Element(
                kind=kind,
                uuid=im.group(1) if im else uuid,
                category_path=cat,
                raw=data,
                source_path=f"{path}!{base}",
                container_format="package",
                input_form=form,
            )

            if kind == "ResourceElement":
                el.name, el.raw, warn = _resource_payload(data)
                el.mime = _resource_mime(data)
                el.warnings += warn
            else:
                el.xml = read_xml_text(data)
                if kind in ("ScriptModule", "ScriptingAction"):
                    m = MODULE_NAME_RE.search(el.xml)
                    el.name = (m.group(1).strip() if m else "") or el.uuid
                else:
                    el.name = _display_name(el.xml) or el.uuid
                    el.file_stem = "".join(
                        c for c in _display_name(el.xml) if c.isalnum() or c in "._ -"
                    ).strip() or el.uuid

            elements.append(el)
            summary["element_counts"][kind] = summary["element_counts"].get(kind, 0) + 1

    return elements, summary


def _resource_payload(data: bytes) -> tuple[str, bytes, list[str]]:
    """A ResourceElement's data is a nested ZIP under VSO-RESOURCE-INF/."""
    warnings: list[str] = []
    try:
        with safety.open_zip(data, "resource element") as rz:
            try:
                name = decode_bytes(safety.read_member(rz, "VSO-RESOURCE-INF/attribute_name")).strip()
            except KeyError:
                name = ""
                warnings.append("resource element has no VSO-RESOURCE-INF/attribute_name")
            try:
                payload = safety.read_member(rz, "VSO-RESOURCE-INF/data")
            except KeyError:
                payload = b""
                warnings.append("resource element has no VSO-RESOURCE-INF/data")
            return name, payload, warnings
    except zipfile.BadZipFile:
        return "", data, ["resource element data is not a ZIP"]
    except safety.UnsafeInput as e:
        return "", b"", [f"resource element refused: {e}"]


def _resource_mime(data: bytes) -> str:
    """VSO-RESOURCE-INF/attribute_mimetype, or "" when the nested ZIP has none."""
    try:
        with safety.open_zip(data, "resource element") as rz:
            return decode_bytes(safety.read_member(rz, "VSO-RESOURCE-INF/attribute_mimetype")).strip()
    except (KeyError, zipfile.BadZipFile, safety.UnsafeInput):
        return ""


# --------------------------------------------------------------------------
# single-workflow export
# --------------------------------------------------------------------------

def read_workflow_export(path: str) -> tuple[list[Element], dict]:
    """A single .workflow export: workflow-info + workflow-content."""
    with safety.open_zip(path) as z:
        names = z.namelist()
        if "workflow-content" not in names:
            raise ValueError("not a single-workflow export: no workflow-content")
        xml = read_xml_text(safety.read_member(z, "workflow-content"))
        meta = {}
        if "workflow-info" in names:
            meta = dict(ENTRY_RE.findall(decode_bytes(safety.read_member(z, "workflow-info"))))
        form = None
        for n in names:
            if n.startswith("input_form_"):
                form = decode_bytes(safety.read_member(z, n))
                break
        el = Element(
            kind="Workflow",
            uuid=meta.get("id", ""),
            name=meta.get("name") or _display_name(xml),
            category_path=(meta.get("categoryPath") or "").replace("/", ".").replace("\\", "."),
            xml=xml,
            source_path=path,
            container_format="workflow-export",
            input_form=form,
        )
    return [el], {"source_path": path, "format": "workflow-export", "element_counts": {"Workflow": 1}}


# --------------------------------------------------------------------------
# raw XML trees
# --------------------------------------------------------------------------

KIND_DIRS = {
    "Workflow": "Workflow",
    "workflows": "Workflow",
    "ScriptModule": "ScriptModule",
    "actions": "ScriptModule",
    "ConfigurationElement": "ConfigurationElement",
    "ResourceElement": "ResourceElement",
    "PolicyTemplate": "PolicyTemplate",
}


def read_raw_tree(root: str) -> tuple[list[Element], dict]:
    """
    Read a BTVA-style source tree of raw XML.

    Layout is <Kind>/<Cat>/<Sub>/<Name>.xml with an optional sibling
    <Name>.element_info.xml. Where element_info is absent the category is taken
    from the directories below the kind directory, which is what vRO's own
    exporter does.
    """
    elements: list[Element] = []
    summary: dict = {"source_path": root, "format": "raw-xml", "element_counts": {}}

    for dirpath, dirs, files in os.walk(root):
        outdir.prune(dirpath, dirs)
        parts = dirpath.replace("\\", "/").split("/")
        kind = None
        idx = -1
        for i in range(len(parts) - 1, -1, -1):
            if parts[i] in KIND_DIRS:
                kind, idx = KIND_DIRS[parts[i]], i
                break
        if kind is None:
            continue
        cat_from_dirs = ".".join(parts[idx + 1:])

        for fn in sorted(files):
            if fn.endswith(".element_info.xml"):
                continue
            if kind == "ResourceElement" and not fn.endswith(".xml"):
                _raw_resource(dirpath, fn, cat_from_dirs, elements, summary)
                continue
            if not fn.endswith(".xml"):
                continue
            full = os.path.join(dirpath, fn)
            if os.path.islink(full):
                summary.setdefault("warnings", []).append(f"{full}: symlink, not followed")
                continue
            try:
                raw = safety.read_file(full)
            except safety.UnsafeInput as e:
                summary.setdefault("warnings", []).append(f"refused: {e}")
                continue
            xml = read_xml_text(raw)

            meta = {}
            info_path = full[:-4] + ".element_info.xml"
            if os.path.isfile(info_path) and not os.path.islink(info_path):
                meta = dict(ENTRY_RE.findall(decode_bytes(safety.read_file(info_path))))

            # element_info's `name` is authoritative where it exists; it does not
            # always match the filename or the XML display-name.
            name = meta.get("name") or _display_name(xml) or fn[:-4]
            cat = meta.get("categoryPath") or cat_from_dirs
            cat = cat.replace("/", ".").replace("\\", ".")

            form = None
            for cand in (full[:-4] + ".form.json",):
                if os.path.isfile(cand) and not os.path.islink(cand):
                    form = safety.read_file(cand).decode("utf-8", "replace")

            elements.append(Element(
                kind=meta.get("type") or kind,
                uuid=meta.get("id", ""),
                name=name.strip(),
                category_path=cat,
                xml=xml,
                raw=raw,
                source_path=full,
                container_format="raw-xml",
                input_form=form,
                file_stem=fn[:-4],
            ))
            summary["element_counts"][kind] = summary["element_counts"].get(kind, 0) + 1

    return elements, summary


def _raw_resource(dirpath: str, fn: str, cat_from_dirs: str, elements: list, summary: dict) -> None:
    """A resource element kept as its own file in a raw tree (any extension), named and placed by its
    `<file>.element_info.xml` where there is one."""
    import mimetypes
    full = os.path.join(dirpath, fn)
    if os.path.islink(full):
        summary.setdefault("warnings", []).append(f"{full}: symlink, not followed")
        return
    try:
        raw = safety.read_file(full)
    except safety.UnsafeInput as e:
        summary.setdefault("warnings", []).append(f"refused: {e}")
        return
    meta = {}
    for info in (full + ".element_info.xml", os.path.splitext(full)[0] + ".element_info.xml"):
        if os.path.isfile(info) and not os.path.islink(info):
            meta = dict(ENTRY_RE.findall(decode_bytes(safety.read_file(info))))
            break
    cat = (meta.get("categoryPath") or cat_from_dirs).replace("/", ".").replace("\\", ".")
    elements.append(Element(
        kind="ResourceElement", uuid=meta.get("id", ""), name=(meta.get("name") or fn).strip(),
        category_path=cat, raw=raw, source_path=full, container_format="raw-xml", file_stem=fn,
        mime=meta.get("mimetype") or mimetypes.guess_type(fn)[0] or ""))
    summary["element_counts"]["ResourceElement"] = summary["element_counts"].get("ResourceElement", 0) + 1


def read_any(path: str) -> tuple[list[Element], dict]:
    """Dispatch on what the path actually is."""
    if os.path.isdir(path):
        return read_raw_tree(path)
    if zipfile.is_zipfile(path):
        with safety.open_zip(path) as z:
            names = z.namelist()
        if "workflow-content" in names:
            return read_workflow_export(path)
        if any(n.startswith("elements/") for n in names):
            return read_package(path)
        raise ValueError(f"ZIP is neither a package nor a workflow export: {path}")
    if path.endswith(".xml"):
        raw = safety.read_file(path)
        xml = read_xml_text(raw)
        return [Element(kind="Workflow", name=_display_name(xml), xml=xml,
                        source_path=path, container_format="raw-xml")], \
               {"source_path": path, "format": "raw-xml", "element_counts": {"Workflow": 1}}
    raise ValueError(f"unrecognised input: {path}")
