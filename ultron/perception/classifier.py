import re


class IntentClassifier:
    """
    Classifies natural-language input into ULTRON's canonical intents.

    Self-analysis and self-improvement are treated as high-priority
    system operations and are resolved before generic search,
    question, memory, or conversation handling.

    Entity extraction is responsible for structured parameters.
    Planning and execution happen downstream.
    """

    _SELF_IMPROVEMENT_PATTERNS = (
        r"\bimprove\s+(?:yourself|itself|ultron)\b",
        r"\bupgrade\s+(?:yourself|itself|ultron)\b",
        r"\bmake\s+(?:yourself|itself|ultron)\s+better\b",
        r"\bfix\s+(?:yourself|itself|ultron)\b",
        r"\bself[-\s]?improv(?:e|ement)\b",
        r"\bimprove\s+your\s+(?:code|capabilities|implementation|system)\b",
        r"\bupgrade\s+your\s+(?:code|capabilities|implementation|system)\b",
        r"\bfind\s+(?:and\s+)?fix\s+(?:your|ultron'?s)\s+(?:missing|weak|broken)\s+capabilit",
        r"\banaly[sz]e\s+(?:yourself|itself|ultron)\b.*\bimprov",
        r"\bimprov.*\banaly[sz]e\s+(?:yourself|itself|ultron)\b",
    )

    _SELF_ANALYSIS_PATTERNS = (
        r"\banaly[sz]e\s+(?:yourself|itself|ultron)\b",
        r"\banaly[sz]e\s+your\s+(?:code|implementation|architecture|system)\b",
        r"\bself[-\s]?analy[sz](?:e|is)\b",
        r"\binspect\s+(?:yourself|itself|ultron)\b",
        r"\baudit\s+(?:yourself|itself|ultron)\b",
        r"\breview\s+(?:yourself|itself|ultron)\b",
        r"\bfind\s+(?:your|ultron'?s)\s+(?:missing|weak|broken)\s+capabilit",
    )

    _PROJECT_PATTERNS = (
        r"\bcreate\b.*\bproject\b",
        r"\bnew\b.*\bproject\b",
        r"\bmake\b.*\bproject\b",
        r"\bbuild\b.*\bproject\b",
        r"\bgenerate\b.*\bproject\b",
        r"\binitialize\b.*\bproject\b",
        r"\binit\b.*\bproject\b",
    )

    _CREATE_FILE_PATTERNS = (
        r"\bcreate\b.*\bfile\b",
        r"\bnew\b.*\bfile\b",
        r"\bmake\b.*\bfile\b",
        r"\bgenerate\b.*\bfile\b",
    )

    _WRITE_FILE_PATTERNS = (
        r"\bwrite\b.*\b(?:into|to|in)\b.*\bfile\b",
        r"\bwrite\b.*\b(?:into|to|in)\b.*\.[a-z0-9]+\b",
        r"\bput\b.*\b(?:into|to|in)\b.*\.[a-z0-9]+\b",
        r"\bsave\b.*\b(?:into|to|in)\b.*\.[a-z0-9]+\b",
        r"\bupdate\b.*\bfile\b",
        r"\bedit\b.*\bfile\b",
    )

    _READ_FILE_PATTERNS = (
        r"\bread\b.*\bfile\b",
        r"\bread\b.*\.[a-z0-9]+\b",
        r"\bshow\b.*\bfile\b",
        r"\bshow\b.*\.[a-z0-9]+\b",
        r"\bopen\b.*\.[a-z0-9]+\b",
    )

    _LIST_DIRECTORY_PATTERNS = (
        r"\blist\b.*\bfiles?\b",
        r"\blist\b.*\bdirector(?:y|ies)\b",
        r"\bshow\b.*\bfiles?\b",
        r"\bshow\b.*\bdirector(?:y|ies)\b",
        r"\bwhat\b.*\bfiles?\b.*\bhere\b",
    )

    _OPEN_APPLICATION_PATTERNS = (
        r"\bopen\b",
        r"\blaunch\b",
        r"\bstart\b",
    )

    _SEARCH_PATTERNS = (
        r"\bsearch\b",
        r"\bfind\b",
        r"\blook\s+up\b",
    )

    _MEMORY_PATTERNS = (
        r"\bremember\b",
        r"\bsave\b",
        r"\bstore\b",
    )

    _SELF_ANALYSIS_PATTERNS = (
        r"\banalyze\s+yourself\b",
        r"\banalyse\s+yourself\b",
        r"\bself[- ]analysis\b",
        r"\bself[- ]analyz(e|ing|ed)\b",
        r"\binspect\s+yourself\b",
        r"\bdiagnose\s+yourself\b",
        r"\bcheck\s+yourself\b",
    )

    _IMPROVE_SELF_PATTERNS = (
        r"\bimprove\s+yourself\b",
        r"\bimprove\s+yourself\s+now\b",
        r"\bself[- ]improv(e|ement)\b",
        r"\bself[- ]improve\b",
        r"\bupgrade\s+yourself\b",
        r"\bupgrade\s+your\s+capabilit(y|ies)\b",
        r"\bmake\s+yourself\s+better\b",
        r"\bevolve\s+yourself\b",
    )

    _QUESTION_PATTERNS = (
        r"^\s*(what|who|when|where|why|how|which|can|could|would|is|are|do|does|did)\b",
    )

    @staticmethod
    def _matches(
        text: str,
        patterns: tuple[str, ...],
    ) -> bool:
        return any(
            re.search(pattern, text)
            for pattern in patterns
        )

    def classify(
        self,
        text: str,
    ) -> tuple[str, float]:

        normalized = " ".join(
            text.lower().strip().split()
        )

        if not normalized:
            return "conversation", 0.0

        # ---------------------------------------------------------
        # ULTRON self-management
        #
        # These MUST be evaluated before generic search/question
        # detection. "Find a missing capability and improve yourself"
        # contains the word "find", but its actual operation is
        # self-improvement.
        # ---------------------------------------------------------

        if self._matches(
            normalized,
            self._SELF_IMPROVEMENT_PATTERNS,
        ):
            return "improve_self", 0.99

        if self._matches(
            normalized,
            self._SELF_ANALYSIS_PATTERNS,
        ):
            return "self_analysis", 0.98

        # ---------------------------------------------------------
        # Self-analysis / self-improvement
        #
        # These must be resolved before generic conversation,
        # search, question, or other weak intent detection.
        # ---------------------------------------------------------

        if self._matches(
            normalized,
            self._IMPROVE_SELF_PATTERNS,
        ):
            return "improve_self", 0.99

        if self._matches(
            normalized,
            self._SELF_ANALYSIS_PATTERNS,
        ):
            return "self_analysis", 0.99

        # ---------------------------------------------------------
        # Project operations
        # ---------------------------------------------------------

        if self._matches(
            normalized,
            self._PROJECT_PATTERNS,
        ):
            return "build_project", 0.98

        # ---------------------------------------------------------
        # File operations
        #
        # These must occur before generic open/search handling.
        # ---------------------------------------------------------

        if self._matches(
            normalized,
            self._CREATE_FILE_PATTERNS,
        ):
            return "create_file", 0.97

        if self._matches(
            normalized,
            self._WRITE_FILE_PATTERNS,
        ):
            return "write_file", 0.97

        if self._matches(
            normalized,
            self._READ_FILE_PATTERNS,
        ):
            return "read_file", 0.95

        if self._matches(
            normalized,
            self._LIST_DIRECTORY_PATTERNS,
        ):
            return "list_directory", 0.95

        # ---------------------------------------------------------
        # Application control
        # ---------------------------------------------------------

        if self._matches(
            normalized,
            self._OPEN_APPLICATION_PATTERNS,
        ):
            return "open_application", 0.90

        # ---------------------------------------------------------
        # Memory
        # ---------------------------------------------------------

        if self._matches(
            normalized,
            self._MEMORY_PATTERNS,
        ):
            return "memory_store", 0.95

        # ---------------------------------------------------------
        # Search
        # ---------------------------------------------------------

        if self._matches(
            normalized,
            self._SEARCH_PATTERNS,
        ):
            return "search", 0.90

        # ---------------------------------------------------------
        # Questions
        # ---------------------------------------------------------

        if self._matches(
            normalized,
            self._QUESTION_PATTERNS,
        ):
            return "question", 0.80

        return "conversation", 0.50
