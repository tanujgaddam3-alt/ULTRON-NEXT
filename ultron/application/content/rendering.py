from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import base64
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from ultron.domain.content import (
    AssetState,
    ContentClipRender,
    ContentPrivateRenderOperation,
    ContentEditPlanAudio,
    ContentCreativeEditPlanRecord,
    ContentCreativeEditPlanV2,
    ContentEditPlanSegment,
    ContentEditPlanSource,
    ContentEditSegment,
    ContentPlatform,
    ContentTargetContext,
    PrivateRenderOperationState,
    ProcessingJobState,
    RightsAction,
    RightsState,
    RightsUse,
)
from ultron.domain.content_intelligence import (
    HighlightCandidate,
    HighlightConfig,
    TranscriptRecord,
)
from ultron.security.identity.authorization import AuthorizationContext

from .clip_renderer import (
    ClipRenderTimeout,
    FFmpegLocalClipRenderer,
    MediaInfo,
    RenderCaption,
    SourceEditSegment,
)
from .processing import ContentBlobStore
from .service import ContentRightsService
from .store import ContentStore


def _select_scene_diverse_intervals(
    duration_seconds: float,
    scene_boundaries: tuple[float, ...],
    segment_length_seconds: float,
    slot_count: int,
) -> tuple[tuple[float, float, str, float | None], ...]:
    """Choose distinct detected scenes first; explicitly mark bounded fallbacks."""
    if (
        not math.isfinite(duration_seconds)
        or duration_seconds <= 0
        or not math.isfinite(segment_length_seconds)
        or segment_length_seconds <= 0
        or type(slot_count) is not int
        or slot_count < 1
        or any(
            not math.isfinite(boundary)
            or boundary <= 0
            or boundary >= duration_seconds
            for boundary in scene_boundaries
        )
        or any(
            left >= right
            for left, right in zip(scene_boundaries, scene_boundaries[1:])
        )
    ):
        raise ValueError("Scene-diverse interval selection input is invalid.")

    scene_spans = tuple(
        zip(
            (0.0, *scene_boundaries),
            (*scene_boundaries, duration_seconds),
        )
    )
    clip_length = min(segment_length_seconds, duration_seconds)
    candidates = tuple(
        span
        for span in scene_spans
        if span[1] - span[0] + 1e-6 >= segment_length_seconds
    )
    chosen: list[tuple[float, float, str, float | None]] = []
    chosen_scene_starts: set[float] = set()

    for slot in range(slot_count):
        preferred_center = duration_seconds * (slot + 0.5) / slot_count
        available = tuple(
            span for span in candidates if span[0] not in chosen_scene_starts
        )
        if candidates:
            is_repeat = not available
            if is_repeat:
                available = candidates

            def placement(span: tuple[float, float]) -> tuple[float, float, float]:
                start, end = span
                latest_start = end - clip_length
                selected_start = min(
                    max(preferred_center - clip_length / 2.0, start),
                    latest_start,
                )
                center = selected_start + clip_length / 2.0
                return abs(center - preferred_center), start, selected_start

            selected_span = min(available, key=placement)
            scene_start, _ = selected_span
            selected_start = placement(selected_span)[2]
            selected_end = selected_start + segment_length_seconds
            if is_repeat:
                prior = next(
                    interval
                    for interval in chosen
                    if (interval[3] or 0.0) == scene_start
                )
                selected_start, selected_end = prior[0], prior[1]
                method = "repeated_detected_scene_interval"
            else:
                chosen_scene_starts.add(scene_start)
                method = "distinct_detected_scene_interval"
            selected_cut = scene_start if scene_start > 0 else None
        else:
            selected_start = min(
                max(preferred_center - clip_length / 2.0, 0.0),
                duration_seconds - clip_length,
            )
            selected_end = selected_start + clip_length
            overlaps_prior = any(
                min(selected_end, prior_end) - max(selected_start, prior_start)
                >= 0.5 * min(selected_end - selected_start, prior_end - prior_start)
                for prior_start, prior_end, _, _ in chosen
            )
            method = (
                "repeated_source_interval_fallback"
                if overlaps_prior
                else "insufficient_scene_duration_fallback"
            )
            selected_cut = None

        chosen.append(
            (
                round(selected_start, 6),
                round(min(selected_end, duration_seconds), 6),
                method,
                None if selected_cut is None else round(selected_cut, 6),
            )
        )
    return tuple(chosen)


