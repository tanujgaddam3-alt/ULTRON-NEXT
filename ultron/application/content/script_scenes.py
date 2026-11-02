from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


SCENE_SCHEMA_VERSION = 2
SCENE_PLANNER_VERSION = "deterministic-heuristic-v2"
MAX_SCENE_SCRIPT_CHARACTERS = 20_000
MAX_SCENE_LIMIT = 140
_VISUAL_STRATEGY_SUPPORT = {
    "COMPARISON_LAYOUT": "SUPPORTED",
    "PROCESS_DIAGRAM": "SUPPORTED",
    "NUMERICAL_CALLOUT": "SUPPORTED",
    "CONCEPT_MAP_DIAGRAM": "SUPPORTED",
    "PROCESS_ANIMATION": "UNSUPPORTED",
    "TIMELINE_LAYOUT": "UNSUPPORTED",
    "CINEMATIC_ESTABLISHING_SHOT": "UNSUPPORTED",
    "SUBJECT_OR_OBJECT_ACTION": "UNSUPPORTED",
}
_NARRATIVE_ROLES = frozenset(
    {
        "EXPLANATION",
        "PROCESS",
        "COMPARISON",
        "CAUSE_EFFECT",
        "NUMERICAL_FACT",
        "TIMELINE",
        "QUESTION_ANSWER",
        "SUMMARY",
    }
)
_VISUAL_THEMES = {
    "BOTANICAL": (
        (11, 34, 32),
        (25, 62, 54),
        (121, 231, 163),
        (241, 190, 113),
    ),
    "DIGITAL": (
        (13, 23, 48),
        (24, 43, 77),
        (101, 218, 255),
        (255, 139, 120),
    ),
    "HUMAN": (
        (37, 22, 43),
        (66, 35, 62),
        (255, 160, 174),
        (255, 204, 129),
    ),
    "LANDSCAPE": (
        (13, 34, 48),
        (22, 58, 71),
        (97, 219, 205),
        (244, 191, 113),
    ),
    "EDITORIAL": (
        (25, 26, 34),
        (45, 43, 51),
        (245, 196, 91),
        (153, 222, 214),
    ),
}


@dataclass(frozen=True, slots=True)
class SceneVisualInstructions:
    layout: str
    labels: tuple[str, ...]
    relationship: str
    source_text_scope: str

    def to_dict(self) -> dict[str, object]:
        return {
            "layout": self.layout,
            "labels": list(self.labels),
            "relationship": self.relationship,
            "sourceTextScope": self.source_text_scope,
        }

    @classmethod
    def from_dict(cls, value: object) -> SceneVisualInstructions:
        if not isinstance(value, dict) or set(value) != {
            "layout",
            "labels",
            "relationship",
            "sourceTextScope",
        }:
            raise ValueError("Scene visual instructions are malformed.")
        labels = value["labels"]
        if (
            not isinstance(labels, list)
            or any(not isinstance(label, str) for label in labels)
        ):
            raise ValueError("Scene visual labels are malformed.")
        return cls(
            layout=value["layout"],
            labels=tuple(labels),
            relationship=value["relationship"],
            source_text_scope=value["sourceTextScope"],
        )


