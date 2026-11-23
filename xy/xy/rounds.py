"""Rounds are YAML files in the rounds directory, one per round.

The file name (without .yaml) is the round id used in URLs and in the
database, so never rename a file once people have played it. See
rounds/README.md for the format.
"""

import datetime as dt
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

SLUG = r"^[a-z0-9][a-z0-9-]{0,62}$"


class Pole(BaseModel):
    neg: str = Field(min_length=1, max_length=40)
    pos: str = Field(min_length=1, max_length=40)


class Axes(BaseModel):
    x: Pole
    y: Pole


class Candidate(BaseModel):
    id: str = Field(pattern=SLUG)
    name: str = Field(min_length=1, max_length=40)
    emoji: str = Field(default="", max_length=16)


class Round(BaseModel):
    id: str = Field(pattern=SLUG)
    title: str = Field(min_length=1, max_length=80)
    # Hidden until that day (Europe/Paris is close enough to the server's date)
    date: dt.date
    axes: Axes
    candidates: list[Candidate] = Field(min_length=2, max_length=40)

    @field_validator("candidates")
    @classmethod
    def unique_ids(cls, candidates: list[Candidate]) -> list[Candidate]:
        ids = [c.id for c in candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("candidate ids must be unique")
        return candidates

    @model_validator(mode="after")
    def no_empty_axis(self) -> "Round":
        if self.axes.x.neg == self.axes.x.pos or self.axes.y.neg == self.axes.y.pos:
            raise ValueError("an axis needs two different poles")
        return self

    def candidate_ids(self) -> set[str]:
        return {c.id for c in self.candidates}


def load(directory: Path) -> dict[str, Round]:
    """Every round in the directory, newest first."""
    rounds = []
    for path in sorted(directory.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        rounds.append(Round.model_validate({**data, "id": path.stem}))
    rounds.sort(key=lambda r: (r.date, r.id), reverse=True)
    return {r.id: r for r in rounds}


def published(rounds: dict[str, Round], today: dt.date) -> list[Round]:
    return [r for r in rounds.values() if r.date <= today]