class ContentClipRenderingService:
    """Authorized video production from an existing transcript highlight."""

    MAX_CLIP_DURATION_SECONDS = 60.0
    MAX_SOURCE_DURATION_SECONDS = 300.0
    MAX_RUNTIME_SECONDS = FFmpegLocalClipRenderer.MAX_RUNTIME_SECONDS

    def __init__(
        self,
        *,
        content_rights: ContentRightsService,
        renderer: FFmpegLocalClipRenderer | None = None,
    ) -> None:
        if type(content_rights) is not ContentRightsService:
            raise TypeError("Clip rendering requires ContentRightsService.")
        storage = content_rights.content_storage
        if storage is None or not all(
            callable(getattr(storage, name, None))
            for name in (
                "stage_clip_source",
                "create_clip_staging_path",
                "put_clip_output",
                "read_clip_output",
                "delete_clip_output",
                "delete_clip_staging",
            )
        ):
            raise RuntimeError("Managed clip storage is required for rendering.")
        if not isinstance(getattr(storage, "clip_staging_directory", None), Path):
            raise RuntimeError("Managed clip storage must expose its staging directory.")
        if renderer is not None and type(renderer) is not FFmpegLocalClipRenderer:
            raise TypeError("Clip rendering requires the supported local FFmpeg renderer.")
        staging_directory = storage.clip_staging_directory
        if renderer is not None and renderer.managed_staging_directory != staging_directory:
            raise ValueError("Renderer must use the registered content staging directory.")
        self.content_rights = content_rights
        self.store: ContentStore = content_rights.store
        self.content_storage: ContentBlobStore = storage
        self.renderer = renderer or FFmpegLocalClipRenderer(
            managed_staging_directory=staging_directory
        )

    @staticmethod
    def _platform_for_target(target: ContentTargetContext) -> ContentPlatform | None:
        return {
            ContentTargetContext.YOUTUBE_SHORTS: ContentPlatform.YOUTUBE,
            ContentTargetContext.YOUTUBE_KIDS_AUDIENCE: ContentPlatform.YOUTUBE,
            ContentTargetContext.INSTAGRAM_REELS: ContentPlatform.INSTAGRAM,
        }.get(target)

    @staticmethod
    def _load_candidate_context(
        connection: sqlite3.Connection,
        candidate_id: str,
        principal: str,
    ) -> tuple[
        HighlightCandidate,
        TranscriptRecord,
        ContentTargetContext,
        HighlightConfig,
    ]:
        row = connection.execute(
            """
            SELECT c.*, r.config_json AS highlight_config_json,
                r.transcript_fingerprint, r.transcript_id AS run_transcript_id,
                t.transcript_id AS stored_transcript_id,
                t.asset_id AS transcript_asset_id,
                t.asset_sha256 AS transcript_asset_sha256,
                t.target_context AS transcript_target_context,
                j.owner_id AS job_owner_id, j.state AS job_state,
                j.asset_id AS job_asset_id, j.asset_sha256 AS job_asset_sha256,
                j.target_context AS job_target_context
            FROM content_highlight_candidates c
            JOIN content_highlight_runs r ON r.run_id = c.run_id
            JOIN content_transcripts t ON t.transcript_id = c.transcript_id
            JOIN content_processing_jobs j ON j.job_id = t.job_id
            WHERE c.candidate_id = ?
            """,
            (candidate_id,),
        ).fetchone()
        if row is None or row["job_owner_id"] != principal:
            raise PermissionError("An owned persisted highlight candidate is required.")
        if (
            row["stored_transcript_id"] != row["run_transcript_id"]
            or row["job_state"] != ProcessingJobState.SUCCEEDED.value
            or row["asset_id"] != row["job_asset_id"]
            or row["asset_sha256"] != row["job_asset_sha256"]
            or row["asset_id"] != row["transcript_asset_id"]
            or row["asset_sha256"] != row["transcript_asset_sha256"]
            or row["transcript_target_context"] != row["job_target_context"]
        ):
            raise RuntimeError("Highlight candidate provenance linkage is inconsistent.")

        transcript_row = connection.execute(
            "SELECT * FROM content_transcripts WHERE transcript_id = ?",
            (row["stored_transcript_id"],),
        ).fetchone()
        if transcript_row is None:
            raise RuntimeError("Highlight candidate transcript is unavailable.")
        transcript = ContentStore._load_transcript(transcript_row)
        if transcript.fingerprint() != row["transcript_fingerprint"]:
            raise RuntimeError("Highlight candidate transcript fingerprint is inconsistent.")
        candidate = HighlightCandidate(
            candidate_id=row["candidate_id"],
            run_id=row["run_id"],
            transcript_id=row["transcript_id"],
            asset_id=row["asset_id"],
            asset_sha256=row["asset_sha256"],
            start_seconds=row["start_seconds"],
            end_seconds=row["end_seconds"],
            duration_seconds=row["duration_seconds"],
            rank=row["rank"],
            score=row["score"],
            scores=json.loads(row["scores_json"]),
            evidence=json.loads(row["evidence_json"]),
            transcript_segment_ids=json.loads(row["segment_ids_json"]),
            explanation=row["explanation"],
            warnings=json.loads(row["warnings_json"]),
            scoring_version=row["scoring_version"],
            created_at=row["created_at"],
        )
        positions_by_id = {
            segment.segment_id: position
            for position, segment in enumerate(transcript.segments)
        }
        positions = tuple(
            positions_by_id.get(segment_id, -1)
            for segment_id in candidate.transcript_segment_ids
        )
        if (
            not positions
            or any(position < 0 for position in positions)
            or positions != tuple(range(positions[0], positions[0] + len(positions)))
            or candidate.start_seconds != transcript.segments[positions[0]].start_seconds
            or candidate.end_seconds != transcript.segments[positions[-1]].end_seconds
            or candidate.end_seconds > transcript.duration_seconds
        ):
            raise RuntimeError("Highlight candidate timing is not transcript-linked.")
        return (
            candidate,
            transcript,
            ContentTargetContext(row["transcript_target_context"]),
            HighlightConfig.model_validate_json(row["highlight_config_json"]),
        )

    @staticmethod
    def _edit_timeline(
        candidate: HighlightCandidate,
        transcript: TranscriptRecord,
        config: HighlightConfig,
    ) -> tuple[SourceEditSegment, ...]:
        selected_ids = set(candidate.transcript_segment_ids)
        selected_segments = [
            segment
            for segment in transcript.segments
            if segment.segment_id in selected_ids
        ]
        if len(selected_segments) != len(selected_ids):
            raise RuntimeError("Highlight timeline references missing transcript segments.")
        source_ranges: list[SourceEditSegment] = []
        for segment in selected_segments:
            if (
                source_ranges
                and segment.start_seconds
                < source_ranges[-1].source_out_seconds
            ):
                previous = source_ranges[-1]
                source_ranges[-1] = SourceEditSegment(
                    source_in_seconds=previous.source_in_seconds,
                    source_out_seconds=max(previous.source_out_seconds, segment.end_seconds),
                )
            else:
                source_ranges.append(
                    SourceEditSegment(
                        source_in_seconds=segment.start_seconds,
                        source_out_seconds=segment.end_seconds,
                    )
                )
        timeline: list[SourceEditSegment] = []
        for source_range in source_ranges:
            boundaries = (
                boundary
                for boundary in config.scene_boundary_seconds
                if source_range.source_in_seconds
                < boundary
                < source_range.source_out_seconds
            )
            points = (
                source_range.source_in_seconds,
                *boundaries,
                source_range.source_out_seconds,
            )
            timeline.extend(
                SourceEditSegment(source_in_seconds=start, source_out_seconds=end)
                for start, end in zip(points, points[1:])
            )
        return tuple(timeline)

    @staticmethod
    def _timeline_manifest(
        timeline: tuple[SourceEditSegment, ...],
    ) -> dict[str, object]:
        output_offset = 0.0
        segments: list[dict[str, float | int]] = []
        for order, segment in enumerate(timeline, start=1):
            duration = segment.source_out_seconds - segment.source_in_seconds
            segments.append(
                {
                    "order": order,
                    "sourceInSeconds": segment.source_in_seconds,
                    "sourceOutSeconds": segment.source_out_seconds,
                    "outputInSeconds": output_offset,
                    "outputOutSeconds": output_offset + duration,
                }
            )
            output_offset += duration
        return {
            "version": "cre-source-edl-v1",
            "assembly": "ordered_hard_cuts",
            "durationSeconds": output_offset,
            "segments": segments,
        }

    @staticmethod
    def _check_rights(
        content_rights: ContentRightsService,
        connection: sqlite3.Connection,
        *,
        use: RightsUse,
        principal: str,
    ) -> str:
        decision = content_rights._evaluate_rights_in_transaction(
            connection,
            use=use,
            decision_id=str(uuid4()),
            principal=principal,
        )
        if (
            decision.copyright_state != RightsState.CLEARED
            or decision.platform_rights_state != RightsState.CLEARED
        ):
            raise PermissionError("Current rights do not authorize clip creation.")
        return decision.decision_id

    def _append_event(
        self,
        connection: sqlite3.Connection,
        *,
        render_id: str,
        from_state: ProcessingJobState | None,
        to_state: ProcessingJobState,
        actor: str,
        reason: str,
        now: str,
    ) -> None:
        connection.execute(
            """
            INSERT INTO content_clip_render_events (
                render_id, from_state, to_state, actor, reason, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                render_id,
                None if from_state is None else from_state.value,
                to_state.value,
                actor,
                reason,
                now,
            ),
        )

    def render_edit_project(
        self,
        project_id: str,
        *,
        rights_uses: dict[str, RightsUse],
        vertical: bool,
        context: AuthorizationContext,
        command_id: str,
    ) -> dict[str, object]:
        action = "content_render_edit_project"
        payload = {
            "project_id": project_id,
            "asset_ids": sorted(rights_uses),
            "vertical": vertical,
        }
        result: dict[str, object] | None = None

        def operation(connection: sqlite3.Connection):
            nonlocal result
            project = self.store.get_edit_project(project_id, context.principal)
            if project is None:
                raise PermissionError("The project is outside the authorized account.")
            segments = self.store.get_edit_segments(project_id, context.principal)
            if len(segments) < 2:
                raise ValueError("A CRE project timeline must contain at least 2 segments.")
            source_paths: dict[str, Path] = {}
            staged_sources: list[Path] = []
            try:
                for asset_id in project.source_asset_ids:
                    asset = self.content_rights._asset_in_transaction(connection, asset_id)
                    use = rights_uses.get(asset_id)
                    if (
                        asset is None
                        or asset.owner_id != context.principal
                        or asset.storage_key is None
                        or use is None
                        or use.expected_asset_sha256 != asset.content_sha256
                    ):
                        raise PermissionError("A project source is unavailable or outside the authorized account.")
                    decision = self.content_rights._evaluate_rights_in_transaction(
                        connection,
                        use=use,
                        decision_id=str(uuid4()),
                        principal=context.principal,
                    )
                    if (
                        decision.copyright_state != RightsState.CLEARED
                        or decision.platform_rights_state != RightsState.CLEARED
                    ):
                        raise PermissionError("Current rights do not authorize rendering this project source.")
                    content = self.content_storage.read_verified_input(
                        asset.storage_key,
                        asset.content_sha256,
                    )
                    staged = self.content_storage.stage_clip_source(asset_id, content)
                    staged_sources.append(staged)
                    source_paths[asset_id] = staged

                render_id = str(uuid4())
                staged_output = self.content_storage.create_clip_staging_path(render_id)
                try:
                    _, output_info = self.renderer.render_multi_source_timeline(
                        source_paths,
                        segments=segments,
                        staged_output=staged_output,
                        vertical=vertical,
                    )
                    output_key, output_sha256, output_size = self.content_storage.put_clip_output(
                        render_id,
                        staged_output,
                    )
                finally:
                    if staged_output.exists():
                        self.content_storage.delete_clip_staging(staged_output)
                result = {
                    "renderId": render_id,
                    "projectId": project_id,
                    "artifactId": output_key,
                    "sha256": output_sha256,
                    "sizeBytes": output_size,
                    "durationSeconds": output_info.duration_seconds,
                    "width": output_info.video_width,
                    "height": output_info.video_height,
                    "mimeType": "video/mp4",
                    "sourceAssetIds": list(project.source_asset_ids),
                }
            finally:
                for staged in staged_sources:
                    if staged.exists():
                        self.content_storage.delete_clip_staging(staged)

        self.content_rights._run(
            context,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=project_id,
            operation=operation,
        )
        if result is None:
            raise RuntimeError("Project rendering completed without a durable result.")
        return result

    def autoplan_edit_project(
        self,
        project_id: str,
        instruction: str,
        *,
        context: AuthorizationContext,
        command_id: str,
    ) -> dict[str, object]:
        """Build a private owner-review timeline from real source media metadata."""
        action = "content_autoplan_edit_project"
        payload = {"project_id": project_id, "instruction_length": len(instruction)}
        result: dict[str, object] | None = None

        def operation(connection: sqlite3.Connection):
            nonlocal result
            project = self.store.get_edit_project(project_id, context.principal)
            if project is None:
                raise PermissionError("The project is outside the authorized account.")
            if len(project.source_asset_ids) < 1:
                raise ValueError("At least one registered source is required.")
            source_paths: dict[str, Path] = {}
            staged_sources: list[Path] = []
            info_by_asset: dict[str, MediaInfo] = {}
            scene_changes_by_asset: dict[str, tuple[float, ...]] = {}
            try:
                for asset_id in project.source_asset_ids:
                    asset = self.content_rights._asset_in_transaction(connection, asset_id)
                    if asset is None or asset.owner_id != context.principal or asset.storage_key is None:
                        raise PermissionError("A project source is unavailable or outside the authorized account.")
                    content = self.content_storage.read_verified_input(asset.storage_key, asset.content_sha256)
                    staged = self.content_storage.stage_clip_source(asset_id, content)
                    staged_sources.append(staged)
                    source_paths[asset_id] = staged
                    info_by_asset[asset_id] = self.renderer.inspect_video_source(staged)
                for asset_id, source_path in source_paths.items():
                    observed = self.renderer.detect_scene_changes(source_path)
                    scene_changes_by_asset[asset_id] = tuple(
                        sorted({round(cut, 6) for cut in observed if 0 < cut < info_by_asset[asset_id].duration_seconds})
                    )

                normalized = instruction.lower()
                pace_seconds = 3.5 if any(word in normalized for word in ("slow", "cinematic", "lingering")) else 2.5
                if any(word in normalized for word in ("fast", "quick", "energetic", "punchy")):
                    pace_seconds = 1.5
                target_seconds = pace_seconds * (6 if len(project.source_asset_ids) == 1 else 8)
                match = re.search(r"(\d+(?:\.\d+)?)\s*(?:second|sec|s)\b", normalized)
                if match:
                    try:
                        target_seconds = min(60.0, max(6.0, float(match.group(1))))
                    except ValueError:
                        pass
                segment_count = min(100, max(2, round(target_seconds / pace_seconds)))
                segment_count = segment_count if segment_count % len(project.source_asset_ids) == 0 else segment_count + (len(project.source_asset_ids) - segment_count % len(project.source_asset_ids))
                segment_count = min(100, segment_count)
                segments_per_asset = max(1, segment_count // len(project.source_asset_ids))
                segment_length = min(pace_seconds, target_seconds / max(1, segment_count))
                segments: list[ContentEditSegment] = []
                plan_segments: list[ContentEditPlanSegment] = []
                selected_intervals_by_asset = {
                    asset_id: _select_scene_diverse_intervals(
                        info_by_asset[asset_id].duration_seconds,
                        scene_changes_by_asset[asset_id],
                        segment_length,
                        segments_per_asset,
                    )
                    for asset_id in project.source_asset_ids
                }
                order = 1
                for cycle in range(segments_per_asset):
                    for asset_id in project.source_asset_ids:
                        if len(segments) >= segment_count:
                            break
                        start, end, selection_method, selected_cut = (
                            selected_intervals_by_asset[asset_id][cycle]
                        )
                        segments.append(ContentEditSegment(
                            source_asset_id=asset_id,
                            source_in_seconds=start,
                            source_out_seconds=end,
                            timeline_order=order,
                        ))
                        plan_segments.append(
                            ContentEditPlanSegment(
                                source_asset_id=asset_id,
                                source_in_seconds=start,
                                source_out_seconds=end,
                                timeline_order=order,
                                selection_method=selection_method,
                                selected_scene_cut_seconds=selected_cut,
                                transition_before="start" if order == 1 else "hard_cut",
                            )
                        )
                        order += 1
                previous_plan = self.store.get_latest_edit_plan(
                    project_id,
                    context.principal,
                )
                plan = ContentCreativeEditPlanV2(
                    project_id=project_id,
                    revision=1 if previous_plan is None else previous_plan.revision + 1,
                    creative_direction=instruction,
                    sources=tuple(
                        ContentEditPlanSource(
                            source_asset_id=asset_id,
                            content_sha256=self.content_rights._asset_in_transaction(
                                connection,
                                asset_id,
                            ).content_sha256,
                            duration_seconds=info_by_asset[asset_id].duration_seconds,
                            scene_boundary_seconds=scene_changes_by_asset[asset_id],
                        )
                        for asset_id in project.source_asset_ids
                    ),
                    segments=tuple(
                        {
                            **segment.model_dump(mode="python"),
                            "audio": (
                                ContentEditPlanAudio(
                                    mode="source_audio",
                                    source_asset_id=segment.source_asset_id,
                                    source_in_seconds=segment.source_in_seconds,
                                    source_out_seconds=segment.source_out_seconds,
                                    gain_db=0.0,
                                    timing_policy="trim_pad_to_video_segment",
                                )
                                if info_by_asset[
                                    segment.source_asset_id
                                ].has_audio
                                else ContentEditPlanAudio(
                                    mode="silence",
                                    source_asset_id=None,
                                    source_in_seconds=None,
                                    source_out_seconds=None,
                                    gain_db=0.0,
                                    timing_policy="trim_pad_to_video_segment",
                                )
                            ),
                        }
                        for segment in plan_segments
                    ),
                )
                saved = self.store.save_edit_plan_in_transaction(
                    connection,
                    context.principal,
                    plan,
                )
                plan_digest = hashlib.sha256(
                    self.store._canonical(plan).encode("utf-8")
                ).hexdigest()
                result = {
                    "projectId": project_id,
                    "segmentCount": len(saved),
                    "planRevision": plan.revision,
                    "planSha256": plan_digest,
                    "sourceAssetIds": list(project.source_asset_ids),
                    "instructionApplied": bool(instruction.strip()),
                    "segments": [segment.model_dump(mode="json") for segment in saved],
                    "analysis": (
                        "Timeline planning used verified source durations and "
                        "non-semantic FFmpeg scene boundaries. Distinct usable "
                        "scene intervals were selected before repetition; "
                        "insufficient scene duration is recorded as an explicit "
                        "fallback. Semantic vision and speech understanding were "
                        "not inferred."
                    ),
                }
                return project_id, "planned", {
                    "segment_count": len(saved),
                    "source_asset_ids": list(project.source_asset_ids),
                    "plan_revision": plan.revision,
                    "plan_sha256": plan_digest,
                }
            finally:
                for staged in staged_sources:
                    if staged.exists():
                        self.content_storage.delete_clip_staging(staged)

        self.content_rights._run(
            context,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=project_id,
            operation=operation,
        )
        if result is None:
            raise RuntimeError("Autonomous edit planning completed without a durable result.")
        return result

    @staticmethod
    def _validate_private_render_operation_id(operation_id: str) -> str:
        if not isinstance(operation_id, str):
            raise ValueError("A UUIDv4 private render operation ID is required.")
        try:
            parsed = UUID(operation_id)
        except (ValueError, AttributeError) as exc:
            raise ValueError(
                "A UUIDv4 private render operation ID is required."
            ) from exc
        if parsed.version != 4 or str(parsed) != operation_id.lower():
            raise ValueError("A canonical UUIDv4 private render operation ID is required.")
        return str(parsed)

    @staticmethod
    def _private_render_operation_summary(
        operation: ContentPrivateRenderOperation,
    ) -> dict[str, object]:
        result = (
            None
            if operation.result_json is None
            else json.loads(operation.result_json)
        )
        return {
            "operationId": operation.operation_id,
            "projectId": operation.project_id,
            "state": operation.state.value,
            "createdAt": operation.created_at.isoformat(),
            "updatedAt": operation.updated_at.isoformat(),
            "completedAt": (
                None
                if operation.completed_at is None
                else operation.completed_at.isoformat()
            ),
            "artifactId": operation.artifact_key,
            "sha256": operation.artifact_sha256,
            "sizeBytes": operation.artifact_byte_count,
            "failureCode": operation.failure_code,
            "failureDetail": operation.failure_detail,
            "result": result,
        }

    def submit_private_project_render_operation(
        self,
        operation_id: str,
        project_id: str,
        *,
        vertical: bool,
        context: AuthorizationContext,
    ) -> dict[str, object]:
        """Create or recover one owner-scoped render operation and claim it once."""
        operation_id = self._validate_private_render_operation_id(operation_id)
        action = "content_submit_private_project_render"
        payload = {
            "operation_id": operation_id,
            "project_id": project_id,
            "vertical": vertical,
        }
        request_fingerprint = self.store._fingerprint(action, payload)

        def create(connection: sqlite3.Connection):
            project = self.store.get_edit_project(project_id, context.principal)
            if project is None:
                raise PermissionError("The project is outside the authorized account.")
            segments = self.store.get_edit_segments(project_id, context.principal)
            if len(segments) < 2:
                raise ValueError("A CRE project timeline must contain at least 2 segments.")
            plan = self.store.get_latest_edit_plan(project_id, context.principal)
            source_records: list[dict[str, str]] = []
            sources_by_id: dict[str, dict[str, str]] = {}
            for asset_id in project.source_asset_ids:
                asset = self.content_rights._asset_in_transaction(connection, asset_id)
                if (
                    asset is None
                    or asset.owner_id != context.principal
                    or asset.state != AssetState.ACTIVE
                    or asset.storage_key is None
                ):
                    raise PermissionError(
                        "A project source is unavailable or outside the authorized account."
                    )
                record = {
                    "assetId": asset.asset_id,
                    "ownerId": asset.owner_id,
                    "storageKey": asset.storage_key,
                    "sha256": asset.content_sha256,
                }
                source_records.append(record)
                sources_by_id[asset_id] = record
            plan_json = None if plan is None else self.store._canonical(plan)
            if isinstance(plan, ContentCreativeEditPlanV2):
                for segment in plan.segments:
                    audio = segment.audio
                    if audio.mode == "source_audio":
                        pinned_audio = sources_by_id.get(audio.source_asset_id or "")
                        audio_plan_source = next(
                            (
                                source
                                for source in plan.sources
                                if source.source_asset_id == audio.source_asset_id
                            ),
                            None,
                        )
                        if (
                            pinned_audio is None
                            or audio_plan_source is None
                            or pinned_audio["sha256"]
                            != audio_plan_source.content_sha256
                        ):
                            raise ValueError(
                                "Structured audio source identity or hash is stale."
                            )
            input_snapshot = {
                "projectId": project_id,
                "ownerId": context.principal,
                "vertical": vertical,
                "sourceAssetIds": list(project.source_asset_ids),
                "sources": source_records,
                "segments": [
                    segment.model_dump(mode="json") for segment in segments
                ],
                "planJson": plan_json,
                "planSha256": (
                    None
                    if plan_json is None
                    else hashlib.sha256(plan_json.encode("utf-8")).hexdigest()
                ),
            }
            self.store.create_private_render_operation_in_transaction(
                connection,
                operation_id=operation_id,
                owner_id=context.principal,
                project_id=project_id,
                request_fingerprint=request_fingerprint,
                input_snapshot_json=self.store._canonical(input_snapshot),
            )
            return operation_id, "queued", {
                "operation_id": operation_id,
                "project_id": project_id,
                "request_fingerprint": request_fingerprint,
            }

        self.content_rights._run(
            context,
            action=action,
            payload=payload,
            command_id=operation_id,
            subject_id=project_id,
            operation=create,
        )
        operation = self.store.get_private_render_operation(
            operation_id,
            context.principal,
        )
        if operation is None:
            raise RuntimeError("Private render operation was not durably created.")
        if operation.request_fingerprint != request_fingerprint:
            raise RuntimeError("Private render operation request identity is inconsistent.")
        if operation.state == PrivateRenderOperationState.QUEUED:
            if self.store.claim_private_render_operation(
                operation_id,
                context.principal,
            ):
                operation = self._execute_private_render_operation(operation)
            else:
                operation = self.store.get_private_render_operation(
                    operation_id,
                    context.principal,
                )
                if operation is None:
                    raise RuntimeError("Private render operation disappeared after claim.")
        return self._private_render_operation_summary(operation)

    def _execute_private_render_operation(
        self,
        operation: ContentPrivateRenderOperation,
    ) -> ContentPrivateRenderOperation:
        snapshot = json.loads(operation.input_snapshot_json)
        staged_sources: list[Path] = []
        staged_output: Path | None = None
        output_key: str | None = None
        try:
            source_paths: dict[str, Path] = {}
            for source in snapshot["sources"]:
                if source["ownerId"] != operation.owner_id:
                    raise PermissionError("Pinned source owner does not match the operation.")
                content = self.content_storage.read_verified_input(
                    source["storageKey"],
                    source["sha256"],
                )
                staged = self.content_storage.stage_clip_source(
                    source["assetId"],
                    content,
                )
                staged_sources.append(staged)
                source_paths[source["assetId"]] = staged

            segments = tuple(
                ContentEditSegment.model_validate(segment)
                for segment in snapshot["segments"]
            )
            plan_json = snapshot["planJson"]
            plan: ContentCreativeEditPlanRecord | None = None
            if plan_json is not None:
                plan_payload = json.loads(plan_json)
                if plan_payload.get("schema_version") == "cre-creative-plan-v1":
                    from ultron.domain.content import ContentCreativeEditPlan

                    plan = ContentCreativeEditPlan.model_validate(plan_payload)
                elif plan_payload.get("schema_version") == "cre-creative-plan-v2":
                    plan = ContentCreativeEditPlanV2.model_validate(plan_payload)
                else:
                    raise ValueError("Pinned creative plan version is unsupported.")
                if (
                    plan.project_id != operation.project_id
                    or hashlib.sha256(plan_json.encode("utf-8")).hexdigest()
                    != snapshot["planSha256"]
                ):
                    raise ValueError("Pinned creative plan identity or digest is invalid.")

            staged_output = self.content_storage.create_clip_staging_path(
                operation.operation_id
            )
            if plan is None:
                _, output_info = self.renderer.render_multi_source_timeline(
                    source_paths,
                    segments=tuple(
                        SourceEditSegment(
                            source_in_seconds=segment.source_in_seconds,
                            source_out_seconds=segment.source_out_seconds,
                            source_asset_id=segment.source_asset_id,
                        )
                        for segment in segments
                    ),
                    staged_output=staged_output,
                    vertical=snapshot["vertical"],
                )
            else:
                if tuple(source.source_asset_id for source in plan.sources) != tuple(
                    snapshot["sourceAssetIds"]
                ):
                    raise ValueError("Pinned plan sources do not match the project.")
                _, output_info = self.renderer.render_creative_edit_plan(
                    source_paths,
                    plan=plan,
                    staged_output=staged_output,
                    vertical=snapshot["vertical"],
                )
            output_key, output_sha256, output_size = (
                self.content_storage.put_clip_output(
                    operation.operation_id,
                    staged_output,
                )
            )
            if (
                output_size <= 0
                or output_size
                > self.content_storage.MAX_CLIP_OUTPUT_BYTES
            ):
                raise ValueError("Rendered artifact exceeds the configured size limit.")
            result: dict[str, object] = {
                "operationId": operation.operation_id,
                "renderId": operation.operation_id,
                "projectId": operation.project_id,
                "artifactId": output_key,
                "sha256": output_sha256,
                "sizeBytes": output_size,
                "durationSeconds": output_info.duration_seconds,
                "width": output_info.video_width,
                "height": output_info.video_height,
                "mimeType": "video/mp4",
                "sourceAssetIds": list(snapshot["sourceAssetIds"]),
                "privateReviewOnly": True,
                "planRevision": None if plan is None else plan.revision,
                "planSha256": snapshot["planSha256"],
                "planMode": "legacy" if plan is None else "structured",
                "planVersion": None if plan is None else plan.schema_version,
                "orientation": "vertical" if snapshot["vertical"] else "landscape",
                "rendererSettings": {
                    "videoCodec": "h264",
                    "frameRateFps": 30,
                    "maxWidth": 1280,
                    "maxHeight": 1280,
                    "audioPolicy": (
                        "preserve_segment_source_audio"
                        if plan is None
                        else plan.audio_policy
                    ),
                    "transitions": "hard_cut",
                    "audioSampleRateHz": 48_000,
                    **(
                        {
                            "peakProtectionPolicy": plan.peak_protection_policy,
                        }
                        if isinstance(plan, ContentCreativeEditPlanV2)
                        else {}
                    ),
                },
            }
            if isinstance(plan, ContentCreativeEditPlanV2):
                result["audioTimeline"] = [
                    {
                        "timelineOrder": segment.timeline_order,
                        "mode": segment.audio.mode,
                        "sourceAssetId": segment.audio.source_asset_id,
                        "sourceInSeconds": segment.audio.source_in_seconds,
                        "sourceOutSeconds": segment.audio.source_out_seconds,
                        "gainDb": segment.audio.gain_db,
                        "timingPolicy": segment.audio.timing_policy,
                    }
                    for segment in plan.segments
                ]
                result["audioValidation"] = {
                    "outputPresent": output_info.has_audio,
                    "sampleRateHz": output_info.audio_sample_rate,
                    "channels": output_info.audio_channels,
                    "durationSeconds": output_info.audio_duration_seconds,
                    "decodedPeakDbfs": output_info.audio_peak_dbfs,
                    "peakLimitDbfs": self.renderer.V2_AUDIO_PEAK_TARGET_DBFS,
                    "peakValidationToleranceDb": (
                        self.renderer.V2_AUDIO_PEAK_VALIDATION_TOLERANCE_DB
                    ),
                    "status": "PASSED",
                }
            self.store.complete_private_render_operation(
                operation.operation_id,
                operation.owner_id,
                artifact_key=output_key,
                artifact_sha256=output_sha256,
                artifact_byte_count=output_size,
                result_json=self.store._canonical(result),
            )
        except ClipRenderTimeout:
            self.store.fail_private_render_operation(
                operation.operation_id,
                operation.owner_id,
                reconciliation_required=True,
                failure_code="RENDER_OUTCOME_UNKNOWN",
                failure_detail=(
                    "Renderer timed out; inspect the operation and stored output "
                    "before taking further action."
                ),
            )
        except Exception as exc:
            try:
                self.store.fail_private_render_operation(
                    operation.operation_id,
                    operation.owner_id,
                    reconciliation_required=output_key is not None,
                    failure_code=(
                        "PERSISTENCE_OUTCOME_UNKNOWN"
                        if output_key is not None
                        else "RENDER_FAILED"
                    ),
                    failure_detail=(
                        "Rendered output exists but final persistence requires "
                        "reconciliation."
                        if output_key is not None
                        else f"Render failed ({type(exc).__name__})."
                    ),
                )
            except Exception:
                raise RuntimeError(
                    "Private render failed and its operation state could not be persisted."
                ) from exc
        except BaseException:
            self.store.fail_private_render_operation(
                operation.operation_id,
                operation.owner_id,
                reconciliation_required=True,
                failure_code="PROCESS_INTERRUPTED",
                failure_detail=(
                    "Rendering was interrupted; the outcome requires reconciliation."
                ),
            )
            raise
        finally:
            cleanup_errors: list[Exception] = []
            for path in (*staged_sources, staged_output):
                if path is not None and path.exists():
                    try:
                        self.content_storage.delete_clip_staging(path)
                    except Exception as cleanup_error:
                        cleanup_errors.append(cleanup_error)
            if cleanup_errors:
                raise RuntimeError(
                    "Managed render staging files could not be removed."
                ) from cleanup_errors[0]
        current = self.store.get_private_render_operation(
            operation.operation_id,
            operation.owner_id,
        )
        if current is None:
            raise RuntimeError("Private render operation disappeared after execution.")
        return current

    def get_private_render_operation(
        self,
        operation_id: str,
        *,
        context: AuthorizationContext,
    ) -> dict[str, object]:
        operation_id = self._validate_private_render_operation_id(operation_id)
        action = "content_read_private_project_render"
        payload = {"operation_id": operation_id}

        def read(connection: sqlite3.Connection):
            self.store._require_private_render_operation_schema(connection)
            row = connection.execute(
                """
                SELECT operation_id FROM content_private_render_operations
                WHERE operation_id = ? AND owner_id = ?
                """,
                (operation_id, context.principal),
            ).fetchone()
            if row is None:
                raise PermissionError("Private render operation is unavailable.")
            return operation_id, "private_render_operation_read", {}

        self.content_rights._run(
            context,
            action=action,
            payload=payload,
            command_id=str(uuid4()),
            subject_id=operation_id,
            operation=read,
        )
        operation = self.store.get_private_render_operation(
            operation_id,
            context.principal,
        )
        if operation is None:
            raise PermissionError("Private render operation is unavailable.")
        return self._private_render_operation_summary(operation)

    def get_private_render_artifact(
        self,
        operation_id: str,
        *,
        context: AuthorizationContext,
    ) -> dict[str, object]:
        operation_id = self._validate_private_render_operation_id(operation_id)
        action = "content_read_private_project_render_artifact"
        payload = {"operation_id": operation_id}

        def read(connection: sqlite3.Connection):
            self.store._require_private_render_operation_schema(connection)
            row = connection.execute(
                """
                SELECT operation_id, artifact_key, artifact_sha256,
                    artifact_byte_count, result_json
                FROM content_private_render_operations
                WHERE operation_id = ? AND owner_id = ? AND state = ?
                """,
                (
                    operation_id,
                    context.principal,
                    PrivateRenderOperationState.COMPLETED.value,
                ),
            ).fetchone()
            if row is None:
                raise PermissionError("Private render artifact is unavailable.")
            return operation_id, "private_render_artifact_read", {
                "artifact_sha256": row["artifact_sha256"],
                "artifact_byte_count": row["artifact_byte_count"],
            }

        self.content_rights._run(
            context,
            action=action,
            payload=payload,
            command_id=str(uuid4()),
            subject_id=operation_id,
            operation=read,
        )
        operation = self.store.get_private_render_operation(
            operation_id,
            context.principal,
        )
        if (
            operation is None
            or operation.state != PrivateRenderOperationState.COMPLETED
            or operation.artifact_key is None
            or operation.artifact_sha256 is None
            or operation.artifact_byte_count is None
            or operation.result_json is None
        ):
            raise PermissionError("Private render artifact is unavailable.")
        content = self.content_storage.read_clip_output(
            operation.artifact_key,
            operation.artifact_sha256,
        )
        if (
            len(content) != operation.artifact_byte_count
            or len(content) > self.content_storage.MAX_CLIP_OUTPUT_BYTES
            or hashlib.sha256(content).hexdigest() != operation.artifact_sha256
        ):
            raise ValueError("Stored private render artifact failed integrity verification.")
        result = json.loads(operation.result_json)
        result["base64"] = base64.b64encode(content).decode("ascii")
        return result

    def render_private_edit_project(
        self,
        project_id: str,
        *,
        vertical: bool,
        context: AuthorizationContext,
        command_id: str,
    ) -> dict[str, object]:
        """Render an owner-scoped private review copy without publishing rights checks."""
        action = "content_render_private_edit_project"
        payload = {"project_id": project_id, "vertical": vertical}
        result: dict[str, object] | None = None

        def operation(connection: sqlite3.Connection):
            nonlocal result
            project = self.store.get_edit_project(project_id, context.principal)
            if project is None:
                raise PermissionError("The project is outside the authorized account.")
            segments = self.store.get_edit_segments(project_id, context.principal)
            if len(segments) < 2:
                raise ValueError("A CRE project timeline must contain at least 2 segments.")
            plan: ContentCreativeEditPlanRecord | None = (
                self.store.get_latest_edit_plan(project_id, context.principal)
            )
            plan_digest = (
                None
                if plan is None
                else hashlib.sha256(
                    self.store._canonical(plan).encode("utf-8")
                ).hexdigest()
            )
            source_paths: dict[str, Path] = {}
            staged_sources: list[Path] = []
            try:
                for asset_id in project.source_asset_ids:
                    asset = self.content_rights._asset_in_transaction(connection, asset_id)
                    if asset is None or asset.owner_id != context.principal or asset.storage_key is None:
                        raise PermissionError("A project source is unavailable or outside the authorized account.")
                    if plan is not None and not any(
                        source.source_asset_id == asset_id
                        and source.content_sha256 == asset.content_sha256
                        for source in plan.sources
                    ):
                        raise ValueError("Structured plan source identity or hash is stale.")
                    content = self.content_storage.read_verified_input(asset.storage_key, asset.content_sha256)
                    staged = self.content_storage.stage_clip_source(asset_id, content)
                    staged_sources.append(staged)
                    source_paths[asset_id] = staged

                render_id = str(uuid4())
                staged_output = self.content_storage.create_clip_staging_path(render_id)
                try:
                    if plan is None:
                        _, output_info = self.renderer.render_multi_source_timeline(
                            source_paths,
                            segments=tuple(
                                SourceEditSegment(
                                    source_in_seconds=segment.source_in_seconds,
                                    source_out_seconds=segment.source_out_seconds,
                                    source_asset_id=segment.source_asset_id,
                                )
                                for segment in segments
                            ),
                            staged_output=staged_output,
                            vertical=vertical,
                        )
                    else:
                        if tuple(source.source_asset_id for source in plan.sources) != project.source_asset_ids:
                            raise ValueError("Structured plan sources do not match the project.")
                        if isinstance(plan, ContentCreativeEditPlanV2):
                            for segment in plan.segments:
                                if segment.audio.mode == "source_audio":
                                    audio_source = next(
                                        (
                                            source
                                            for source in plan.sources
                                            if source.source_asset_id
                                            == segment.audio.source_asset_id
                                        ),
                                        None,
                                    )
                                    registered_audio_source = self.content_rights._asset_in_transaction(
                                        connection,
                                        segment.audio.source_asset_id or "",
                                    )
                                    if (
                                        audio_source is None
                                        or registered_audio_source is None
                                        or registered_audio_source.owner_id
                                        != context.principal
                                        or registered_audio_source.storage_key is None
                                        or registered_audio_source.content_sha256
                                        != audio_source.content_sha256
                                    ):
                                        raise ValueError(
                                            "Structured audio source identity or hash is stale."
                                        )
                        _, output_info = self.renderer.render_creative_edit_plan(
                            source_paths,
                            plan=plan,
                            staged_output=staged_output,
                            vertical=vertical,
                        )
                    output_key, output_sha256, output_size = self.content_storage.put_clip_output(render_id, staged_output)
                finally:
                    if staged_output.exists():
                        self.content_storage.delete_clip_staging(staged_output)
                result = {
                    "renderId": render_id,
                    "projectId": project_id,
                    "artifactId": output_key,
                    "sha256": output_sha256,
                    "sizeBytes": output_size,
                    "durationSeconds": output_info.duration_seconds,
                    "width": output_info.video_width,
                    "height": output_info.video_height,
                    "mimeType": "video/mp4",
                    "sourceAssetIds": list(project.source_asset_ids),
                    "privateReviewOnly": True,
                    "planRevision": None if plan is None else plan.revision,
                    "planSha256": plan_digest,
                    "planMode": "legacy" if plan is None else "structured",
                    "planVersion": None if plan is None else plan.schema_version,
                    "orientation": "vertical" if vertical else "landscape",
                    "rendererSettings": {
                        "videoCodec": "h264",
                        "frameRateFps": 30,
                        "maxWidth": 1280,
                        "maxHeight": 1280,
                        "audioPolicy": (
                            "preserve_segment_source_audio"
                            if plan is None
                            else plan.audio_policy
                        ),
                        "transitions": "hard_cut",
                        "audioSampleRateHz": 48_000,
                        **(
                            {
                                "peakProtectionPolicy": (
                                    plan.peak_protection_policy
                                )
                            }
                            if isinstance(plan, ContentCreativeEditPlanV2)
                            else {}
                        ),
                    },
                }
                if isinstance(plan, ContentCreativeEditPlanV2):
                    result["audioTimeline"] = [
                        {
                            "timelineOrder": segment.timeline_order,
                            "mode": segment.audio.mode,
                            "sourceAssetId": segment.audio.source_asset_id,
                            "sourceInSeconds": segment.audio.source_in_seconds,
                            "sourceOutSeconds": segment.audio.source_out_seconds,
                            "gainDb": segment.audio.gain_db,
                            "timingPolicy": segment.audio.timing_policy,
                        }
                        for segment in plan.segments
                    ]
                    result["audioValidation"] = {
                        "outputPresent": output_info.has_audio,
                        "sampleRateHz": output_info.audio_sample_rate,
                        "channels": output_info.audio_channels,
                        "durationSeconds": output_info.audio_duration_seconds,
                        "decodedPeakDbfs": output_info.audio_peak_dbfs,
                        "peakLimitDbfs": self.renderer.V2_AUDIO_PEAK_TARGET_DBFS,
                        "peakValidationToleranceDb": (
                            self.renderer.V2_AUDIO_PEAK_VALIDATION_TOLERANCE_DB
                        ),
                        "status": "PASSED",
                    }
                return project_id, "rendered", {
                    "render_id": render_id,
                    "artifact_id": output_key,
                    "sha256": output_sha256,
                    "size_bytes": output_size,
                    "duration_seconds": output_info.duration_seconds,
                    "width": output_info.video_width,
                    "height": output_info.video_height,
                    "mime_type": "video/mp4",
                    "plan_revision": None if plan is None else plan.revision,
                    "plan_sha256": plan_digest,
                    "plan_version": None if plan is None else plan.schema_version,
                    "plan_mode": "legacy" if plan is None else "structured",
                    "orientation": "vertical" if vertical else "landscape",
                    "renderer_settings": result["rendererSettings"],
                    **(
                        {
                            "audio_timeline": result["audioTimeline"],
                            "audio_validation": result["audioValidation"],
                        }
                        if isinstance(plan, ContentCreativeEditPlanV2)
                        else {}
                    ),
                }
            finally:
                for staged in staged_sources:
                    if staged.exists():
                        self.content_storage.delete_clip_staging(staged)

        self.content_rights._run(
            context,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=project_id,
            operation=operation,
        )
        if result is None:
            raise RuntimeError("Private project rendering completed without a durable result.")
        return result

    def create_clip_render(
        self,
        candidate_id: str,
        use: RightsUse,
        *,
        context: AuthorizationContext,
        command_id: str,
    ) -> ContentClipRender:
        action = "content_create_clip_render"
        payload = {
            "candidate_id": candidate_id,
            "rights_use": use.model_dump(mode="json"),
        }
        render_id = str(uuid4())

        def operation(connection: sqlite3.Connection):
            candidate, transcript, target, _ = self._load_candidate_context(
                connection,
                candidate_id,
                context.principal,
            )
            asset = self.content_rights._asset_in_transaction(
                connection,
                candidate.asset_id,
            )
            expected_platform = self._platform_for_target(target)
            if (
                asset is None
                or asset.state != AssetState.ACTIVE
                or asset.owner_id != context.principal
                or asset.storage_key is None
                or candidate.asset_id != asset.asset_id
                or candidate.asset_sha256 != asset.content_sha256
                or expected_platform is None
                or use.asset_id != asset.asset_id
                or use.expected_asset_sha256 != asset.content_sha256
                or use.platform != expected_platform
                or use.actions != frozenset({RightsAction.CREATE_DERIVATIVE})
                or use.commercial_intent
                or use.monetization_intent
                or use.duration_seconds != math.ceil(candidate.duration_seconds)
                or candidate.duration_seconds > self.MAX_CLIP_DURATION_SECONDS
                or not math.isfinite(candidate.start_seconds)
                or not math.isfinite(candidate.end_seconds)
                or candidate.start_seconds < 0
                or candidate.end_seconds <= candidate.start_seconds
                or candidate.end_seconds > transcript.duration_seconds
            ):
                raise ValueError("Clip request does not match supported candidate scope.")

            use_json = self.content_rights._json(use.model_dump(mode="json"))
            existing = connection.execute(
                """
                SELECT render_id FROM content_clip_renders
                WHERE candidate_id = ? AND rights_use_json = ?
                """,
                (candidate_id, use_json),
            ).fetchone()
            if existing is not None:
                return existing["render_id"], "idempotent_render", {
                    "candidate_id": candidate_id,
                    "rights_use": use.model_dump(mode="json"),
                }

            decision_id = self._check_rights(
                self.content_rights,
                connection,
                use=use,
                principal=context.principal,
            )
            now = datetime.now(UTC).isoformat()
            connection.execute(
                """
                INSERT INTO content_clip_renders (
                    render_id, owner_id, command_id, candidate_id, transcript_id,
                    asset_id, asset_sha256, target_context, rights_use_json,
                    start_seconds, end_seconds, state, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    render_id,
                    context.principal,
                    command_id,
                    candidate.candidate_id,
                    transcript.transcript_id,
                    asset.asset_id,
                    asset.content_sha256,
                    target.value,
                    use_json,
                    candidate.start_seconds,
                    candidate.end_seconds,
                    ProcessingJobState.QUEUED.value,
                    now,
                    now,
                ),
            )
            self._append_event(
                connection,
                render_id=render_id,
                from_state=None,
                to_state=ProcessingJobState.QUEUED,
                actor=context.principal,
                reason="authorized_clip_render_request",
                now=now,
            )
            return render_id, "queued", {
                "candidate_id": candidate.candidate_id,
                "transcript_id": transcript.transcript_id,
                "asset_id": asset.asset_id,
                "asset_sha256": asset.content_sha256,
                "rights_decision_id": decision_id,
                "source_interval": [candidate.start_seconds, candidate.end_seconds],
            }

        self.content_rights._run(
            context,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=render_id,
            operation=operation,
        )
        result = self.store.get_clip_render(render_id)
        if result is None:
            with self.store._lock:
                command = self.store._connection.execute(
                    """
                    SELECT result_id FROM content_commands
                    WHERE principal = ? AND action = ? AND command_id = ?
                    """,
                    (context.principal, action, command_id),
                ).fetchone()
            result = (
                None
                if command is None
                else self.store.get_clip_render(command["result_id"])
            )
        if result is None:
            raise RuntimeError("Authorized clip render was not durably recorded.")
        return result

    def _finish(
        self,
        render_id: str,
        *,
        state: ProcessingJobState,
        reason: str,
        context: AuthorizationContext,
        media: tuple[MediaInfo, MediaInfo] | None = None,
        output: tuple[str, str, int] | None = None,
        quality: dict[str, object] | None = None,
    ) -> ProcessingJobState:
        now = datetime.now(UTC).isoformat()
        action = f"content_clip_render_{state.value}"
        fingerprint = ContentStore._fingerprint(
            action,
            {"render_id": render_id, "state": state.value, "reason": reason},
        )
        with self.store._transaction() as connection:
            row = connection.execute(
                """
                SELECT owner_id, state FROM content_clip_renders
                WHERE render_id = ?
                """,
                (render_id,),
            ).fetchone()
            if row is None or row["owner_id"] != context.principal:
                raise PermissionError("Clip render is outside the authorized account.")
            current = ProcessingJobState(row["state"])
            if current != ProcessingJobState.RUNNING:
                return current
            fields: list[object] = [
                state.value,
                now,
            ]
            assignments = ["state = ?", "updated_at = ?"]
            if media is not None:
                source, result = media
                assignments.extend(
                    [
                        "source_duration_seconds = ?",
                        "output_duration_seconds = ?",
                        "has_video = ?",
                        "has_audio = ?",
                    ]
                )
                fields.extend(
                    [
                        source.duration_seconds,
                        result.duration_seconds,
                        int(result.has_video),
                        int(result.has_audio),
                    ]
                )
            if output is not None:
                assignments.extend(
                    [
                        "output_key = ?",
                        "output_sha256 = ?",
                        "output_byte_size = ?",
                    ]
                )
                fields.extend(output)
            if quality is not None:
                assignments.extend(
                    [
                        "quality_output_sha256 = ?",
                        "quality_status = ?",
                        "quality_black_frames = ?",
                        "quality_frozen_frames = ?",
                        "quality_black_ranges_json = ?",
                        "quality_frozen_ranges_json = ?",
                        "quality_revision_count = ?",
                    ]
                )
                fields.extend(
                    [
                        output[1] if output is not None else None,
                        quality["status"],
                        quality["blackFrames"],
                        quality["frozenFrames"],
                        json.dumps(quality.get("blackRanges", []), separators=(",", ":")),
                        json.dumps(quality.get("frozenRanges", []), separators=(",", ":")),
                        quality.get("revisionCount", 0),
                    ]
                )
            fields.extend([render_id, ProcessingJobState.RUNNING.value])
            connection.execute(
                f"""
                UPDATE content_clip_renders
                SET {", ".join(assignments)}
                WHERE render_id = ? AND state = ?
                """,
                fields,
            )
            self._append_event(
                connection,
                render_id=render_id,
                from_state=current,
                to_state=state,
                actor=context.principal,
                reason=reason,
                now=now,
            )
            self.store._insert_audit(
                connection,
                context,
                action,
                render_id,
                state.value,
                fingerprint,
                {"reason": reason},
                now,
            )
            return state

    def run_clip_render(
        self,
        render_id: str,
        *,
        context: AuthorizationContext,
        command_id: str,
    ) -> ContentClipRender:
        action = "content_run_clip_render"
        payload = {"render_id": render_id}
        content: bytes | None = None
        captions: tuple[RenderCaption, ...] = ()
        timeline: tuple[SourceEditSegment, ...] = ()
        vertical = False
        did_claim = False
        claim_failure: str | None = None

        def claim(connection: sqlite3.Connection):
            nonlocal content, did_claim, claim_failure, captions, timeline, vertical
            row = connection.execute(
                "SELECT * FROM content_clip_renders WHERE render_id = ?",
                (render_id,),
            ).fetchone()
            if row is None or row["owner_id"] != context.principal:
                raise PermissionError("Clip render is outside the authorized account.")
            current = ProcessingJobState(row["state"])
            if current != ProcessingJobState.QUEUED:
                raise PermissionError("Clip render is not available for dispatch.")
            render = self.store._load_clip_render(row)
            candidate, transcript, target, highlight_config = self._load_candidate_context(
                connection,
                render.candidate_id,
                context.principal,
            )
            if (
                candidate.transcript_id != render.transcript_id
                or candidate.asset_id != render.asset_id
                or candidate.asset_sha256 != render.asset_sha256
                or candidate.start_seconds != render.start_seconds
                or candidate.end_seconds != render.end_seconds
                or target != render.target_context
            ):
                raise RuntimeError("Clip render no longer matches its persisted highlight.")
            selected_segment_ids = set(candidate.transcript_segment_ids)
            captions = tuple(
                RenderCaption(
                    start_seconds=segment.start_seconds,
                    end_seconds=segment.end_seconds,
                    text=segment.text,
                )
                for segment in transcript.segments
                if segment.segment_id in selected_segment_ids
            )
            timeline = self._edit_timeline(candidate, transcript, highlight_config)
            vertical = target in {
                ContentTargetContext.YOUTUBE_SHORTS,
                ContentTargetContext.INSTAGRAM_REELS,
            }
            asset = self.content_rights._asset_in_transaction(
                connection,
                render.asset_id,
            )
            if (
                asset is None
                or asset.state != AssetState.ACTIVE
                or asset.owner_id != context.principal
                or asset.content_sha256 != render.asset_sha256
                or asset.storage_key is None
                or render.rights_use.expected_asset_sha256 != asset.content_sha256
            ):
                raise RuntimeError("Clip source asset linkage is inconsistent.")
            try:
                content = self.content_storage.read_verified_input(
                    asset.storage_key,
                    asset.content_sha256,
                )
            except (OSError, ValueError, TypeError) as exc:
                claim_failure = f"registered_source_invalid:{type(exc).__name__}"
            else:
                try:
                    decision_id = self._check_rights(
                        self.content_rights,
                        connection,
                        use=render.rights_use,
                        principal=context.principal,
                    )
                except PermissionError:
                    claim_failure = "rights_revalidation_denied_before_render"
                else:
                    claim_failure = None

            now = datetime.now(UTC).isoformat()
            if claim_failure is not None:
                connection.execute(
                    """
                    UPDATE content_clip_renders SET state = ?, updated_at = ?
                    WHERE render_id = ? AND state = ?
                    """,
                    (
                        ProcessingJobState.FAILED.value,
                        now,
                        render_id,
                        ProcessingJobState.QUEUED.value,
                    ),
                )
                self._append_event(
                    connection,
                    render_id=render_id,
                    from_state=current,
                    to_state=ProcessingJobState.FAILED,
                    actor=context.principal,
                    reason=claim_failure,
                    now=now,
                )
                did_claim = True
                return render_id, "failed_before_render", {
                    "reason": claim_failure,
                }

            connection.execute(
                """
                UPDATE content_clip_renders SET state = ?, updated_at = ?
                WHERE render_id = ? AND state = ?
                """,
                (
                    ProcessingJobState.RUNNING.value,
                    now,
                    render_id,
                    ProcessingJobState.QUEUED.value,
                ),
            )
            self._append_event(
                connection,
                render_id=render_id,
                from_state=current,
                to_state=ProcessingJobState.RUNNING,
                actor=context.principal,
                reason="rights_revalidated_and_renderer_claimed",
                now=now,
            )
            did_claim = True
            return render_id, "running", {"rights_decision_id": decision_id}

        self.content_rights._run(
            context,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=render_id,
            operation=claim,
        )
        current = self.store.get_clip_render(render_id)
        if current is None:
            raise RuntimeError("Clip render disappeared after worker claim.")
        if not did_claim:
            return current
        if current.state != ProcessingJobState.RUNNING:
            raise PermissionError("Clip render failed its pre-render authorization checks.")
        if content is None:
            raise RuntimeError("Verified clip source was not loaded.")

        staged_path: Path | None = None
        source_staging_path: Path | None = None
        output_key: str | None = None
        output_metadata: tuple[str, str, int] | None = None
        media: tuple[MediaInfo, MediaInfo] | None = None
        quality: dict[str, object] | None = None
        try:
            source_staging_path = self.content_storage.stage_clip_source(
                render_id, content
            )
            staged_path = self.content_storage.create_clip_staging_path(render_id)
            source_info = self.renderer.inspect(source_staging_path)
            if len(timeline) > 1 and source_info.has_video:
                media = self.renderer.render_edit_timeline(
                    source_staging_path,
                    segments=timeline,
                    staged_output=staged_path,
                    vertical=vertical,
                    captions=captions if vertical else (),
                )
            else:
                media = self.renderer.render(
                    source_staging_path,
                    start_seconds=current.start_seconds,
                    end_seconds=current.end_seconds,
                    staged_output=staged_path,
                    vertical=vertical,
                    captions=captions if vertical else (),
                )
            if media[0].duration_seconds > self.MAX_SOURCE_DURATION_SECONDS:
                raise ValueError("Source media exceeds the configured duration limit.")
            quality = self.renderer.inspect_render_quality(staged_path)
            if quality["status"] != "PASSED":
                raise ValueError("Rendered clip has unresolved technical defects.")
            output_metadata = self.content_storage.put_clip_output(
                render_id,
                staged_path,
            )
            output_key = output_metadata[0]
            verified_output = self.content_storage.read_clip_output(
                output_key,
                output_metadata[1],
            )
            if len(verified_output) != output_metadata[2]:
                raise ValueError("Stored clip byte count failed verification.")
        except ClipRenderTimeout:
            self._finish(
                render_id,
                state=ProcessingJobState.UNCERTAIN,
                reason="renderer_timeout_outcome_unknown",
                context=context,
            )
            raise
        except Exception as primary:
            try:
                self._finish(
                    render_id,
                    state=ProcessingJobState.FAILED,
                    reason=f"clip_render_or_validation_failed:{type(primary).__name__}",
                    context=context,
                )
            except Exception as persistence:
                raise RuntimeError(
                    f"Clip render failed ({primary}); failure state could not be "
                    f"durably recorded ({persistence})."
                ) from primary
            raise
        except BaseException as interrupted:
            try:
                self._finish(
                    render_id,
                    state=ProcessingJobState.UNCERTAIN,
                    reason="clip_render_interrupted_outcome_unknown",
                    context=context,
                )
            except Exception as persistence:
                raise RuntimeError(
                    f"Clip render was interrupted ({interrupted}); uncertain state "
                    f"could not be durably recorded ({persistence})."
                ) from interrupted
            raise
        finally:
            cleanup_errors: list[Exception] = []
            for path in (staged_path, source_staging_path):
                if path is None:
                    continue
                try:
                    self.content_storage.delete_clip_staging(path)
                except Exception as cleanup_error:
                    cleanup_errors.append(cleanup_error)
            if cleanup_errors:
                raise RuntimeError(
                    "One or more managed clip staging files could not be removed."
                ) from cleanup_errors[0]

        if output_metadata is None or media is None or output_key is None:
            raise RuntimeError("Validated clip output was not available for finalization.")
        try:
            terminal = self._finish(
                render_id,
                state=ProcessingJobState.SUCCEEDED,
                reason="clip_rendered_and_independently_validated",
                context=context,
                media=media,
                output=output_metadata,
                quality=quality,
            )
        except Exception as primary:
            try:
                self.content_storage.delete_clip_output(output_key)
            except Exception as cleanup:
                raise RuntimeError(
                    f"Clip finalization failed ({primary}); unreferenced output cleanup "
                    f"failed ({cleanup})."
                ) from primary
            raise
        if terminal != ProcessingJobState.SUCCEEDED:
            self.content_storage.delete_clip_output(output_key)
        completed = self.store.get_clip_render(render_id)
        if completed is None:
            raise RuntimeError("Clip render disappeared after finalization.")
        return completed

    def get_clip_output_bytes(
        self,
        render_id: str,
        *,
        context: AuthorizationContext,
    ) -> bytes:
        action = "content_read_clip_render_output"
        payload = {"render_id": render_id}

        def operation(connection: sqlite3.Connection):
            row = connection.execute(
                """
                SELECT owner_id, state, output_key, output_sha256
                FROM content_clip_renders WHERE render_id = ?
                """,
                (render_id,),
            ).fetchone()
            if (
                row is None
                or row["owner_id"] != context.principal
                or row["state"] != ProcessingJobState.SUCCEEDED.value
                or row["output_key"] is None
                or row["output_sha256"] is None
            ):
                raise PermissionError("A successful owned clip output is required.")
            return render_id, "clip_output_read_authorized", {
                "output_sha256": row["output_sha256"],
            }

        self.content_rights._run(
            context,
            action=action,
            payload=payload,
            command_id=str(uuid4()),
            subject_id=render_id,
            operation=operation,
        )
        render = self.store.get_clip_render(render_id)
        if render is None or render.output_key is None or render.output_sha256 is None:
            raise RuntimeError("Authorized clip output metadata disappeared.")
        return self.content_storage.read_clip_output(
            render.output_key,
            render.output_sha256,
        )

    def get_clip_review_manifest(
        self,
        render_id: str,
        *,
        context: AuthorizationContext,
    ) -> dict[str, object]:
        action = "content_read_clip_review_manifest"
        payload = {"render_id": render_id}
        manifest: dict[str, object] | None = None

        def operation(connection: sqlite3.Connection):
            nonlocal manifest
            row = connection.execute(
                """
                SELECT * FROM content_clip_renders WHERE render_id = ?
                """,
                (render_id,),
            ).fetchone()
            if row is None or row["owner_id"] != context.principal:
                raise PermissionError("Clip review manifest is outside the authorized account.")
            render = self.store._load_clip_render(row)
            candidate, transcript, target, highlight_config = self._load_candidate_context(
                connection,
                render.candidate_id,
                context.principal,
            )
            edit_timeline = self._edit_timeline(
                candidate,
                transcript,
                highlight_config,
            )
            if (
                candidate.transcript_id != render.transcript_id
                or candidate.asset_id != render.asset_id
                or candidate.asset_sha256 != render.asset_sha256
                or candidate.start_seconds != render.start_seconds
                or candidate.end_seconds != render.end_seconds
                or target != render.target_context
            ):
                raise RuntimeError("Clip review provenance linkage is inconsistent.")

            selected_segment_ids = set(candidate.transcript_segment_ids)
            selected_segments = [
                {
                    "segmentId": segment.segment_id,
                    "startSeconds": segment.start_seconds,
                    "endSeconds": segment.end_seconds,
                    "text": segment.text,
                }
                for segment in transcript.segments
                if segment.segment_id in selected_segment_ids
            ]
            succeeded = render.state == ProcessingJobState.SUCCEEDED
            quality_inspection: dict[str, object] = {
                "status": "NOT_ATTEMPTED",
                "blackFrames": 0,
                "frozenFrames": 0,
            }
            if succeeded and render.output_key is not None and render.output_sha256 is not None:
                if render.quality_output_sha256 == render.output_sha256 and render.quality_status is not None:
                    quality_inspection = {
                        "status": render.quality_status,
                        "blackFrames": render.quality_black_frames or 0,
                        "frozenFrames": render.quality_frozen_frames or 0,
                        "blackRanges": json.loads(render.quality_black_ranges_json or "[]"),
                        "frozenRanges": json.loads(render.quality_frozen_ranges_json or "[]"),
                        "revisionCount": render.quality_revision_count or 0,
                    }
                else:
                    quality_inspection = {
                        "status": "NOT_ATTEMPTED",
                        "blackFrames": 0,
                        "frozenFrames": 0,
                        "blackRanges": [],
                        "frozenRanges": [],
                        "revisionCount": 0,
                        "evidenceBindingError": "quality evidence is absent or output hash mismatch",
                    }
            is_vertical = target in {
                ContentTargetContext.YOUTUBE_SHORTS,
                ContentTargetContext.INSTAGRAM_REELS,
            }
            manifest = {
                "renderId": render.render_id,
                "state": render.state.value,
                "previewOnly": True,
                "publication": {
                    "status": "NOT_ATTEMPTED_BY_RENDER",
                    "livePublishingEnabled": False,
                },
                "humanReview": {
                    "required": True,
                    "status": "REQUIRED",
                    "releaseApproval": "SEPARATE_WORKFLOW_NOT_IMPLIED_BY_RENDER",
                },
                "technicalValidation": {
                    "status": "PASSED" if succeeded else "NOT_PASSED",
                    "validatedBeforeSuccess": succeeded,
                    "outputDurationSeconds": render.output_duration_seconds,
                    "outputByteSize": render.output_byte_size,
                    "outputSha256": render.output_sha256,
                    "qualityInspection": quality_inspection,
                    "video": {
                        "present": render.has_video,
                        "codec": "H.264" if succeeded and render.has_video else None,
                        "dimensions": (
                            "720x1280"
                            if succeeded and is_vertical and render.has_video
                            else None
                        ),
                    },
                    "audio": {
                        "present": render.has_audio,
                        "codec": "AAC" if succeeded and render.has_audio else None,
                        "sourceAudioUsed": render.has_audio,
                    },
                },
                "provenance": {
                    "assetId": render.asset_id,
                    "assetSha256": render.asset_sha256,
                    "transcriptId": render.transcript_id,
                    "candidateId": render.candidate_id,
                    "targetContext": target.value,
                    "sourceIntervalSeconds": [
                        render.start_seconds,
                        render.end_seconds,
                    ],
                    "rightsUse": render.rights_use.model_dump(mode="json"),
                    "rightsRevalidatedAtRender": succeeded,
                },
                "transcript": {
                    "engine": transcript.engine_id,
                    "version": transcript.engine_version,
                    "language": transcript.language,
                    "segments": selected_segments,
                    "rawOutputSha256": transcript.raw_output_sha256,
                },
                "highlight": {
                    "startSeconds": candidate.start_seconds,
                    "endSeconds": candidate.end_seconds,
                    "score": candidate.score,
                    "explanation": candidate.explanation,
                    "warnings": list(candidate.warnings),
                },
                "editTimeline": self._timeline_manifest(edit_timeline),
                "editorialLimitations": [
                    "Transcript wording and factual claims require human verification.",
                    "The rendered timeline is an ordered assembly of transcript-timed source intervals.",
                    "No semantic visual understanding or transitions are applied.",
                    (
                        "Audio-only source uses a designed graphic background, not "
                        "source footage."
                        if is_vertical
                        else "Visual composition follows the existing source-media render."
                    ),
                ],
            }
            return render_id, "clip_review_manifest_read_authorized", {
                "state": render.state.value,
                "output_sha256": render.output_sha256,
            }

        self.content_rights._run(
            context,
            action=action,
            payload=payload,
            command_id=str(uuid4()),
            subject_id=render_id,
            operation=operation,
        )
        if manifest is None:
            raise RuntimeError("Authorized clip review manifest was not created.")
        return manifest
