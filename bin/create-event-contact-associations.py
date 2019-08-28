#!/usr/bin/env python
"""
Create event contact associations for events that don't have any.
"""

import click

from inbox.ignition import engine_manager
from nylas.logging import get_logger, configure_logging
from inbox.contacts.processing import update_contacts_from_event
from inbox.models import Event
from inbox.models.session import session_scope_by_shard_id

configure_logging()
log = get_logger(purpose='create-event-contact-associations')


def process_shard(shard_id, dry_run):
    with session_scope_by_shard_id(shard_id) as db_session:
        # NOTE: The session is implicitly autoflushed, which ensures no
        # duplicate contacts are created.
        event_query = db_session.query(Event)
        n_skipped = 0
        for n, event in enumerate(event_query):
            if n % 100 == 0:
                log.info('progress', shard_id=shard_id, n=n, n_skipped=n_skipped)

            if not event.participants or event.contacts:
                n_skipped += 1
                continue

            if not dry_run:
                event.contacts = []
                update_contacts_from_event(db_session, event, event.namespace_id)

            if n % 100 == 0:
                if not dry_run:
                    db_session.commit()
    log.info('finished', shard_id=shard_id, n=n, n_skipped=n_skipped)


@click.command()
@click.option('--shard-id', type=int, default=None)
@click.option('--dry-run', is_flag=True)
def main(shard_id, dry_run):
    if shard_id is not None:
        process_shard(shard_id, dry_run)
    else:
        for shard_id in engine_manager.engines:
            process_shard(shard_id, dry_run)

if __name__ == '__main__':
    main()
