"""Validate and snapshot operator-managed citation evaluations."""

from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, model_validator

from demo.release_trace_demo import digest

Revision = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")]
CaseID = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")]
Citation = Annotated[str, StringConstraints(min_length=1, max_length=128)]
MAX_CONFIG_BYTES = 512 * 1024


class EvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class CitationEvidence(EvaluationModel):
    id: Citation
    revision: Annotated[int, Field(ge=1)]
    text: Annotated[str, Field(min_length=1, max_length=4096)]


class EvaluationCase(EvaluationModel):
    case_id: CaseID
    question: Annotated[str, Field(min_length=1, max_length=4096)]
    required_citation: Citation
    policies: Annotated[list[CitationEvidence], Field(min_length=1, max_length=16)]

    @model_validator(mode="after")
    def validate_citations(self):
        ids = {item.id for item in self.policies}
        if len(ids) != len(self.policies) or self.required_citation not in ids:
            raise ValueError("Citations must be unique and include the expected citation")
        return self


class EvaluationSuite(EvaluationModel):
    revision: Revision
    # Two sequential requests per case must fit the Temporal activity budget.
    cases: Annotated[list[EvaluationCase], Field(min_length=1, max_length=25)]


class GatePolicy(EvaluationModel):
    revision: Revision
    minimum_score: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
    critical_cases: Annotated[list[CaseID], Field(max_length=25)]


class EvaluationDefinition(EvaluationModel):
    suite: EvaluationSuite
    policy: GatePolicy

    @model_validator(mode="after")
    def validate_policy(self):
        ids = {case.case_id for case in self.suite.cases}
        critical = set(self.policy.critical_cases)
        if len(ids) != len(self.suite.cases) or len(critical) != len(self.policy.critical_cases) or critical - ids:
            raise ValueError("Cases and critical references must be unique, and critical cases must be in the suite")
        return self

    def snapshot(self):
        data = self.model_dump(mode="json")
        return EvaluationSnapshot(
            **data, dataset_digest=digest(data["suite"]), policy_digest=digest(data["policy"]),
        )


class EvaluationSnapshot(EvaluationDefinition):
    dataset_digest: str
    policy_digest: str

    @model_validator(mode="after")
    def validate_fingerprints(self):
        if (self.dataset_digest != digest(self.suite.model_dump(mode="json"))
                or self.policy_digest != digest(self.policy.model_dump(mode="json"))):
            raise ValueError("Evaluation input fingerprints do not match the snapshot")
        return self

    def cases(self):
        critical = set(self.policy.critical_cases)
        return [{**case.model_dump(mode="json"), "critical": case.case_id in critical} for case in self.suite.cases]


def read_evaluation(path: Path):
    try:
        with path.open("rb") as source:
            body = source.read(MAX_CONFIG_BYTES + 1)
        if len(body) > MAX_CONFIG_BYTES:
            raise ValueError("Evaluation file exceeds the size limit")
        return EvaluationDefinition.model_validate_json(body).snapshot()
    except (OSError, ValidationError):
        raise ValueError("Invalid configured evaluation file") from None
