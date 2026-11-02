from pathlib import PurePath


class SafetyValidator:
    """
    Validates improvement safety constraints.
    """

    PROTECTED_COMPONENTS = [
        "container",
        "memory",
        "reasoning",
        "execution",
        "tests",
    ]

    def validate(
        self,
        patch,
    ) -> dict:
        warnings: list[str] = []
        errors: list[str] = []

        try:
            if isinstance(patch, dict):
                values = patch
            elif patch is not None:
                values = {
                    "target_file": getattr(patch, "target_file"),
                    "description": getattr(patch, "description"),
                    "changes": getattr(patch, "changes"),
                    "operation": getattr(patch, "operation"),
                    "files": getattr(patch, "files", []),
                }
            else:
                values = {}

            target = values.get("target_file")
            description = values.get("description")
            changes = values.get("changes")
            operation = values.get("operation", "modify")
            files = values.get("files", [])

            if (
                operation != "create"
                and (
                    not isinstance(target, str)
                    or not target.strip()
                )
            ):
                errors.append("Missing target file")

            if not isinstance(description, str) or not description.strip():
                errors.append("Missing description")

            if not isinstance(changes, str):
                errors.append("Changes must be a string")

            if operation not in {"create", "modify", "extend", "replace"}:
                errors.append("Unsupported patch operation")

            if operation == "create" and (
                not isinstance(files, list) or not files
            ):
                errors.append("Creation patch must contain files")

            target_text = target.lower() if isinstance(target, str) else ""
            changes_text = changes.lower() if isinstance(changes, str) else ""

            if isinstance(target, str):
                target_path = PurePath(target)
                if target_path.is_absolute() or ".." in target_path.parts:
                    errors.append("Target file path is not allowed")

            if operation == "create" and isinstance(files, list):
                for item in files:
                    if not isinstance(item, dict):
                        errors.append("Creation files must be dictionaries")
                        continue
                    file_path = item.get("path")
                    if not isinstance(file_path, str) or not file_path.strip():
                        errors.append("Creation file path is required")
                    else:
                        creation_path = PurePath(file_path)
                        if (
                            creation_path.is_absolute()
                            or ".." in creation_path.parts
                        ):
                            errors.append(
                                "Creation file path is not allowed"
                            )
                    if not isinstance(item.get("content"), str):
                        errors.append("Creation file content must be a string")

            for component in self.PROTECTED_COMPONENTS:
                if component in target_text:
                    warnings.append(f"{component} requires approval")

            if "delete" in changes_text:
                warnings.append("Deletion is blocked")

        except Exception as exc:
            errors.append(
                "Safety validation could not establish validity: "
                f"{type(exc).__name__}: {exc}"
            )

        return {
            "safe": not errors and not warnings,
            "requires_approval": bool(warnings) or bool(errors),
            "warnings": warnings,
            "errors": errors,
        }
