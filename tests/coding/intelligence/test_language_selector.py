from ultron.agents.coding.intelligence import LanguageSelector


def test_android_prefers_kotlin():
    result = LanguageSelector().select(
        "Build an Android application"
    )

    assert result.selected is not None
    assert result.selected.language == "Kotlin"


def test_embedded_prefers_c():
    result = LanguageSelector().select(
        "Build embedded firmware for a microcontroller"
    )

    assert result.selected is not None
    assert result.selected.language == "C"


def test_automation_prefers_python():
    result = LanguageSelector().select(
        "Create a quick automation prototype"
    )

    assert result.selected is not None
    assert result.selected.language == "Python"


def test_browser_frontend_prefers_typescript():
    result = LanguageSelector().select(
        "Create a modern browser frontend"
    )

    assert result.selected is not None
    assert result.selected.language == "TypeScript"


def test_machine_learning_prefers_python():
    result = LanguageSelector().select(
        "Create a machine learning system for image analysis"
    )

    assert result.selected is not None
    assert result.selected.language == "Python"


def test_high_performance_concurrent_backend_prefers_systems_language():
    result = LanguageSelector().select(
        "Build a high performance concurrent backend with very low latency"
    )

    assert result.selected is not None
    assert result.selected.language in {"Rust", "Go"}


def test_memory_safe_systems_prefers_rust():
    result = LanguageSelector().select(
        "Build a memory-safe systems component"
    )

    assert result.selected is not None
    assert result.selected.language == "Rust"


def test_explicit_language_overrides_inference():
    result = LanguageSelector().select(
        "Build an Android application in Java"
    )

    assert result.selected is not None
    assert result.selected.language == "Java"


def test_android_does_not_select_javascript():
    result = LanguageSelector().select(
        "Build an Android application"
    )

    assert result.selected is not None
    assert result.selected.language not in {
        "JavaScript",
        "TypeScript",
    }


def test_web_does_not_select_non_browser_language():
    result = LanguageSelector().select(
        "Create a modern browser frontend"
    )

    assert result.selected is not None
    assert result.selected.language in {
        "TypeScript",
        "JavaScript",
    }


def test_android_native_language_beats_cross_platform_language():
    result = LanguageSelector().select(
        "Build an Android application"
    )

    assert result.selected is not None
    assert result.selected.language == "Kotlin"
    assert result.selected.language not in {
        "JavaScript",
        "TypeScript",
    }


def test_android_java_is_valid_native_fallback():
    selector = LanguageSelector()

    result = selector.select(
        "Build an Android application",
        preferred_languages=["Java"],
    )

    assert result.selected is not None
    assert result.selected.language in {
        "Kotlin",
        "Java",
    }


def test_automation_prefers_python_over_javascript():
    result = LanguageSelector().select(
        "Create a quick automation prototype"
    )

    assert result.selected is not None
    assert result.selected.language == "Python"


def test_embedded_prefers_native_systems_languages():
    result = LanguageSelector().select(
        "Build embedded firmware for a microcontroller"
    )

    assert result.selected is not None
    assert result.selected.language in {
        "C",
        "C++",
        "Rust",
    }


def test_browser_frontend_prefers_typescript_or_javascript():
    result = LanguageSelector().select(
        "Create a modern browser frontend"
    )

    assert result.selected is not None
    assert result.selected.language in {
        "TypeScript",
        "JavaScript",
    }


def test_machine_learning_prefers_python():
    result = LanguageSelector().select(
        "Create a machine learning system for image analysis"
    )

    assert result.selected is not None
    assert result.selected.language == "Python"


def test_high_performance_backend_does_not_prefer_python():
    result = LanguageSelector().select(
        "Build a high performance concurrent backend with very low latency"
    )

    assert result.selected is not None
    assert result.selected.language in {
        "Rust",
        "Go",
        "C++",
        "C",
    }


def test_memory_safe_systems_prefers_rust():
    result = LanguageSelector().select(
        "Build a memory-safe systems component"
    )

    assert result.selected is not None
    assert result.selected.language == "Rust"


def test_explicit_language_remains_authoritative():
    result = LanguageSelector().select(
        "Build a high performance backend in Python"
    )

    assert result.selected is not None
    assert result.selected.language == "Python"
