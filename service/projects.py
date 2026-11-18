"""Load the operator-managed organization and repository directory."""

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .evaluate_builds import BuildTargets
from .evaluation_config import EvaluationSnapshot, read_evaluation

Role = Literal["viewer", "maintainer", "approver"]
GitHubID = Annotated[int, Field(strict=True, gt=0, le=2**63 - 1)]


class ProjectConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    repository_id: GitHubID
    installation_id: GitHubID
    organization: Annotated[str, Field(min_length=1, max_length=63, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]
    name: Annotated[str, Field(min_length=1, max_length=120)]
    build_targets_file: Path | None = None
    evaluation_file: Path | None = None


class ProjectDirectory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    projects: Annotated[list[ProjectConfig], Field(min_length=1)]


@dataclass(frozen=True)
class Project:
    repository_id: int
    installation_id: int
    organization: str
    name: str
    targets: BuildTargets | None
    evaluation: EvaluationSnapshot | None = None


def read_targets(path):
    if path:
        try:
            return BuildTargets.model_validate_json(Path(path).read_bytes())
        except (OSError, ValidationError):
            raise ValueError("Invalid configured build target file") from None
    return None


def managed_projects(path):
    path = Path(path)
    try:
        config = ProjectDirectory.model_validate_json(path.read_bytes())
    except (OSError, ValidationError):
        raise ValueError("Invalid managed project file") from None
    projects = {}
    for entry in config.projects:
        if entry.repository_id in projects:
            raise ValueError("Each repository must appear only once in the managed project file")
        targets = read_targets(path.parent / entry.build_targets_file) if entry.build_targets_file else None
        if entry.evaluation_file and targets is None:
            raise ValueError("A project evaluation requires a build targets file")
        evaluation = read_evaluation(path.parent / entry.evaluation_file) if entry.evaluation_file else None
        projects[entry.repository_id] = Project(
            entry.repository_id, entry.installation_id, entry.organization, entry.name, targets, evaluation,
        )
    return projects


def load_projects():
    path = os.getenv("RELEASETRACE_PROJECTS_FILE")
    if path:
        return managed_projects(path), True
    repository = int(os.environ["RELEASETRACE_REPOSITORY_ID"])
    installation = int(os.environ["RELEASETRACE_INSTALLATION_ID"])
    if not (0 < repository <= 2**63 - 1 and 0 < installation <= 2**63 - 1):
        raise ValueError("Positive GitHub IDs are required")
    return {
        repository: Project(repository, installation, "local", f"Repository {repository}",
                            read_targets(os.getenv("RELEASETRACE_BUILD_TARGETS_FILE"))),
    }, False