@dataclass(frozen=True, slots=True)
class ScriptScene:
    scene_id: str
    order: int
    start_offset: int
    end_offset: int
    excerpt: str
    visual_type: str
    labels: tuple[str, ...]
    narrative_role: str
    key_message: str
    visual_strategy: str
    renderer_support: str
    visual_instructions: SceneVisualInstructions
    narration_ref: str
    caption_ref: str
    timing: None = None
    asset_state: str = "NOT_GENERATED"
    technical_validation: str = "NOT_RUN"
    planner_version: str = SCENE_PLANNER_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "sceneId": self.scene_id,
            "order": self.order,
            "scriptOffsets": {
                "start": self.start_offset,
                "endExclusive": self.end_offset,
            },
            "excerpt": self.excerpt,
            "excerptSha256": hashlib.sha256(
                self.excerpt.encode("utf-8")
            ).hexdigest(),
            "narrativeRole": self.narrative_role,
            "keyMessage": self.key_message,
            "keyMessageSource": "SCRIPT_EXCERPT",
            "visualType": self.visual_type,
            "visualStrategy": self.visual_strategy,
            "rendererSupport": self.renderer_support,
            "visualInstructions": self.visual_instructions.to_dict(),
            "narrationRef": self.narration_ref,
            "captionRef": self.caption_ref,
            "timing": self.timing,
            "assetState": self.asset_state,
            "technicalValidation": self.technical_validation,
            "plannerVersion": self.planner_version,
        }

    @classmethod
    def from_dict(cls, value: object) -> ScriptScene:
        required = {
            "sceneId",
            "order",
            "scriptOffsets",
            "excerpt",
            "excerptSha256",
            "narrativeRole",
            "keyMessage",
            "keyMessageSource",
            "visualType",
            "visualStrategy",
            "rendererSupport",
            "visualInstructions",
            "narrationRef",
            "captionRef",
            "timing",
            "assetState",
            "technicalValidation",
            "plannerVersion",
        }
        if not isinstance(value, dict) or set(value) != required:
            raise ValueError("Serialized scene contract has missing or extra fields.")
        offsets = value["scriptOffsets"]
        if not isinstance(offsets, dict) or set(offsets) != {
            "start",
            "endExclusive",
        }:
            raise ValueError("Serialized scene source offsets are malformed.")
        if value["keyMessageSource"] != "SCRIPT_EXCERPT":
            raise ValueError("Scene key message must remain bound to source text.")
        try:
            scene = cls(
                scene_id=value["sceneId"],
                order=value["order"],
                start_offset=offsets["start"],
                end_offset=offsets["endExclusive"],
                excerpt=value["excerpt"],
                visual_type=value["visualType"],
                labels=tuple(value["visualInstructions"]["labels"]),
                narrative_role=value["narrativeRole"],
                key_message=value["keyMessage"],
                visual_strategy=value["visualStrategy"],
                renderer_support=value["rendererSupport"],
                visual_instructions=SceneVisualInstructions.from_dict(
                    value["visualInstructions"]
                ),
                narration_ref=value["narrationRef"],
                caption_ref=value["captionRef"],
                timing=value["timing"],
                asset_state=value["assetState"],
                technical_validation=value["technicalValidation"],
                planner_version=value["plannerVersion"],
            )
        except (KeyError, TypeError) as exc:
            raise ValueError("Serialized scene contract is malformed.") from exc
        _validate_scene_fields(scene)
        if value["excerptSha256"] != hashlib.sha256(
            scene.excerpt.encode("utf-8")
        ).hexdigest():
            raise ValueError("Serialized scene excerpt hash does not match its text.")
        return scene


class SceneVisualRenderError(RuntimeError):
    def __init__(self, scene_id: str, failure_code: str) -> None:
        if (
            not isinstance(scene_id, str)
            or not re.fullmatch(r"scene-\d{2}", scene_id)
            or failure_code not in {
                "SCENE_COMPOSITION_FAILED",
                "SCENE_ASSET_VALIDATION_FAILED",
            }
        ):
            raise ValueError("Scene visual failure identity is invalid.")
        self.scene_id = scene_id
        self.failure_code = failure_code
        super().__init__(
            f"Scene {scene_id} failed local visual production ({failure_code})."
        )


_SENTENCES = re.compile(r"\S.*?(?:[.!?](?=\s|$)|$)", re.DOTALL)
_WORDS = re.compile(r"\b[\w'-]+\b", re.UNICODE)
_COMPARISON_MARKER = re.compile(
    r"\b(?:but|however|whereas|versus|vs\.?)\b",
    re.IGNORECASE,
)
_STOP_WORDS = frozenset(
    {
        "a", "about", "after", "all", "also", "an", "and", "are", "as", "at",
        "be", "because", "been", "before", "being", "between", "both", "but",
        "by", "can", "could", "did", "do", "does", "for", "from", "had", "has",
        "first", "finally", "have", "he", "her", "here", "hers", "him", "his",
        "how", "if", "in",
        "into", "is", "it", "its", "just", "may", "more", "most", "much", "must",
        "no", "next", "not", "of", "on", "one", "or", "our", "out", "over", "she", "so",
        "some", "than", "that", "the", "their", "them", "then", "there", "these",
        "they", "this", "those", "through", "to", "too", "under", "up", "us",
        "was", "we", "were", "what", "when", "where", "which", "while", "who",
        "why", "will", "with", "would", "you", "your",
    }
)


def _trim_span(script: str, start: int, end: int) -> tuple[int, int]:
    while start < end and script[start].isspace():
        start += 1
    while end > start and script[end - 1].isspace():
        end -= 1
    return start, end


def _split_long_span(
    script: str,
    start: int,
    end: int,
    *,
    max_characters: int,
) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    while end - start > max_characters:
        boundary = script.rfind(" ", start + 1, start + max_characters + 1)
        if boundary <= start:
            boundary = start + max_characters
        trimmed = _trim_span(script, start, boundary)
        if trimmed[0] < trimmed[1]:
            spans.append(trimmed)
        start = boundary
    trimmed = _trim_span(script, start, end)
    if trimmed[0] < trimmed[1]:
        spans.append(trimmed)
    return spans


def _scene_labels(text: str) -> tuple[str, ...]:
    labels: list[str] = []
    seen: set[str] = set()
    for match in _WORDS.finditer(text):
        word = match.group()
        folded = word.casefold()
        if (
            len(word) < 3
            or len(word) > 24
            or folded in _STOP_WORDS
            or folded in seen
        ):
            continue
        seen.add(folded)
        labels.append(word)
        if len(labels) == 4:
            break
    if not labels:
        labels = [match.group() for match in _WORDS.finditer(text)][:4]
    return tuple(labels)


