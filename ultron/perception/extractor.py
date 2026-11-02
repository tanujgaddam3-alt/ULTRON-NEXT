import re


class EntityExtractor:
    """
    Extracts structured entities from natural-language input.

    Extraction is context-aware:

    - project_name only comes from project requests
    - project_type only comes from project requests
    - description only comes from project requests
    - filename comes from explicit file references
    - application comes from application-control requests
    """

    APPLICATIONS = {
        "vscode": (
            "vscode",
            "vs code",
            "visual studio code",
        ),
        "terminal": (
            "terminal",
        ),
        "firefox": (
            "firefox",
        ),
        "chrome": (
            "chrome",
            "google chrome",
        ),
        "calculator": (
            "calculator",
        ),
    }

    PROJECT_TYPES = (
        "python",
        "fastapi",
        "django",
        "flask",
        "javascript",
        "typescript",
        "node",
        "nodejs",
        "react",
        "nextjs",
        "next.js",
        "vue",
        "angular",
        "java",
        "kotlin",
        "swift",
        "rust",
        "go",
        "golang",
        "cpp",
        "c++",
        "c",
    )

    PROJECT_ACTION_PATTERN = re.compile(
        r"\b("
        r"create|"
        r"build|"
        r"make|"
        r"generate|"
        r"initialize|"
        r"init"
        r")\b",
        re.IGNORECASE,
    )

    PROJECT_NOUN_PATTERN = re.compile(
        r"\bproject\b",
        re.IGNORECASE,
    )

    PROJECT_NAME_PATTERNS = (
        re.compile(
            r"\bproject\s+(?:named|called)\s+"
            r"['\"]?([A-Za-z0-9_.-]+)['\"]?",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:named|called)\s+"
            r"['\"]?([A-Za-z0-9_.-]+)['\"]?"
            r"\s*(?:project)?",
            re.IGNORECASE,
        ),
    )

    FILENAME_PATTERNS = (
        re.compile(
            r"\bfile\s+(?:named|called)\s+"
            r"['\"]?([A-Za-z0-9_.-]+\.[A-Za-z0-9_-]+)['\"]?",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b(?:into|to|in|from|edit|open|read|write)\s+"
            r"['\"]?([A-Za-z0-9_.-]+\.[A-Za-z0-9_-]+)['\"]?",
            re.IGNORECASE,
        ),
    )

    # Explicit filename fallback. Requires a conventional extension.
    GENERIC_FILENAME_PATTERN = re.compile(
        r"\b([A-Za-z0-9_.-]+\.[A-Za-z0-9_-]+)\b",
        re.IGNORECASE,
    )

    DESCRIPTION_PATTERNS = (
        re.compile(
            r"\bdescription\s*[:=]?\s*"
            r"['\"]([^'\"]+)['\"]",
            re.IGNORECASE,
        ),
        re.compile(
            r"\bwith\s+description\s+"
            r"['\"]([^'\"]+)['\"]",
            re.IGNORECASE,
        ),
    )

    def extract(
        self,
        text: str,
    ) -> dict[str, str]:

        if not isinstance(text, str):
            return {}

        text = text.strip()

        if not text:
            return {}

        lower = text.lower()

        entities: dict[str, str] = {}

        project_request = self._is_project_request(lower)

        # ---------------------------------------------------------
        # Project entities
        # ---------------------------------------------------------

        if project_request:
            project_name = self._extract_project_name(text)

            if project_name:
                entities["project_name"] = project_name

            project_type = self._extract_project_type(lower)

            if project_type:
                entities["project_type"] = project_type

            description = self._extract_description(text)

            if description:
                entities["description"] = description

        # ---------------------------------------------------------
        # File entity
        # ---------------------------------------------------------

        filename = self._extract_filename(text)

        if filename:
            entities["filename"] = filename

        # A filename is never a project name.
        if entities.get("project_name") == entities.get("filename"):
            entities.pop("project_name", None)

        # ---------------------------------------------------------
        # Application entity
        # ---------------------------------------------------------

        application = self._extract_application(lower)

        if application:
            entities["application"] = application

        return entities

    def _is_project_request(
        self,
        text: str,
    ) -> bool:

        return bool(
            self.PROJECT_ACTION_PATTERN.search(text)
            and self.PROJECT_NOUN_PATTERN.search(text)
        )

    def _extract_project_name(
        self,
        text: str,
    ) -> str | None:

        for pattern in self.PROJECT_NAME_PATTERNS:
            match = pattern.search(text)

            if match:
                name = match.group(1).strip(
                    " \t\r\n.,!?;:'\""
                )

                if name:
                    return name

        return None

    def _extract_project_type(
        self,
        text: str,
    ) -> str | None:

        for project_type in self.PROJECT_TYPES:
            pattern = re.compile(
                rf"\b{re.escape(project_type)}\b",
                re.IGNORECASE,
            )

            if pattern.search(text):
                normalized = project_type.lower()

                if normalized == "next.js":
                    return "nextjs"

                if normalized == "c++":
                    return "cpp"

                if normalized == "golang":
                    return "go"

                if normalized == "nodejs":
                    return "node"

                return normalized

        return None

    def _extract_description(
        self,
        text: str,
    ) -> str | None:

        for pattern in self.DESCRIPTION_PATTERNS:
            match = pattern.search(text)

            if match:
                description = match.group(1).strip()

                if description:
                    return description

        return None

    def _extract_filename(
        self,
        text: str,
    ) -> str | None:

        for pattern in self.FILENAME_PATTERNS:
            match = pattern.search(text)

            if not match:
                continue

            filename = match.group(1).strip(
                " \t\r\n.,!?;:'\""
            )

            if self._valid_filename(filename):
                return filename

        for match in self.GENERIC_FILENAME_PATTERN.finditer(text):
            filename = match.group(1).strip(
                " \t\r\n.,!?;:'\""
            )

            if self._valid_filename(filename):
                return filename

        return None

    @staticmethod
    def _valid_filename(
        filename: str,
    ) -> bool:

        if not filename or "." not in filename:
            return False

        if filename.lower().startswith(
            (
                "http.",
                "https.",
            )
        ):
            return False

        # Prevent sentence punctuation from becoming part of the name.
        if filename.endswith("."):
            return False

        return True

    def _extract_application(
        self,
        text: str,
    ) -> str | None:

        for normalized_name, aliases in self.APPLICATIONS.items():
            for alias in aliases:
                if re.search(
                    rf"\b{re.escape(alias)}\b",
                    text,
                    re.IGNORECASE,
                ):
                    return normalized_name

        return None
