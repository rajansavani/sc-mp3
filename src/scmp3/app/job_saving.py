"""One save, as the save worker runs it: check the file name, then tag and move the file.

A taken name under the "ask" policy comes back as a question instead of waiting for an answer,
so the one save worker never sits blocked on the user (T-016).
"""

from dataclasses import dataclass, replace
from pathlib import Path

from scmp3.app.job_record import JobRecord
from scmp3.app.job_steps import Steps
from scmp3.core import pipeline
from scmp3.core.library import CollisionChoice, reuse_folders_by_case
from scmp3.core.models import ArtworkChoice, CollisionPolicy, Settings, TagSuggestion, TitleStyle


@dataclass(frozen=True)
class SaveOrder:
    """Everything one save needs, taken under the lock so edits made meanwhile can't mix in."""

    prepared: pipeline.Prepared
    staged: pipeline.Staged
    suggestion: TagSuggestion
    overrides: dict[str, str]
    style: TitleStyle | None
    artwork: ArtworkChoice
    settings: Settings
    # The user's answer about a taken name, and the file it was about.
    answer: tuple[Path, CollisionChoice] | None


@dataclass(frozen=True)
class CollisionQuestion:
    """The file name is taken and the policy is to ask, so the user decides first."""

    existing: Path


def save_order(record: JobRecord, settings: Settings) -> SaveOrder:
    """What the job saves with, as it is now. The job must have its audio."""
    job = record.job
    if record.prepared is None or record.staged is None or job.suggestion is None:
        raise ValueError("a job is saved only once it's resolved and its audio is in")
    return SaveOrder(
        prepared=record.prepared,
        staged=record.staged,
        suggestion=job.suggestion,
        overrides=dict(job.overrides),
        style=job.style,
        artwork=cover_to_save(record),
        settings=settings,
        answer=record.collision_answer,
    )


def cover_to_save(record: JobRecord) -> ArtworkChoice:
    """The job's cover choice, with the source's cover read from the copy kept when it resolved."""
    artwork = record.job.artwork
    kept = record.source_cover_file
    if artwork.kind != "source" or kept is None or not kept.is_file():
        return artwork
    return ArtworkChoice("file", str(kept), artwork.crop)


def keep_both(path: Path) -> CollisionChoice:
    """The answer for a file that appears between the name check and the save."""
    return "keep_both"


def save(order: SaveOrder, steps: Steps) -> pipeline.Saved | CollisionQuestion | None:
    """Save the file, or ask first when its name is taken. None means the policy said to skip.

    The name is planned with pipeline.destination, and folders that differ only by case are
    reused, as finalize does, so the two agree. That lists folders, which is fine here on the
    save worker. Raises ScmpError, or InvalidOverride for hand edits that can't be rendered.
    """
    source = order.prepared.source
    tags = pipeline.render_tags(
        source, order.suggestion, order.settings, style=order.style, overrides=order.overrides
    )
    planned = pipeline.destination(
        source,
        order.suggestion,
        order.staged,
        tags,
        order.settings,
        style=order.style,
        overrides=order.overrides,
    )
    # So the question is about the file finalize would replace, as in "kaia" planned as "Kaia".
    # Finalize looks again and warns about a folder it can't list, so the save warns once.
    planned = reuse_folders_by_case(planned, order.settings.library_dir, warn=False)
    policy = answered_policy(order, planned)
    if policy == "ask" and planned.exists():
        return CollisionQuestion(planned)
    return steps.finalize(
        order.prepared,
        order.staged,
        tags,
        order.artwork,
        replace(order.settings, when_file_exists=policy),
        style=order.style,
        suggestion=order.suggestion,
        overrides=order.overrides,
        ask=keep_both,
    )


def answered_policy(order: SaveOrder, planned: Path) -> CollisionPolicy:
    """The user's answer when it was about this very file, or else the setting.

    An answer about another file, such as the name before a settings change, never applies, so a
    file the user wasn't asked about is never replaced.
    """
    if order.answer is None:
        return order.settings.when_file_exists
    asked_about, choice = order.answer
    return choice if asked_about == planned else order.settings.when_file_exists