def _visual_type(text: str) -> str:
    lowered = text.casefold()
    if _COMPARISON_MARKER.search(lowered):
        return "COMPARISON"
    if re.search(r"\b(?:first|next|then|finally|before|after|because|therefore)\b", lowered):
        return "SEQUENCE"
    if re.search(r"\b\d+(?:[.,]\d+)?%?\b", text):
        return "NUMERIC_CALLOUT"
    return "CONCEPT_MAP"


def _narrative_role(text: str) -> str:
    lowered = text.casefold()
    if _COMPARISON_MARKER.search(lowered):
        return "COMPARISON"
    if re.search(
        r"\b(?:timeline|over time|in (?:19|20)\d{2}|by (?:19|20)\d{2}|"
        r"during the \d{4}s|century|decade)\b",
        lowered,
    ):
        return "TIMELINE"
    if re.search(r"\?\s+\S", text):
        return "QUESTION_ANSWER"
    if re.search(
        r"\b(?:in conclusion|to summarize|in summary|overall|in short|"
        r"to conclude|finally)\b",
        lowered,
    ):
        return "SUMMARY"
    if re.search(
        r"\b(?:because|therefore|as a result|results? in|leads? to|"
        r"causes?|due to|consequently)\b",
        lowered,
    ):
        return "CAUSE_EFFECT"
    if re.search(
        r"\b(?:first|next|then|after that|finally|step \d+|before)\b",
        lowered,
    ):
        return "PROCESS"
    if re.search(r"\b\d+(?:[.,]\d+)?%?\b", text):
        return "NUMERICAL_FACT"
    return "EXPLANATION"


def _visual_strategy(visual_type: str) -> str:
    return {
        "COMPARISON": "COMPARISON_LAYOUT",
        "SEQUENCE": "PROCESS_DIAGRAM",
        "NUMERIC_CALLOUT": "NUMERICAL_CALLOUT",
        "CONCEPT_MAP": "CONCEPT_MAP_DIAGRAM",
    }[visual_type]


def _diagram_labels(text: str, visual_type: str) -> tuple[str, ...]:
    if visual_type == "COMPARISON":
        marker = _COMPARISON_MARKER.search(text)
        if marker is not None:
            left = text[: marker.start()].strip(" \t\r\n,;:.")
            right = text[marker.end() :].strip(" \t\r\n,;:.")
            if left and right:
                return left, right
    return _scene_labels(text)


def _visual_instructions(
    text: str,
    visual_type: str,
    strategy: str,
) -> SceneVisualInstructions:
    relationship = {
        "COMPARISON": "CONTRAST_MARKER_HEURISTIC",
        "SEQUENCE": "SOURCE_WORD_ORDER_ONLY",
        "NUMERIC_CALLOUT": "SCRIPT_DIGITS_ONLY",
        "CONCEPT_MAP": "EXTRACTED_SCRIPT_TERMS_ONLY",
    }[visual_type]
    return SceneVisualInstructions(
        layout=strategy,
        labels=_diagram_labels(text, visual_type),
        relationship=relationship,
        source_text_scope="EXACT_SCENE_EXCERPT",
    )


def _validate_scene_fields(scene: ScriptScene) -> None:
    if (
        not isinstance(scene.scene_id, str)
        or not re.fullmatch(r"scene-\d{2,3}", scene.scene_id)
        or type(scene.order) is not int
        or not 1 <= scene.order <= MAX_SCENE_LIMIT
        or scene.scene_id != f"scene-{scene.order:02d}"
        or type(scene.start_offset) is not int
        or type(scene.end_offset) is not int
        or scene.start_offset < 0
        or scene.end_offset <= scene.start_offset
        or not isinstance(scene.excerpt, str)
        or not scene.excerpt
        or scene.end_offset - scene.start_offset > 144
        or not isinstance(scene.visual_type, str)
        or scene.visual_type not in {
            "COMPARISON",
            "SEQUENCE",
            "NUMERIC_CALLOUT",
            "CONCEPT_MAP",
        }
        or not isinstance(scene.narrative_role, str)
        or scene.narrative_role not in _NARRATIVE_ROLES
        or scene.key_message != scene.excerpt
        or not isinstance(scene.visual_strategy, str)
        or scene.visual_strategy not in _VISUAL_STRATEGY_SUPPORT
        or not isinstance(scene.renderer_support, str)
        or scene.renderer_support
        != _VISUAL_STRATEGY_SUPPORT.get(scene.visual_strategy)
        or type(scene.visual_instructions) is not SceneVisualInstructions
        or not isinstance(scene.labels, tuple)
        or scene.visual_instructions.layout != scene.visual_strategy
        or scene.visual_instructions.labels != scene.labels
        or scene.visual_instructions.source_text_scope != "EXACT_SCENE_EXCERPT"
        or not isinstance(scene.visual_instructions.relationship, str)
        or scene.visual_instructions.relationship
        not in {
            "CONTRAST_MARKER_HEURISTIC",
            "SOURCE_WORD_ORDER_ONLY",
            "SCRIPT_DIGITS_ONLY",
            "EXTRACTED_SCRIPT_TERMS_ONLY",
        }
        or not 1 <= len(scene.labels) <= 4
        or any(
            not isinstance(label, str) or not label or len(label) > 144
            for label in scene.labels
        )
        or scene.narration_ref != f"{scene.scene_id}:narration"
        or scene.caption_ref != f"{scene.scene_id}:caption"
        or scene.timing is not None
        or scene.asset_state != "NOT_GENERATED"
        or scene.technical_validation != "NOT_RUN"
        or scene.planner_version != SCENE_PLANNER_VERSION
    ):
        raise ValueError("Scene contract contains invalid or unsupported fields.")


