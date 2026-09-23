"""Check public documentation without depending on an enclosing workspace."""

import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

from openevent.cmd_sdk import CmdSDKError, loads_payload


ROOT = Path(__file__).resolve().parents[1]
PAIRS = [(ROOT / "README.md", ROOT / "README_cn.md")]
PAIRS += [(ROOT / "docs" / (name + ".md"), ROOT / "docs" / (name + "_cn.md"))
          for name in ("CMD_PROTOCOL", "CONFIGURATION", "SDK_USAGE")]


def headings(text):
    outside_code = re.sub(r"```.*?```", "", text, flags=re.S)
    return re.findall(r"^(#{1,6})\s+(.+)$", outside_code, flags=re.M)


def anchors(text):
    return {re.sub(r"[^\w\s-]", "", title.lower()).replace(" ", "-")
            for _, title in headings(text)}


def main():
    errors = []
    dependency = re.search(r'"(openevent-sdk>=[^"]+)"', (ROOT / "pyproject.toml").read_text()).group(1)
    documents = {path: path.read_text(encoding="utf-8") for pair in PAIRS for path in pair}
    links = 0
    protocol_examples = 0
    for english, chinese in PAIRS:
        if ([level for level, _ in headings(documents[english])]
                != [level for level, _ in headings(documents[chinese])]):
            errors.append(f"translation heading structure differs: {english.name}, {chinese.name}")
        blocks = lambda text: re.findall(r"```[^\n]*\n(.*?)```", text, flags=re.S)
        if blocks(documents[english]) != blocks(documents[chinese]):
            errors.append(f"translation examples differ: {english.name}, {chinese.name}")
    for path, text in documents.items():
        for example in re.finditer(r"```json\s*\n(.*?)```", text, flags=re.S):
            line = text.count("\n", 0, example.start()) + 1
            try:
                payload = example.group(1)
                data = json.loads(payload)
                if isinstance(data, dict) and "kind" in data:
                    loads_payload(payload.encode("utf-8"))
                    protocol_examples += 1
            except (json.JSONDecodeError, CmdSDKError, UnicodeError) as exc:
                errors.append(f"{path.relative_to(ROOT)}:{line}: invalid JSON/protocol example: {exc}")
        for declared in re.findall(r"openevent-sdk>=[0-9.]+", text):
            if declared != dependency:
                errors.append(f"{path.relative_to(ROOT)}: stale dependency {declared}, expected {dependency}")
        for raw in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
            target = urlsplit(raw)
            if target.scheme or target.netloc:
                continue
            links += 1
            destination = path.parent / unquote(target.path) if target.path else path
            if not destination.exists():
                errors.append(f"{path.relative_to(ROOT)}: missing local link {raw}")
            elif target.fragment and destination.suffix == ".md":
                if unquote(target.fragment) not in anchors(destination.read_text(encoding="utf-8")):
                    errors.append(f"{path.relative_to(ROOT)}: missing anchor {raw}")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(f"checked {len(documents)} documents, {len(PAIRS)} translation pairs, "
          f"{links} local links, {protocol_examples} protocol examples")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
