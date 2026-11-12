"""Explicit, idempotent seed; existing operator-owned cases are never rewritten."""

from sqlalchemy.dialects.postgresql import insert

from proofline.persistence.database import Database
from proofline.persistence.models import Case, Source, SourceVersion, new_id
from proofline.providers.replay import ReplayCatalog


def seed_demo(database: Database, catalog: ReplayCatalog) -> list[str]:
    created = []
    with database.sessions.begin() as session:
        for seed in catalog.cases:
            value = seed.input
            inserted = session.scalar(
                insert(Case)
                .values(
                    id=value.case_id,
                    title=seed.title,
                    user_text=value.user_text,
                    requested_fact_keys=list(value.requested_fact_keys),
                    money=value.money.model_dump(mode="json") if value.money is not None else None,
                    context=value.context,
                    version=1,
                    content_revision=1,
                )
                .on_conflict_do_nothing(index_elements=[Case.id])
                .returning(Case.id)
            )
            if inserted is None:
                continue
            for entry in value.catalog:
                version = entry.version()
                version_id = new_id()
                session.add(
                    Source(
                        case_id=value.case_id,
                        source_id=entry.source_id,
                        kind=entry.kind,
                        locator=entry.locator,
                        title=entry.title,
                        trust=entry.trust,
                        position=entry.position,
                        included=True,
                        current_version_id=version_id,
                    )
                )
                session.flush()
                session.add(
                    SourceVersion(
                        id=version_id,
                        case_id=value.case_id,
                        source_id=entry.source_id,
                        sequence=1,
                        text=version.text,
                        text_sha256=version.text_sha256,
                    )
                )
            session.flush()
            created.append(value.case_id)
    return created