def validate_scene_plan(
    script: str,
    scenes: tuple[ScriptScene, ...],
    *,
    max_scenes: int = MAX_SCENE_LIMIT,
    max_characters_per_scene: int = 144,
) -> None:
    """Validate exact spans; only whitespace may be omitted between/around scenes."""
    if (
        not isinstance(script, str)
        or len(script) > MAX_SCENE_SCRIPT_CHARACTERS
        or not isinstance(scenes, tuple)
        or not scenes
        or type(max_scenes) is not int
        or not 1 <= max_scenes <= MAX_SCENE_LIMIT
        or type(max_characters_per_scene) is not int
        or not 36 <= max_characters_per_scene <= 144
        or len(scenes) > max_scenes
    ):
        raise ValueError("Scene plan is empty or exceeds its configured bounds.")
    try:
        script.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("Scene plan source must be valid UTF-8 text.") from exc
    previous_end = 0
    identities: set[str] = set()
    for expected_order, scene in enumerate(scenes, start=1):
        if type(scene) is not ScriptScene:
            raise ValueError("Scene plan contains an unsupported record.")
        _validate_scene_fields(scene)
        if (
            scene.order != expected_order
            or scene.scene_id in identities
            or scene.end_offset > len(script)
            or scene.end_offset - scene.start_offset > max_characters_per_scene
            or scene.start_offset < previous_end
            or (
                script[previous_end : scene.start_offset]
                and not script[previous_end : scene.start_offset].isspace()
            )
            or script[scene.start_offset : scene.end_offset] != scene.excerpt
            or hashlib.sha256(scene.excerpt.encode("utf-8")).hexdigest()
            != hashlib.sha256(
                script[scene.start_offset : scene.end_offset].encode("utf-8")
            ).hexdigest()
            or scene.visual_type != _visual_type(scene.excerpt)
            or scene.narrative_role != _narrative_role(scene.excerpt)
            or scene.visual_instructions
            != _visual_instructions(
                scene.excerpt,
                scene.visual_type,
                scene.visual_strategy,
            )
        ):
            raise ValueError("Scene plan identity, source span, or bindings are invalid.")
        identities.add(scene.scene_id)
        previous_end = scene.end_offset
    if script[previous_end:] and not script[previous_end:].isspace():
        raise ValueError("Scene plan leaves non-whitespace source text unexplained.")


def serialize_scene_plan(scenes: tuple[ScriptScene, ...]) -> str:
    return json.dumps(
        {
            "schemaVersion": SCENE_SCHEMA_VERSION,
            "plannerVersion": SCENE_PLANNER_VERSION,
            "scenes": [scene.to_dict() for scene in scenes],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def deserialize_scene_plan(script: str, serialized: str) -> tuple[ScriptScene, ...]:
    try:
        payload = json.loads(serialized)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Serialized scene plan is invalid JSON.") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schemaVersion") != SCENE_SCHEMA_VERSION
        or payload.get("plannerVersion") != SCENE_PLANNER_VERSION
        or not isinstance(payload.get("scenes"), list)
    ):
        raise ValueError("Serialized scene plan version or shape is unsupported.")
    scenes = tuple(ScriptScene.from_dict(item) for item in payload["scenes"])
    validate_scene_plan(script, scenes)
    if serialize_scene_plan(scenes) != serialized:
        raise ValueError("Serialized scene plan is not in canonical form.")
    return scenes


