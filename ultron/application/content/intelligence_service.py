from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from datetime import UTC, datetime
from uuid import uuid4

from ultron.domain.content import (
    AssetCategory,
    ContentProcessingJob,
    ContentTargetContext,
    ProcessingJobState,
    RightsAction,
    RightsState,
    RightsUse,
)
from ultron.domain.content_intelligence import (
    EngineTranscript,
    HighlightCandidate,
    HighlightConfig,
    HighlightRun,
    TranscriptRecord,
    TranscriptionConfig,
)
from ultron.security.identity.authorization import AuthorizationContext

from .intelligence import (
    SCORING_VERSION,
    TranscriptionEngine,
    select_highlights,
    validate_engine_transcript,
    validate_pcm_wave,
)
from .clip_renderer import FFmpegLocalClipRenderer
from .service import ContentRightsService
from .store import ContentStore


class ContentIntelligenceService:
    """Authorized transcription and transcript-derived highlight application paths."""

    def __init__(
        self,
        *,
        content_rights: ContentRightsService,
        transcription_engine: TranscriptionEngine,
        media_renderer: FFmpegLocalClipRenderer | None = None,
    ) -> None:
        if type(content_rights) is not ContentRightsService:
            raise TypeError("Content intelligence requires ContentRightsService.")
        if not callable(getattr(transcription_engine, "transcribe", None)):
            raise TypeError("A transcription engine implementing transcribe is required.")
        if not isinstance(getattr(transcription_engine, "engine_id", None), str):
            raise TypeError("Transcription engine identity is required.")
        if not isinstance(getattr(transcription_engine, "engine_version", None), str):
            raise TypeError("Transcription engine version is required.")
        if media_renderer is not None and type(media_renderer) is not FFmpegLocalClipRenderer:
            raise TypeError("Video transcription requires the supported local FFmpeg renderer.")
        self.content_rights = content_rights
        self.store = content_rights.store
        self.transcription_engine = transcription_engine
        self.media_renderer = media_renderer

    @staticmethod
    def _validate_transcript_linkage(
        transcript: TranscriptRecord,
        row: sqlite3.Row,
        use: RightsUse | None = None,
    ) -> None:
        if (
            transcript.job_id != row["job_id"]
            or transcript.asset_id != row["job_asset_id"]
            or transcript.asset_sha256 != row["job_asset_sha256"]
            or transcript.target_context.value != row["job_target_context"]
            or (use is not None and (
                use.asset_id != transcript.asset_id
                or use.expected_asset_sha256 != transcript.asset_sha256
            ))
        ):
            raise RuntimeError("Transcript linkage to its job and source is inconsistent.")

    @staticmethod
    def _validate_candidate_linkage(
        transcript: TranscriptRecord,
        candidates: tuple[HighlightCandidate, ...],
        config: HighlightConfig,
        *,
        run_id: str,
    ) -> None:
        segment_positions = {
            segment.segment_id: position
            for position, segment in enumerate(transcript.segments)
        }
        for expected_rank, candidate in enumerate(candidates, start=1):
            positions = tuple(
                segment_positions.get(segment_id, -1)
                for segment_id in candidate.transcript_segment_ids
            )
            if (
                candidate.run_id != run_id
                or candidate.transcript_id != transcript.transcript_id
                or candidate.asset_id != transcript.asset_id
                or candidate.asset_sha256 != transcript.asset_sha256
                or not positions
                or any(position < 0 for position in positions)
                or positions != tuple(range(positions[0], positions[0] + len(positions)))
                or candidate.start_seconds
                != transcript.segments[positions[0]].start_seconds
                or candidate.end_seconds
                != transcript.segments[positions[-1]].end_seconds
                or not (
                    config.min_duration_seconds
                    <= candidate.duration_seconds
                    <= config.max_duration_seconds
                )
                or candidate.rank != expected_rank
            ):
                raise RuntimeError("Stored highlight candidate linkage is inconsistent.")

    @staticmethod
    def _target_matches_use(
        target_context: ContentTargetContext,
        use: RightsUse,
    ) -> bool:
        expected = {
            ContentTargetContext.YOUTUBE_SHORTS: "youtube",
            ContentTargetContext.YOUTUBE_KIDS_AUDIENCE: "youtube",
            ContentTargetContext.INSTAGRAM_REELS: "instagram",
        }.get(target_context)
        return (
            expected is not None
            and use.platform.value == expected
            and use.actions == frozenset({RightsAction.ANALYZE})
            and not use.commercial_intent
            and not use.monetization_intent
        )

    def create_transcription_job(
        self,
        use: RightsUse,
        *,
        target_context: ContentTargetContext,
        config: TranscriptionConfig,
        context: AuthorizationContext,
        command_id: str,
    ) -> ContentProcessingJob:
        rights = self.content_rights
        if rights.content_storage is None:
            raise RuntimeError("Managed content storage is required for transcription.")
        if not self._target_matches_use(target_context, use):
            raise ValueError("Transcription requires an exact non-commercial ANALYZE use.")
        action = "content_create_transcription_job"
        payload = {
            "use": use.model_dump(mode="json"),
            "target_context": target_context.value,
            "config": config.model_dump(mode="json"),
        }
        job_id = str(uuid4())

        def operation(connection: sqlite3.Connection):
            asset = rights._asset_in_transaction(connection, use.asset_id)
            if (
                asset is None
                or asset.owner_id != context.principal
                or asset.content_sha256 != use.expected_asset_sha256
                or asset.storage_key is None
            ):
                raise PermissionError(
                    "Transcription request does not match a managed registered asset."
                )
            decision = rights._evaluate_rights_in_transaction(
                connection,
                use=use,
                decision_id=str(uuid4()),
                principal=context.principal,
            )
            if (
                decision.copyright_state != RightsState.CLEARED
                or decision.platform_rights_state != RightsState.CLEARED
            ):
                raise PermissionError(
                    "Current rights do not authorize this transcription request."
                )
            now = datetime.now(UTC).isoformat()
            connection.execute(
                """
                INSERT INTO content_processing_jobs (
                    job_id, owner_id, session_id, command_id, asset_id,
                    asset_sha256, target_context, rights_use_json, state,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    context.principal,
                    context.session_id,
                    command_id,
                    asset.asset_id,
                    asset.content_sha256,
                    target_context.value,
                    rights._json(use.model_dump(mode="json")),
                    ProcessingJobState.QUEUED.value,
                    now,
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO content_transcription_requests (
                    job_id, config_json, created_at
                ) VALUES (?, ?, ?)
                """,
                (job_id, rights._json(config.model_dump(mode="json")), now),
            )
            rights._insert_processing_event(
                connection,
                job_id=job_id,
                from_state=None,
                to_state=ProcessingJobState.QUEUED,
                actor=context.principal,
                reason="authorized_transcription_requested",
                now=now,
            )
            return job_id, "queued", {
                "asset_id": asset.asset_id,
                "asset_sha256": asset.content_sha256,
                "rights_decision_id": decision.decision_id,
                "target_context": target_context.value,
                "config": config.model_dump(mode="json"),
            }

        rights._run(
            context,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=job_id,
            operation=operation,
        )
        job = self.store.get_processing_job(job_id)
        if job is None:
            with self.store._lock:
                command = self.store._connection.execute(
                    """
                    SELECT result_id FROM content_commands
                    WHERE principal = ? AND action = ? AND command_id = ?
                    """,
                    (context.principal, action, command_id),
                ).fetchone()
            job = (
                None
                if command is None
                else self.store.get_processing_job(command["result_id"])
            )
        if job is None:
            raise RuntimeError("Authorized transcription job was not durably recorded.")
        return job

    def _finish_without_transcript(
        self,
        job_id: str,
        *,
        state: ProcessingJobState,
        reason: str,
        context: AuthorizationContext,
    ) -> ProcessingJobState:
        return self.content_rights._finish_processing_job(
            job_id,
            state=state,
            reason=reason,
            context=context,
        )

    def _finish_with_transcript(
        self,
        transcript: TranscriptRecord,
        *,
        context: AuthorizationContext,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        action = "content_transcription_succeeded"
        payload = {
            "job_id": transcript.job_id,
            "transcript_id": transcript.transcript_id,
            "raw_output_sha256": transcript.raw_output_sha256,
            "segment_count": len(transcript.segments),
        }
        fingerprint = ContentStore._fingerprint(action, payload)
        with self.store._transaction() as connection:
            row = connection.execute(
                """
                SELECT owner_id, state, asset_id, asset_sha256, target_context
                FROM content_processing_jobs
                WHERE job_id = ?
                """,
                (transcript.job_id,),
            ).fetchone()
            if row is None or row["owner_id"] != context.principal:
                raise PermissionError("Transcription job is outside the authorized account.")
            if row["state"] != ProcessingJobState.RUNNING.value:
                raise RuntimeError("Transcription job is no longer running.")
            if (
                transcript.asset_id != row["asset_id"]
                or transcript.asset_sha256 != row["asset_sha256"]
                or transcript.target_context.value != row["target_context"]
            ):
                raise RuntimeError("Transcript does not match its running job.")
            connection.execute(
                """
                INSERT INTO content_transcripts (
                    transcript_id, job_id, asset_id, asset_sha256,
                    target_context, duration_seconds, engine_id, engine_version,
                    language, raw_output_sha256, validation_version,
                    segments_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    transcript.transcript_id,
                    transcript.job_id,
                    transcript.asset_id,
                    transcript.asset_sha256,
                    transcript.target_context.value,
                    transcript.duration_seconds,
                    transcript.engine_id,
                    transcript.engine_version,
                    transcript.language,
                    transcript.raw_output_sha256,
                    transcript.validation_version,
                    json.dumps(
                        [segment.model_dump(mode="json") for segment in transcript.segments],
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )
            connection.execute(
                """
                UPDATE content_processing_jobs
                SET state = ?, updated_at = ?
                WHERE job_id = ? AND state = ?
                """,
                (
                    ProcessingJobState.SUCCEEDED.value,
                    now,
                    transcript.job_id,
                    ProcessingJobState.RUNNING.value,
                ),
            )
            self.content_rights._insert_processing_event(
                connection,
                job_id=transcript.job_id,
                from_state=ProcessingJobState.RUNNING,
                to_state=ProcessingJobState.SUCCEEDED,
                actor=context.principal,
                reason="transcript_validated_and_committed",
                now=now,
            )
            self.store._insert_audit(
                connection,
                context,
                action,
                transcript.job_id,
                "succeeded",
                fingerprint,
                payload,
                now,
            )

    def run_transcription_job(
        self,
        job_id: str,
        *,
        context: AuthorizationContext,
        command_id: str,
    ) -> TranscriptRecord:
        rights = self.content_rights
        if rights.content_storage is None:
            raise RuntimeError("Managed content storage is required for transcription.")
        action = "content_run_transcription_job"
        payload = {"job_id": job_id}
        input_bytes: bytes | None = None
        audio_metadata = None
        config: TranscriptionConfig | None = None
        job: ContentProcessingJob | None = None
        asset_category: AssetCategory | None = None
        did_claim = False

        def claim(connection: sqlite3.Connection):
            nonlocal input_bytes, audio_metadata, config, job, did_claim
            nonlocal asset_category
            row = connection.execute(
                """
                SELECT j.*, r.config_json
                FROM content_processing_jobs j
                JOIN content_transcription_requests r ON r.job_id = j.job_id
                WHERE j.job_id = ?
                """,
                (job_id,),
            ).fetchone()
            if row is None or row["owner_id"] != context.principal:
                raise PermissionError("Transcription job is outside the authorized account.")
            if ProcessingJobState(row["state"]) != ProcessingJobState.QUEUED:
                raise PermissionError("Transcription job is not available for dispatch.")
            job = self.store._load_processing_job(row)
            config = TranscriptionConfig.model_validate_json(row["config_json"])
            if not self._target_matches_use(job.target_context, job.rights_use):
                raise RuntimeError("Persisted transcription scope is inconsistent.")
            asset = rights._asset_in_transaction(connection, job.asset_id)
            if (
                asset is None
                or asset.owner_id != context.principal
                or asset.content_sha256 != job.asset_sha256
                or asset.storage_key is None
                or job.rights_use.expected_asset_sha256 != asset.content_sha256
            ):
                raise RuntimeError("Transcription job asset linkage is inconsistent.")
            try:
                input_bytes = rights.content_storage.read_verified_input(
                    asset.storage_key,
                    asset.content_sha256,
                )
                asset_category = asset.category
            except Exception as exc:
                now = datetime.now(UTC).isoformat()
                connection.execute(
                    """
                    UPDATE content_processing_jobs
                    SET state = ?, updated_at = ?
                    WHERE job_id = ? AND state = ?
                    """,
                    (
                        ProcessingJobState.FAILED.value,
                        now,
                        job_id,
                        ProcessingJobState.QUEUED.value,
                    ),
                )
                self.content_rights._insert_processing_event(
                    connection,
                    job_id=job_id,
                    from_state=ProcessingJobState.QUEUED,
                    to_state=ProcessingJobState.FAILED,
                    actor=context.principal,
                    reason=f"audio_input_invalid:{type(exc).__name__}",
                    now=now,
                )
                did_claim = True
                return job_id, "invalid_audio", {"failure": type(exc).__name__}

            decision = rights._evaluate_rights_in_transaction(
                connection,
                use=job.rights_use,
                decision_id=str(uuid4()),
                principal=context.principal,
            )
            if (
                decision.copyright_state != RightsState.CLEARED
                or decision.platform_rights_state != RightsState.CLEARED
            ):
                now = datetime.now(UTC).isoformat()
                connection.execute(
                    """
                    UPDATE content_processing_jobs
                    SET state = ?, updated_at = ?
                    WHERE job_id = ? AND state = ?
                    """,
                    (
                        ProcessingJobState.FAILED.value,
                        now,
                        job_id,
                        ProcessingJobState.QUEUED.value,
                    ),
                )
                self.content_rights._insert_processing_event(
                    connection,
                    job_id=job_id,
                    from_state=ProcessingJobState.QUEUED,
                    to_state=ProcessingJobState.FAILED,
                    actor=context.principal,
                    reason="rights_revalidation_denied_before_transcription",
                    now=now,
                )
                did_claim = True
                return job_id, "rights_denied", {
                    "rights_decision_id": decision.decision_id,
                    "reason_codes": list(decision.reason_codes),
                }
            now = datetime.now(UTC).isoformat()
            connection.execute(
                """
                UPDATE content_processing_jobs
                SET state = ?, updated_at = ?
                WHERE job_id = ? AND state = ?
                """,
                (
                    ProcessingJobState.RUNNING.value,
                    now,
                    job_id,
                    ProcessingJobState.QUEUED.value,
                ),
            )
            self.content_rights._insert_processing_event(
                connection,
                job_id=job_id,
                from_state=ProcessingJobState.QUEUED,
                to_state=ProcessingJobState.RUNNING,
                actor=context.principal,
                reason="rights_revalidated_and_transcription_claimed",
                now=now,
            )
            did_claim = True
            return job_id, "running", {
                "rights_decision_id": decision.decision_id,
                "asset_sha256": job.asset_sha256,
                "asset_category": asset_category.value,
            }

        rights._run(
            context,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=job_id,
            operation=claim,
        )
        current = self.store.get_processing_job(job_id)
        if current is None:
            raise RuntimeError("Transcription job disappeared after worker claim.")
        if not did_claim:
            transcript = self.store.get_transcript_for_job(job_id)
            if transcript is not None:
                return transcript
            raise PermissionError(
                "Transcription job was already claimed or completed without a transcript."
            )
        if current.state != ProcessingJobState.RUNNING:
            raise RuntimeError("Transcription job failed before engine dispatch.")
        if (
            input_bytes is None
            or config is None
            or job is None
            or asset_category is None
        ):
            raise RuntimeError("Validated audio input was not bound to the transcription job.")

        started = time.monotonic()
        staged_source = None
        try:
            is_pcm_wave = (
                len(input_bytes) >= 12
                and input_bytes[:4] == b"RIFF"
                and input_bytes[8:12] == b"WAVE"
            )
            if asset_category == AssetCategory.FOOTAGE and not is_pcm_wave:
                if self.media_renderer is None:
                    raise RuntimeError(
                        "Local FFmpeg video-audio extraction is not configured."
                    )
                staged_source = rights.content_storage.stage_clip_source(
                    job_id,
                    input_bytes,
                )
                input_bytes = self.media_renderer.extract_pcm_audio(staged_source)
                expected_audio_sha256 = hashlib.sha256(input_bytes).hexdigest()
            else:
                expected_audio_sha256 = job.asset_sha256
            validated_audio = validate_pcm_wave(
                input_bytes,
                expected_sha256=expected_audio_sha256,
                config=config,
            )
            audio_metadata = validated_audio.metadata
            engine_result = self.transcription_engine.transcribe(
                input_bytes,
                language=config.language,
                max_segments=config.max_segments,
                max_segment_characters=config.max_segment_characters,
                max_text_characters=config.max_text_characters,
            )
            if time.monotonic() - started > config.max_runtime_seconds:
                raise TimeoutError(
                    "Transcription exceeded its runtime limit; the engine was not preempted."
                )
            if type(engine_result) is not EngineTranscript:
                raise ValueError("Transcription engine returned an unsupported result structure.")
            transcript = validate_engine_transcript(
                engine_result,
                transcript_id=str(uuid4()),
                job_id=job.job_id,
                asset_id=job.asset_id,
                asset_sha256=job.asset_sha256,
                target_context=job.target_context,
                duration_seconds=audio_metadata.duration_seconds,
                engine_id=self.transcription_engine.engine_id,
                engine_version=self.transcription_engine.engine_version,
                config=config,
            )
        except TimeoutError:
            self._finish_without_transcript(
                job_id,
                state=ProcessingJobState.UNCERTAIN,
                reason="transcription_timeout_outcome_unknown",
                context=context,
            )
            raise
        except Exception as primary:
            try:
                self._finish_without_transcript(
                    job_id,
                    state=ProcessingJobState.FAILED,
                    reason=f"transcription_failed:{type(primary).__name__}",
                    context=context,
                )
            except Exception as persistence:
                raise RuntimeError(
                    f"Transcription failed ({primary}); failure state was not durably "
                    f"recorded ({persistence})."
                ) from primary
            raise
        finally:
            if staged_source is not None:
                rights.content_storage.delete_clip_staging(staged_source)
        if audio_metadata is None:
            raise RuntimeError("Validated audio metadata was not produced.")
        try:
            self._finish_with_transcript(transcript, context=context)
        except Exception as primary:
            try:
                self._finish_without_transcript(
                    job_id,
                    state=ProcessingJobState.UNCERTAIN,
                    reason="transcript_persistence_failed_after_engine_execution",
                    context=context,
                )
            except Exception as persistence:
                raise RuntimeError(
                    f"Transcript persistence failed ({primary}); uncertain execution "
                    f"could not be durably recorded ({persistence})."
                ) from primary
            raise RuntimeError(
                "Transcript persistence failed; the executed transcription is "
                "UNCERTAIN and no transcript result was committed."
            ) from primary
        stored = self.store.get_transcript_for_job(job_id)
        if stored is None:
            raise RuntimeError("Transcript success committed without a transcript record.")
        return stored

    def get_transcript(
        self,
        transcript_id: str,
        *,
        context: AuthorizationContext,
        command_id: str,
    ) -> TranscriptRecord:
        action = "content_read_transcript"
        payload = {"transcript_id": transcript_id}

        def operation(connection: sqlite3.Connection):
            row = connection.execute(
                """
                SELECT t.*, j.owner_id, j.state AS job_state,
                    j.job_id AS job_id, j.asset_id AS job_asset_id,
                    j.asset_sha256 AS job_asset_sha256,
                    j.target_context AS job_target_context,
                    j.rights_use_json
                FROM content_transcripts t
                JOIN content_processing_jobs j ON j.job_id = t.job_id
                WHERE t.transcript_id = ?
                """,
                (transcript_id,),
            ).fetchone()
            if (
                row is None
                or row["owner_id"] != context.principal
                or row["job_state"] != ProcessingJobState.SUCCEEDED.value
            ):
                raise PermissionError("A successful owned transcript is required.")
            transcript = self.store._load_transcript(row)
            use = RightsUse.model_validate_json(row["rights_use_json"])
            self._validate_transcript_linkage(transcript, row, use)
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
                raise PermissionError(
                    "Current rights do not authorize access to this transcript."
                )
            return transcript_id, "read_authorized", {
                "asset_id": transcript.asset_id,
                "asset_sha256": transcript.asset_sha256,
                "transcript_fingerprint": transcript.fingerprint(),
            }

        self.content_rights._run(
            context,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=transcript_id,
            operation=operation,
        )
        transcript = self.store.get_transcript(transcript_id)
        if transcript is None:
            raise RuntimeError("Authorized transcript disappeared after retrieval.")
        return transcript

    def generate_highlights(
        self,
        transcript_id: str,
        *,
        config: HighlightConfig,
        context: AuthorizationContext,
        command_id: str,
    ) -> HighlightRun:
        action = "content_generate_highlights"
        payload = {
            "transcript_id": transcript_id,
            "config": config.model_dump(mode="json"),
        }
        run_id: str | None = None

        def operation(connection: sqlite3.Connection):
            nonlocal run_id
            row = connection.execute(
                """
                SELECT t.*, j.owner_id, j.job_id AS job_id,
                    j.state AS job_state, j.rights_use_json,
                    j.asset_id AS job_asset_id,
                    j.asset_sha256 AS job_asset_sha256,
                    j.target_context AS job_target_context
                FROM content_transcripts t
                JOIN content_processing_jobs j ON j.job_id = t.job_id
                WHERE t.transcript_id = ?
                """,
                (transcript_id,),
            ).fetchone()
            if (
                row is None
                or row["owner_id"] != context.principal
                or row["job_state"] != ProcessingJobState.SUCCEEDED.value
            ):
                raise PermissionError("A successful owned transcript is required.")
            transcript = self.store._load_transcript(row)
            use = RightsUse.model_validate_json(row["rights_use_json"])
            self._validate_transcript_linkage(transcript, row, use)
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
                raise PermissionError(
                    "Current rights do not authorize transcript analysis."
                )
            transcript_fingerprint = transcript.fingerprint()
            config_json = self.content_rights._json(config.model_dump(mode="json"))
            run_identity = "|".join(
                (transcript_fingerprint, config_json, SCORING_VERSION)
            )
            run_id = "hr-" + hashlib.sha256(
                run_identity.encode("utf-8")
            ).hexdigest()[:32]
            existing = connection.execute(
                "SELECT * FROM content_highlight_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            if existing is not None:
                if (
                    existing["transcript_id"] != transcript_id
                    or existing["transcript_fingerprint"] != transcript_fingerprint
                    or existing["config_json"] != config_json
                    or existing["scoring_version"] != SCORING_VERSION
                ):
                    raise RuntimeError("Deterministic highlight-run identity conflict.")
                candidate_count = connection.execute(
                    "SELECT COUNT(*) FROM content_highlight_candidates WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0]
                return run_id, "idempotent_replay", {
                    "transcript_id": transcript_id,
                    "transcript_fingerprint": transcript_fingerprint,
                    "candidate_count": candidate_count,
                    "scoring_version": SCORING_VERSION,
                }
            candidates = select_highlights(
                transcript,
                run_id=run_id,
                config=config,
            )
            now = datetime.now(UTC).isoformat()
            connection.execute(
                """
                INSERT INTO content_highlight_runs (
                    run_id, transcript_id, transcript_fingerprint,
                    config_json, scoring_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    transcript_id,
                    transcript_fingerprint,
                    self.content_rights._json(config.model_dump(mode="json")),
                    SCORING_VERSION,
                    now,
                ),
            )
            for candidate in candidates:
                connection.execute(
                    """
                    INSERT INTO content_highlight_candidates (
                        candidate_id, run_id, transcript_id, asset_id,
                        asset_sha256, start_seconds, end_seconds,
                        duration_seconds, rank, score, scores_json, evidence_json,
                        segment_ids_json, explanation, warnings_json,
                        scoring_version, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        candidate.candidate_id,
                        candidate.run_id,
                        candidate.transcript_id,
                        candidate.asset_id,
                        candidate.asset_sha256,
                        candidate.start_seconds,
                        candidate.end_seconds,
                        candidate.duration_seconds,
                        candidate.rank,
                        candidate.score,
                        self.content_rights._json(candidate.scores),
                        self.content_rights._json(candidate.evidence),
                        self.content_rights._json(candidate.transcript_segment_ids),
                        candidate.explanation,
                        self.content_rights._json(candidate.warnings),
                        candidate.scoring_version,
                        now,
                    ),
                )
            return run_id, "analyzed", {
                "transcript_id": transcript_id,
                "transcript_fingerprint": transcript_fingerprint,
                "candidate_count": len(candidates),
                "scoring_version": SCORING_VERSION,
            }

        self.content_rights._run(
            context,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=transcript_id,
            operation=operation,
        )
        if run_id is None:
            with self.store._lock:
                command = self.store._connection.execute(
                    """
                    SELECT result_id FROM content_commands
                    WHERE principal = ? AND action = ? AND command_id = ?
                    """,
                    (context.principal, action, command_id),
                ).fetchone()
            run_id = None if command is None else command["result_id"]
        if run_id is None:
            raise RuntimeError("Highlight analysis did not identify its durable run.")
        with self.store._lock:
            run_row = self.store._connection.execute(
                "SELECT * FROM content_highlight_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        if run_row is None:
            raise RuntimeError("Highlight analysis did not persist a durable run.")
        stored_transcript = self.store.get_transcript(run_row["transcript_id"])
        if stored_transcript is None:
            raise RuntimeError("Highlight run references a missing transcript.")
        if stored_transcript.fingerprint() != run_row["transcript_fingerprint"]:
            raise RuntimeError("Stored highlight run references a stale transcript.")
        candidates = self.store.get_highlight_candidates(run_row["run_id"])
        config = HighlightConfig.model_validate_json(run_row["config_json"])
        self._validate_candidate_linkage(
            stored_transcript,
            candidates,
            config,
            run_id=run_row["run_id"],
        )
        return HighlightRun(
            run_id=run_row["run_id"],
            transcript_id=run_row["transcript_id"],
            transcript_fingerprint=run_row["transcript_fingerprint"],
            config=config,
            scoring_version=run_row["scoring_version"],
            candidates=candidates,
            created_at=run_row["created_at"],
        )

    def get_highlight_candidates(
        self,
        run_id: str,
        *,
        context: AuthorizationContext,
        command_id: str,
    ) -> tuple[HighlightCandidate, ...]:
        action = "content_read_highlight_candidates"
        payload = {"run_id": run_id}

        def operation(connection: sqlite3.Connection):
            row = connection.execute(
                """
                SELECT r.transcript_id, r.transcript_fingerprint,
                    j.owner_id, j.rights_use_json, j.job_id AS job_id,
                    j.asset_id AS job_asset_id,
                    j.asset_sha256 AS job_asset_sha256,
                    j.target_context AS job_target_context
                FROM content_highlight_runs r
                JOIN content_transcripts t ON t.transcript_id = r.transcript_id
                JOIN content_processing_jobs j ON j.job_id = t.job_id
                WHERE r.run_id = ?
                """,
                (run_id,),
            ).fetchone()
            if row is None or row["owner_id"] != context.principal:
                raise PermissionError("Highlight run is outside the caller's account.")
            transcript = self.store.get_transcript(row["transcript_id"])
            if (
                transcript is None
                or transcript.fingerprint() != row["transcript_fingerprint"]
            ):
                raise RuntimeError("Highlight run references a stale or missing transcript.")
            use = RightsUse.model_validate_json(row["rights_use_json"])
            self._validate_transcript_linkage(transcript, row, use)
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
                raise PermissionError(
                    "Current rights do not authorize access to these highlight candidates."
                )
            return run_id, "read_authorized", {
                "transcript_id": row["transcript_id"],
                "transcript_fingerprint": row["transcript_fingerprint"],
            }

        self.content_rights._run(
            context,
            action=action,
            payload=payload,
            command_id=command_id,
            subject_id=run_id,
            operation=operation,
        )
        with self.store._lock:
            run_row = self.store._connection.execute(
                """
                SELECT transcript_id, transcript_fingerprint
                FROM content_highlight_runs WHERE run_id = ?
                """,
                (run_id,),
            ).fetchone()
        if run_row is None:
            raise RuntimeError("Authorized highlight run is missing.")
        candidates = self.store.get_highlight_candidates(run_id)
        transcript = self.store.get_transcript(run_row["transcript_id"])
        if (
            transcript is None
            or transcript.fingerprint() != run_row["transcript_fingerprint"]
        ):
            raise RuntimeError("Authorized highlight transcript is stale or missing.")
        config = self.store.get_highlight_config(run_id)
        if config is None:
            raise RuntimeError("Authorized highlight records are incomplete.")
        self._validate_candidate_linkage(
            transcript,
            candidates,
            config,
            run_id=run_id,
        )
        return candidates
