from pathlib import Path
import re

for file in Path("ultron").rglob("*.py"):

    try:
        text = file.read_text(encoding="utf-8")

    except Exception:
        continue

    original = text

    # Add ClassVar import if needed
    if re.search(r"^\s*\w+\s*=\s*(\[\]|\{\})", text, re.MULTILINE):

        if "from typing import ClassVar" not in text:
            text = "from typing import ClassVar\n\n" + text

    # Convert simple class-level mutable defaults
    text = re.sub(
        r"^(\s+)(\w+)\s*=\s*(\[\]|\{\})",
        r"\1\2: ClassVar = \3",
        text,
        flags=re.MULTILINE
    )

    if text != original:
        file.write_text(
            text,
            encoding="utf-8"
        )
        print("Fixed:", file)