def plan_script_scenes(
    script: str,
    *,
    max_scenes: int = MAX_SCENE_LIMIT,
    max_characters_per_scene: int = 144,
) -> tuple[ScriptScene, ...]:
    if (
        not isinstance(script, str)
        or not script.strip()
        or type(max_scenes) is not int
        or not 1 <= max_scenes <= MAX_SCENE_LIMIT
        or type(max_characters_per_scene) is not int
        or not 36 <= max_characters_per_scene <= 144
    ):
        raise ValueError("Script scene planning input is invalid.")
    if len(script) > MAX_SCENE_SCRIPT_CHARACTERS:
        raise ValueError(
            f"Scene planning source exceeds {MAX_SCENE_SCRIPT_CHARACTERS} characters."
        )
    try:
        script.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("Script scene planning requires valid UTF-8 text.") from exc

    spans: list[tuple[int, int]] = []
    for match in _SENTENCES.finditer(script):
        start, end = _trim_span(script, match.start(), match.end())
        if start < end:
            spans.extend(
                _split_long_span(
                    script,
                    start,
                    end,
                    max_characters=max_characters_per_scene,
                )
            )

    grouped: list[tuple[int, int]] = []
    for start, end in spans:
        if (
            grouped
            and end - grouped[-1][0] <= max_characters_per_scene
        ):
            grouped[-1] = (grouped[-1][0], end)
        else:
            grouped.append((start, end))
    if len(grouped) > max_scenes:
        raise ValueError(
            f"Script needs more than {max_scenes} scenes at the configured caption limit."
        )

    scenes: list[ScriptScene] = []
    for order, (start, end) in enumerate(grouped, start=1):
        excerpt = script[start:end]
        visual_type = _visual_type(excerpt)
        strategy = _visual_strategy(visual_type)
        scenes.append(
            ScriptScene(
                scene_id=f"scene-{order:02d}",
                order=order,
                start_offset=start,
                end_offset=end,
                excerpt=excerpt,
                visual_type=visual_type,
                labels=_diagram_labels(excerpt, visual_type),
                narrative_role=_narrative_role(excerpt),
                key_message=excerpt,
                visual_strategy=strategy,
                renderer_support=_VISUAL_STRATEGY_SUPPORT[strategy],
                visual_instructions=_visual_instructions(
                    excerpt,
                    visual_type,
                    strategy,
                ),
                narration_ref=f"scene-{order:02d}:narration",
                caption_ref=f"scene-{order:02d}:caption",
            )
        )
    result = tuple(scenes)
    validate_scene_plan(
        script,
        result,
        max_scenes=max_scenes,
        max_characters_per_scene=max_characters_per_scene,
    )
    return result


def _font(path: Path, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(str(path), size=size)
    except OSError as exc:
        raise RuntimeError("The installed font required for local diagrams is unavailable.") from exc


def _wrapped_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.FreeTypeFont,
    *,
    max_width: int,
    max_lines: int = 2,
    truncate: bool = True,
) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        if draw.textbbox((0, 0), word, font=font)[2] > max_width:
            if current:
                lines.append(current)
                current = ""
            chunk = ""
            for character in word:
                candidate = f"{chunk}{character}"
                if (
                    chunk
                    and draw.textbbox((0, 0), candidate, font=font)[2] > max_width
                ):
                    lines.append(chunk)
                    chunk = character
                else:
                    chunk = candidate
            current = chunk
            continue
        candidate = f"{current} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    if truncate and len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and draw.textbbox((0, 0), f"{last}...", font=font)[2] > max_width:
            last = last[:-1].rstrip()
        lines[-1] = f"{last}..."
    return lines


