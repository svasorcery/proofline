"""Case and source changes are serialised by one aggregate row lock."""

import hashlib

from sqlalchemy import func, select
from sqlalchemy.orm.attributes import flag_modified

from proofline.application.commands import (
    CaseCreate,
    CasePatch,
    SourceCreate,
    SourcePatch,
    SourceVersionCreate,
)
from proofline.application.errors import ApplicationError
from proofline.application.read_models import case_detail, case_summary, case_summary_query
from proofline.application.state import (
    case_or_404,
    check_limits,
    current_input,
    read_session,
    require_version,
    touch,
)
from proofline.application.views import CaseDetail, CasePage, CaseStatus, SourceVersionView
from proofline.config import Settings
from proofline.domain.inputs import AnalysisInput, json_digest
from proofline.persistence.database import Database
from proofline.persistence.models import Case, Source, SourceVersion, new_id


def _input_digest(case: Case) -> str:
    return json_digest(
        {
            "user_text": case.user_text,
            "requested_fact_keys": case.requested_fact_keys,
            "money": case.money,
            "context": case.context,
        }
    )


def _source_state(sources: list[Source]) -> list[tuple[object, ...]]:
    return [
        (
            s.source_id,
            s.kind,
            s.locator,
            s.title,
            s.trust,
            s.position,
            s.included,
            s.current_version_id,
        )
        for s in sources
    ]


