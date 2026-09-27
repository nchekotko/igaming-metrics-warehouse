"""Data contracts: YAML definitions compiled into Pydantic models.

The YAML file (``contracts/contracts.yml``) is the single source of truth. For every entity we
build a Pydantic model with the declared types/constraints, plus a timestamp normalisation step
that enforces the timezone policy:

* default: an ISO-8601 string *with* a UTC offset is required; it is converted to naive UTC;
* ``timezone_by_source``: a producer is allowed to send local wall-clock time, which is
  localised with the declared IANA zone (DST-aware) and then converted to UTC.

Anything else (missing fields, wrong types, bad enums, negative amounts, epoch integers,
offset-less timestamps from producers without an override) is a contract violation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, create_model

from imw.config import CONTRACTS_PATH

_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d{1,6})?(?P<tz>Z|[+-]\d{2}:\d{2})?$")

DUCKDB_TYPES = {
    "string": "VARCHAR",
    "enum": "VARCHAR",
    "integer": "BIGINT",
    "decimal": "DECIMAL(18,2)",
    "boolean": "BOOLEAN",
    "timestamp": "TIMESTAMP",
}


class ContractViolation(ValueError):
    """Raised when a payload does not satisfy its contract."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


@dataclass(frozen=True)
class FieldSpec:
    name: str
    type: str
    nullable: bool = False
    values: tuple[str, ...] = ()
    pattern: str | None = None
    ge: float | None = None
    gt: float | None = None
    le: float | None = None


@dataclass
class EntityContract:
    name: str
    producer: str
    primary_key: str
    fields: list[FieldSpec]
    timezone_by_source: dict[str, str] = field(default_factory=dict)
    model: type[BaseModel] | None = None

    @property
    def timestamp_fields(self) -> list[str]:
        return [f.name for f in self.fields if f.type == "timestamp"]

    def ddl_columns(self) -> list[tuple[str, str]]:
        return [(f.name, DUCKDB_TYPES[f.type]) for f in self.fields]

    def validate(self, payload: Any, source: str) -> tuple[dict[str, Any], bool]:
        """Validate one payload.

        Returns ``(normalised_record, tz_adjusted)``; raises :class:`ContractViolation`.
        ``tz_adjusted`` is True when at least one timestamp was not already in UTC
        (non-zero offset or an assumed local zone) — useful for DQ monitoring.
        """
        if not isinstance(payload, dict):
            raise ContractViolation(["payload is not a JSON object"])
        assert self.model is not None
        try:
            obj = self.model.model_validate(payload)
        except ValidationError as exc:
            raise ContractViolation(
                [f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()]
            ) from None
        record = obj.model_dump()
        errors: list[str] = []
        tz_adjusted = False
        assumed_zone = self.timezone_by_source.get(source)
        for name in self.timestamp_fields:
            value = record[name]
            if value is None:
                continue
            try:
                record[name], adjusted = parse_timestamp(value, assumed_zone)
                tz_adjusted = tz_adjusted or adjusted
            except ValueError as exc:
                errors.append(f"{name}: {exc}")
        if errors:
            raise ContractViolation(errors)
        return record, tz_adjusted


def parse_timestamp(value: str, assumed_zone: str | None = None) -> tuple[datetime, bool]:
    """Parse an ISO-8601 string into naive UTC.

    Offset-less strings are only accepted when ``assumed_zone`` is given. Wall-clock times that
    do not exist (spring-forward gap) are shifted forward by the gap, ambiguous times
    (fall-back) resolve to the first occurrence (``fold=0``) — both documented in README.
    """
    m = _ISO_RE.match(value)
    if not m:
        raise ValueError(f"not an ISO-8601 timestamp: {value!r}")
    ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if m.group("tz") is not None:
        utc = ts.astimezone(UTC).replace(tzinfo=None)
        return utc, ts.utcoffset() != timedelta(0)
    if assumed_zone is None:
        raise ValueError(f"timestamp without UTC offset: {value!r}")
    zone = ZoneInfo(assumed_zone)
    local = ts.replace(tzinfo=zone, fold=0)
    # Non-existent local time (DST gap): round-tripping through UTC changes the wall clock.
    roundtrip = local.astimezone(UTC).astimezone(zone)
    if roundtrip.replace(tzinfo=None) != ts:
        local = (ts + timedelta(hours=1)).replace(tzinfo=zone, fold=0)
    return local.astimezone(UTC).replace(tzinfo=None), True


def _annotation(spec: FieldSpec) -> Any:
    ann: Any
    if spec.type in ("string", "timestamp"):
        ann = Annotated[str, StringConstraints(pattern=spec.pattern, min_length=1)]
    elif spec.type == "enum":
        ann = Literal[tuple(spec.values)]
    elif spec.type == "integer":
        ann = Annotated[int, Field(ge=spec.ge, le=spec.le)]
    elif spec.type == "decimal":
        ann = Annotated[float, Field(ge=spec.ge, gt=spec.gt, le=spec.le, allow_inf_nan=False)]
    elif spec.type == "boolean":
        ann = bool
    else:  # pragma: no cover - guarded by load_contracts
        raise ValueError(f"unknown type {spec.type}")
    if spec.nullable:
        return (ann | None, Field(...))
    return (ann, Field(...))


def _build_model(entity: str, specs: list[FieldSpec]) -> type[BaseModel]:
    fields = {s.name: _annotation(s) for s in specs}
    # strict: "12.5" is not a number, 1 is not a bool; extra fields are dropped (additive evolution).
    config = ConfigDict(strict=True, extra="ignore")
    return create_model(f"{entity.title().replace('_', '')}Contract", __config__=config, **fields)


def load_contracts(path: Path = CONTRACTS_PATH) -> tuple[str, dict[str, EntityContract]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    version = str(raw["contract_version"])
    out: dict[str, EntityContract] = {}
    for name, spec in raw["entities"].items():
        specs = []
        for fname, f in spec["fields"].items():
            if f["type"] not in DUCKDB_TYPES:
                raise ValueError(f"{name}.{fname}: unknown type {f['type']}")
            specs.append(
                FieldSpec(
                    name=fname,
                    type=f["type"],
                    nullable=bool(f.get("nullable", False)),
                    values=tuple(f.get("values", ())),
                    pattern=f.get("pattern"),
                    ge=f.get("ge"),
                    gt=f.get("gt"),
                    le=f.get("le"),
                )
            )
        contract = EntityContract(
            name=name,
            producer=spec["producer"],
            primary_key=spec["primary_key"],
            fields=specs,
            timezone_by_source=dict(spec.get("timezone_by_source", {})),
        )
        contract.model = _build_model(name, specs)
        out[name] = contract
    return version, out