def _draw_card(
    draw: ImageDraw.ImageDraw,
    bounds: tuple[int, int, int, int],
    label: str,
    *,
    font_path: Path,
    accent: tuple[int, int, int],
) -> None:
    left, top, right, bottom = bounds
    draw.rounded_rectangle(
        bounds,
        radius=22,
        fill=(30, 50, 70),
        outline=accent,
        width=3,
    )
    font_size = 30
    max_width = right - left - 32
    available_height = bottom - top - 32
    for candidate_size in (30, 28, 26, 24, 22, 20, 18):
        candidate_font = _font(font_path, candidate_size)
        candidate_line_height = candidate_size + 10
        candidate_max_lines = max(
            1,
            min(6, available_height // candidate_line_height),
        )
        candidate_lines = _wrapped_lines(
            draw,
            label,
            candidate_font,
            max_width=max_width,
            max_lines=candidate_max_lines,
            truncate=False,
        )
        if (
            candidate_lines
            and len(candidate_lines) <= candidate_max_lines
            and all(
                draw.textbbox((0, 0), line, font=candidate_font)[2] <= max_width
                for line in candidate_lines
            )
        ):
            font_size = candidate_size
            line_height = candidate_line_height
            lines = candidate_lines
            break
    else:
        raise ValueError("Scene visual label cannot fit within its card bounds.")
    y = top + (bottom - top - len(lines) * line_height) // 2
    for line in lines:
        font = _font(font_path, font_size)
        if draw.textbbox((0, 0), line, font=font)[2] > max_width:
            raise ValueError("Scene visual label exceeds its safe card width.")
        draw.text(
            (left + 16, y),
            line,
            font=font,
            fill=(244, 248, 250),
        )
        y += line_height


def rendered_scene_composition(scene: ScriptScene) -> str:
    if scene.visual_type == "COMPARISON":
        return "COMPARISON_LAYOUT"
    if scene.narrative_role == "TIMELINE":
        return "TIMELINE_LAYOUT"
    if scene.narrative_role == "QUESTION_ANSWER":
        return "QUESTION_ANSWER_LAYOUT"
    if scene.narrative_role == "SUMMARY":
        return "CONCLUSION_LAYOUT"
    if scene.narrative_role == "CAUSE_EFFECT":
        return "CAUSE_EFFECT_DIAGRAM"
    if scene.visual_type == "NUMERIC_CALLOUT":
        return "NUMERICAL_CALLOUT"
    if scene.visual_type == "SEQUENCE":
        return "PROCESS_DIAGRAM"
    return "EXPLANATORY_CONCEPT_MAP"


def visual_theme(text: str) -> str:
    lowered = text.casefold()
    themes = (
        ("BOTANICAL", ("plant", "leaf", "leaves", "root", "chlorophyll", "flower")),
        ("DIGITAL", ("software", "technology", "digital", "computer", "robot", "code", " ai ")),
        ("HUMAN", ("health", "body", "brain", "heart", "patient", "muscle")),
        ("LANDSCAPE", ("ocean", "water", "climate", "river", "earth", "mountain")),
    )
    padded = f" {lowered} "
    for theme, terms in themes:
        if any(term in padded for term in terms):
            return theme
    return "EDITORIAL"


def visual_treatment(scene: ScriptScene) -> str:
    lowered = scene.excerpt.casefold()
    if re.search(
        r"\b(?:not authorized|not published|rights|factual claims|publication)\b",
        lowered,
    ):
        return "REVIEW_BOUNDARY"
    if re.search(r"\b(?:synthetic|verification|baseline|test)\b", lowered):
        return "LOCAL_TEST_HERO"
    if scene.narrative_role == "COMPARISON":
        return "SPLIT_COMPARISON"
    if scene.narrative_role == "PROCESS":
        return "SEQUENTIAL_CARDS"
    if scene.narrative_role == "NUMERICAL_FACT":
        return "NUMBER_HERO"
    if scene.narrative_role == "TIMELINE":
        return "TIMELINE_RAIL"
    if scene.narrative_role == "QUESTION_ANSWER":
        return "QUESTION_ANSWER"
    if scene.narrative_role == "SUMMARY":
        return "EDITORIAL_CLOSE"
    return "EDITORIAL_HERO" if scene.order == 1 else "EDITORIAL_FEATURE"


def _draw_visual_composition(
    draw: ImageDraw.ImageDraw,
    scene: ScriptScene,
    *,
    font_path: Path,
    accent: tuple[int, int, int],
) -> None:
    labels = scene.labels or ("Script",)
    composition = rendered_scene_composition(scene)
    if composition == "COMPARISON_LAYOUT" and len(labels) > 1:
        _draw_card(
            draw, (68, 412, 332, 676), labels[0],
            font_path=font_path, accent=accent,
        )
        _draw_card(
            draw, (388, 412, 652, 676), labels[1],
            font_path=font_path, accent=(255, 185, 92),
        )
        draw.line((336, 544, 370, 544), fill=(218, 232, 240), width=5)
        draw.polygon(((370, 534), (384, 544), (370, 554)), fill=(218, 232, 240))
        return

    if composition == "NUMERICAL_CALLOUT":
        numbers = [
            match.group()
            for match in re.finditer(r"\b\d+(?:[.,]\d+)?%?\b", scene.excerpt)
        ][:3]
        if not numbers:
            raise ValueError("Numerical scene has no source-bound numeric label.")
        width = 176
        start_x = (720 - len(numbers) * width - (len(numbers) - 1) * 24) // 2
        for index, value in enumerate(numbers):
            left = start_x + index * (width + 24)
            draw.ellipse(
                (left, 438, left + width, 614),
                fill=(30, 50, 70),
                outline=accent if index % 2 == 0 else (255, 185, 92),
                width=4,
            )
            font_size = 43 if len(value) <= 6 else 30
            font = _font(font_path, font_size)
            box = draw.textbbox((0, 0), value, font=font)
            while box[2] - box[0] > width - 24 and font_size > 16:
                font_size -= 2
                font = _font(font_path, font_size)
                box = draw.textbbox((0, 0), value, font=font)
            draw.text(
                (left + (width - (box[2] - box[0])) // 2, 494),
                value,
                font=font,
                fill=(244, 248, 250),
            )
        _draw_card(
            draw, (68, 658, 652, 818), "Source fact",
            font_path=font_path, accent=accent,
        )
        return

    if composition == "PROCESS_DIAGRAM":
        process_steps = [
            part.strip(" \t\r\n,;:.")
            for part in re.split(
                r"\b(?:first|next|then|after that|finally|step\s+\d+)\b",
                scene.excerpt,
                flags=re.IGNORECASE,
            )
            if part.strip(" \t\r\n,;:.")
        ]
        process_labels = process_steps if len(process_steps) > 1 else list(labels)
        count = min(len(process_labels), 3)
        width = 174
        start_x = (720 - count * width - (count - 1) * 24) // 2
        centers: list[int] = []
        for index, label in enumerate(process_labels[:count]):
            left = start_x + index * (width + 24)
            _draw_card(
                draw, (left, 426, left + width, 658), label,
                font_path=font_path,
                accent=accent if index % 2 == 0 else (255, 185, 92),
            )
            centers.append(left + width // 2)
        for left, right in zip(centers, centers[1:]):
            gap_right = right - width // 2
            draw.line(
                (left + width // 2 + 2, 542, gap_right - 12, 542),
                fill=(218, 232, 240),
                width=4,
            )
            draw.polygon(
                (
                    (gap_right - 18, 533),
                    (gap_right - 2, 542),
                    (gap_right - 18, 551),
                ),
                fill=(218, 232, 240),
            )
        return

    if composition == "CAUSE_EFFECT_DIAGRAM":
        _draw_card(
            draw, (68, 426, 332, 658), f"CAUSE · {labels[0]}",
            font_path=font_path, accent=accent,
        )
        _draw_card(
            draw, (388, 426, 652, 658),
            f"EFFECT · {labels[-1] if len(labels) > 1 else labels[0]}",
            font_path=font_path, accent=(255, 185, 92),
        )
        draw.line((336, 542, 384, 542), fill=(218, 232, 240), width=5)
        draw.polygon(((384, 532), (402, 542), (384, 552)), fill=(218, 232, 240))
        return

    if composition == "TIMELINE_LAYOUT":
        years = sorted(
            {int(match.group()) for match in re.finditer(r"\b(?:19|20)\d{2}\b", scene.excerpt)}
        )
        if years:
            draw.text(
                (64, 350),
                "Years ordered numerically; event match requires review",
                font=_font(font_path, 14),
                fill=(255, 185, 92),
            )
            displayed = [str(year) for year in years[:4]]
        else:
            draw.text(
                (64, 350),
                "Order stated in script; dates not provided",
                font=_font(font_path, 14),
                fill=(255, 185, 92),
            )
            displayed = list(labels[:4])
        count = len(displayed)
        centers = [
            112 + index * (496 // max(1, count - 1))
            for index in range(count)
        ]
        if len(centers) > 1:
            draw.line((centers[0], 552, centers[-1], 552), fill=(218, 232, 240), width=4)
        for index, (center, label) in enumerate(zip(centers, displayed), start=1):
            draw.ellipse(
                (center - 15, 537, center + 15, 567),
                fill=accent if index % 2 else (255, 185, 92),
            )
            _draw_card(
                draw,
                (max(48, center - 88), 430, min(672, center + 88), 520),
                label,
                font_path=font_path,
                accent=accent,
            )
        return

    if composition == "QUESTION_ANSWER_LAYOUT":
        question_end = scene.excerpt.find("?")
        question = scene.excerpt[: question_end + 1].strip()
        answer = scene.excerpt[question_end + 1 :].strip()
        _draw_card(
            draw, (68, 378, 652, 570), f"QUESTION · {question}",
            font_path=font_path, accent=accent,
        )
        _draw_card(
            draw,
            (68, 650, 652, 842),
            f"ANSWER · {answer}" if answer else "ANSWER · NOT PROVIDED IN THIS SCENE",
            font_path=font_path,
            accent=(255, 185, 92),
        )
        return

    if composition == "CONCLUSION_LAYOUT":
        _draw_card(
            draw, (68, 404, 652, 720), scene.excerpt,
            font_path=font_path, accent=(255, 185, 92),
        )
        draw.line((180, 768, 540, 768), fill=accent, width=6)
        return

    center_x, center_y = 360, 552
    center_label = labels[0]
    _draw_card(
        draw, (220, 462, 500, 642), center_label,
        font_path=font_path, accent=accent,
    )
    support_positions = ((150, 362), (570, 362), (150, 742), (570, 742))
    for index, label in enumerate(labels[1:4]):
        x, y = support_positions[index]
        draw.line((center_x, center_y, x, y), fill=(80, 112, 135), width=4)
        draw.ellipse(
            (x - 82, y - 62, x + 82, y + 62),
            fill=(30, 50, 70),
            outline=accent if index % 2 == 0 else (255, 185, 92),
            width=3,
        )
        font = _font(font_path, 24)
        lines = _wrapped_lines(draw, label, font, max_width=132)
        line_y = y - (len(lines) * 32) // 2
        for line in lines:
            box = draw.textbbox((0, 0), line, font=font)
            draw.text(
                (x - (box[2] - box[0]) // 2, line_y),
                line,
                font=font,
                fill=(244, 248, 250),
            )
            line_y += 32


def render_scene_diagram(
    scene: ScriptScene,
    destination: Path,
    *,
    scene_count: int,
    font_path: Path = Path("/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf"),
) -> None:
    if (
        not isinstance(destination, Path)
        or not 1 <= scene_count <= 12
        or not font_path.is_file()
    ):
        raise ValueError("Scene diagram output or font configuration is invalid.")
    _validate_scene_fields(scene)
    if scene.renderer_support != "SUPPORTED":
        raise ValueError(
            f"Scene visual strategy is not supported by this renderer: "
            f"{scene.visual_strategy}."
        )
    theme = visual_theme(scene.excerpt)
    background_top, background_bottom, accent, secondary = _VISUAL_THEMES[theme]
    image = Image.new("RGB", (720, 1280), background_top)
    draw = ImageDraw.Draw(image)
    for y in range(0, 1280, 8):
        blend = y / 1279
        color = tuple(
            round(start + (finish - start) * blend)
            for start, finish in zip(background_top, background_bottom)
        )
        draw.rectangle((0, y, 720, min(y + 7, 1279)), fill=color)
    draw.ellipse(
        (440, -230, 930, 260),
        outline=accent,
        width=2,
    )
    draw.ellipse(
        (480, -190, 870, 200),
        outline=secondary,
        width=2,
    )
    draw.rectangle((0, 0, 10, 1280), fill=accent)
    draw.rectangle((48, 62, 104, 68), fill=accent)
    draw.text(
        (48, 88),
        f"{theme}  /  FIELD NOTE {scene.order:02d}",
        font=_font(font_path, 19),
        fill=(211, 221, 229),
    )
    draw.text(
        (48, 122),
        f"{scene.order:02d} — {scene.narrative_role.replace('_', ' ')}",
        font=_font(font_path, 23),
        fill=accent,
    )
    treatment = visual_treatment(scene)
    if treatment in {"LOCAL_TEST_HERO", "REVIEW_BOUNDARY", "EDITORIAL_HERO", "EDITORIAL_FEATURE"}:
        draw.rounded_rectangle(
            (42, 220, 678, 912),
            radius=32,
            fill=background_bottom,
            outline=accent,
            width=2,
        )
        draw.rectangle((42, 220, 54, 912), fill=accent)
        if treatment == "REVIEW_BOUNDARY":
            eyebrow = "REVIEW BOUNDARY"
            title = "REVIEW"
            title_color = secondary
        elif treatment == "LOCAL_TEST_HERO":
            eyebrow = "LOCAL RENDER TEST"
            title = scene.labels[0].upper() if scene.labels else "SYNTHETIC"
            title_color = accent
        elif treatment == "EDITORIAL_HERO":
            eyebrow = "THE CORE IDEA"
            title = scene.labels[0].upper() if scene.labels else "STORY"
            title_color = accent
        else:
            eyebrow = "SUPPORTING DETAIL"
            title = scene.labels[0].upper() if scene.labels else "DETAIL"
            title_color = secondary
        draw.text((82, 270), eyebrow, font=_font(font_path, 20), fill=secondary)
        headline_font = _font(font_path, 72)
        headline_lines = _wrapped_lines(
            draw,
            title,
            headline_font,
            max_width=530,
            max_lines=2,
        )
        y = 340
        for line in headline_lines:
            draw.text((82, y), line, font=headline_font, fill=title_color)
            y += 84
        draw.line((82, y + 8, 600, y + 8), fill=(*accent, 170), width=3)
        excerpt_lines = _wrapped_lines(
            draw,
            scene.excerpt,
            _font(font_path, 30),
            max_width=520,
            max_lines=4,
        )
        y += 48
        for line in excerpt_lines:
            draw.text((82, y), line, font=_font(font_path, 30), fill=(240, 239, 231))
            y += 44
        draw.text(
            (82, 850),
            f"SOURCE-BOUND  ·  SCENE {scene.order:02d}/{scene_count:02d}",
            font=_font(font_path, 17),
            fill=(194, 203, 210),
        )
    else:
        draw.rounded_rectangle(
            (42, 220, 678, 912),
            radius=32,
            fill=background_bottom,
            outline=accent,
            width=2,
        )
        _draw_visual_composition(
            draw,
            scene,
            font_path=font_path,
            accent=accent,
        )
        draw.text(
            (48, 944),
            f"{visual_treatment(scene).replace('_', ' ')}  ·  SOURCE-BOUND",
            font=_font(font_path, 17),
            fill=(205, 215, 220),
        )
    try:
        image.save(destination, format="PNG", optimize=True)
        with Image.open(destination) as decoded:
            if decoded.format != "PNG" or decoded.size != (720, 1280):
                raise ValueError("Scene visual did not pass image dimension validation.")
            decoded.verify()
    except OSError as exc:
        raise RuntimeError("The local scene diagram could not be written.") from exc
    finally:
        image.close()