class CaseService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def list(self, status: CaseStatus | None = None, limit: int = 50, offset: int = 0) -> CasePage:
        if not 1 <= limit <= 100 or offset < 0:
            raise ApplicationError("INVALID_REQUEST", "Invalid pagination.", status_code=422)
        with read_session(self.database) as session:
            query = case_summary_query(self.settings.validation_profile)
            if status is not None:
                query = query.where(query.selected_columns.display_status == status)
            total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
            rows = session.execute(
                query.order_by(Case.updated_at.desc(), Case.id).limit(limit).offset(offset)
            )
            return CasePage(
                items=[case_summary(case, state, running) for case, state, running in rows],
                total=total,
                limit=limit,
                offset=offset,
            )

    def get(self, case_id: str) -> CaseDetail:
        with read_session(self.database) as session:
            return case_detail(
                session, case_or_404(session, case_id), self.settings.validation_profile
            )

    def create(self, command: CaseCreate) -> CaseDetail:
        case_id = new_id()
        check_limits(
            AnalysisInput(
                case_id=case_id,
                user_text=command.user_text,
                requested_fact_keys=tuple(command.requested_fact_keys),
                money=command.money,
                context=command.context,
                catalog=(),
            ),
            self.settings,
        )
        with self.database.sessions.begin() as session:
            case = Case(
                id=case_id,
                title=command.title,
                user_text=command.user_text,
                requested_fact_keys=list(command.requested_fact_keys),
                context=command.context,
                money=command.money.model_dump(mode="json") if command.money else None,
                version=1,
                content_revision=1,
            )
            session.add(case)
            session.flush()
            return case_detail(session, case, self.settings.validation_profile)

    def update(self, case_id: str, command: CasePatch) -> CaseDetail:
        with self.database.sessions.begin() as session:
            case = case_or_404(session, case_id, lock=True)
            require_version(case, command.expected_case_version)
            previous_input, previous_title = _input_digest(case), case.title
            if command.title is not None:
                case.title = command.title
            if command.user_text is not None:
                case.user_text = command.user_text
            if command.requested_fact_keys is not None:
                case.requested_fact_keys = list(command.requested_fact_keys)
            if command.context is not None and json_digest(command.context) != json_digest(
                case.context
            ):
                case.context = command.context
                flag_modified(
                    case, "context"
                )  # Python considers {"x": 1} == {"x": True}; JSON does not.
            if "money" in command.model_fields_set:
                money = command.money.model_dump(mode="json") if command.money else None
                if json_digest(money) != json_digest(case.money):
                    case.money = money
                    flag_modified(case, "money")
            changed_input = _input_digest(case) != previous_input
            if changed_input or case.title != previous_title:
                touch(case, content=changed_input)
                check_limits(current_input(session, case), self.settings)
            session.flush()
            return case_detail(session, case, self.settings.validation_profile)

    def _text(self, value: str) -> tuple[str, str]:
        text = value.replace("\r\n", "\n").replace("\r", "\n")
        if len(text) > self.settings.max_source_characters:
            raise ApplicationError(
                "INPUT_LIMIT_EXCEEDED",
                "Source text exceeds the configured limit.",
                status_code=413,
                details={"field": "text", "limit": self.settings.max_source_characters},
            )
        return text, hashlib.sha256(text.encode("utf-8")).hexdigest()

    def add_source(self, case_id: str, command: SourceCreate) -> CaseDetail:
        with self.database.sessions.begin() as session:
            case = case_or_404(session, case_id, lock=True)
            require_version(case, command.expected_case_version)
            count = (
                session.scalar(
                    select(func.count()).select_from(Source).where(Source.case_id == case_id)
                )
                or 0
            )
            if count >= self.settings.max_sources:
                raise ApplicationError(
                    "INPUT_LIMIT_EXCEEDED",
                    "Too many sources.",
                    status_code=413,
                    details={"field": "sources", "limit": self.settings.max_sources},
                )
            if session.scalar(
                select(Source.source_id).where(
                    Source.case_id == case_id, Source.locator == command.locator
                )
            ):
                raise ApplicationError(
                    "SOURCE_LOCATOR_CONFLICT", "This locator is already registered."
                )
            included = (
                command.included if command.included is not None else command.text is not None
            )
            if included and command.text is None:
                raise ApplicationError(
                    "SOURCE_TEXT_REQUIRED",
                    "Add a text version before including this source.",
                    status_code=422,
                )
            source = Source(
                case_id=case_id,
                source_id=new_id(),
                kind=command.kind,
                locator=command.locator,
                title=command.title,
                trust=command.trust,
                position=count,
                included=included,
            )
            session.add(source)
            session.flush()
            if command.text is not None:
                text, digest = self._text(command.text)
                version = SourceVersion(
                    case_id=case_id,
                    source_id=source.source_id,
                    sequence=1,
                    text=text,
                    text_sha256=digest,
                )
                session.add(version)
                session.flush()
                source.current_version_id = version.id
            touch(case, content=True)
            check_limits(current_input(session, case), self.settings)
            session.flush()
            return case_detail(session, case, self.settings.validation_profile)

    def add_version(self, case_id: str, source_id: str, command: SourceVersionCreate) -> CaseDetail:
        text, digest = self._text(command.text)
        with self.database.sessions.begin() as session:
            case = case_or_404(session, case_id, lock=True)
            require_version(case, command.expected_case_version)
            source = session.get(Source, (case_id, source_id))
            if source is None:
                raise ApplicationError("SOURCE_NOT_FOUND", "Source not found.", status_code=404)
            last = (
                session.scalar(
                    select(func.max(SourceVersion.sequence)).where(
                        SourceVersion.case_id == case_id, SourceVersion.source_id == source_id
                    )
                )
                or 0
            )
            version = SourceVersion(
                case_id=case_id,
                source_id=source_id,
                sequence=last + 1,
                text=text,
                text_sha256=digest,
            )
            session.add(version)
            session.flush()
            source.current_version_id = version.id
            touch(case, content=True)
            check_limits(current_input(session, case), self.settings)
            session.flush()
            return case_detail(session, case, self.settings.validation_profile)

    def update_source(self, case_id: str, source_id: str, command: SourcePatch) -> CaseDetail:
        with self.database.sessions.begin() as session:
            case = case_or_404(session, case_id, lock=True)
            require_version(case, command.expected_case_version)
            sources = list(
                session.scalars(
                    select(Source).where(Source.case_id == case_id).order_by(Source.position)
                )
            )
            previous_state = _source_state(sources)
            source = next((item for item in sources if item.source_id == source_id), None)
            if source is None:
                raise ApplicationError("SOURCE_NOT_FOUND", "Source not found.", status_code=404)
            if command.locator is not None:
                if any(s.locator == command.locator and s.source_id != source_id for s in sources):
                    raise ApplicationError(
                        "SOURCE_LOCATOR_CONFLICT", "This locator is already registered."
                    )
                source.locator = command.locator
            if command.kind is not None:
                source.kind = command.kind
            if command.title is not None:
                source.title = command.title
            if command.trust is not None:
                source.trust = command.trust
            if command.current_version_id is not None:
                version = session.scalar(
                    select(SourceVersion).where(
                        SourceVersion.id == command.current_version_id,
                        SourceVersion.case_id == case_id,
                        SourceVersion.source_id == source_id,
                    )
                )
                if version is None:
                    raise ApplicationError(
                        "SOURCE_VERSION_NOT_FOUND",
                        "Version not found for this source.",
                        status_code=404,
                    )
                source.current_version_id = version.id
            if command.included is not None:
                source.included = command.included
            if source.included and source.current_version_id is None:
                raise ApplicationError(
                    "SOURCE_TEXT_REQUIRED",
                    "Add a text version before including this source.",
                    status_code=422,
                )
            if command.position is not None:
                if command.position >= len(sources):
                    raise ApplicationError(
                        "INVALID_REQUEST",
                        "Source position is outside the catalogue.",
                        status_code=422,
                    )
                sources.remove(source)
                sources.insert(command.position, source)
                for position, item in enumerate(sources):
                    item.position = position
            if _source_state(sources) != previous_state:
                touch(case, content=True)
                check_limits(current_input(session, case), self.settings)
            session.flush()
            return case_detail(session, case, self.settings.validation_profile)

    def get_version(self, case_id: str, source_id: str, version_id: str) -> SourceVersionView:
        with read_session(self.database) as session:
            version = session.scalar(
                select(SourceVersion).where(
                    SourceVersion.id == version_id,
                    SourceVersion.case_id == case_id,
                    SourceVersion.source_id == source_id,
                )
            )
            if version is None:
                raise ApplicationError(
                    "SOURCE_VERSION_NOT_FOUND", "Source version not found.", status_code=404
                )
            return SourceVersionView.model_validate(version)
