from libs.utils.db.mongodb.src import (
    audit_logs_collection,
    calendar_collection,
    call_reports_collection,
    clients_collection,
    contact_persons_collection,
    leaves_collection,
    notes_collection,
    password_reset_tokens_collection,
    permissions_collection,
    roles_collection,
    segments_collection,
    user_relationships_collection,
    users_collection,
)
from libs.utils.db.mongodb.src.base_repository import BaseRepository

users_repository = BaseRepository(collection=users_collection, timestamps=True)
roles_repository = BaseRepository(collection=roles_collection, timestamps=True)
permissions_repository = BaseRepository(
    collection=permissions_collection, timestamps=True
)
clients_repository = BaseRepository(collection=clients_collection, timestamps=True)
calendar_repository = BaseRepository(collection=calendar_collection, timestamps=True)
call_reports_repository = BaseRepository(
    collection=call_reports_collection, timestamps=True
)
audit_logs_repository = BaseRepository(
    collection=audit_logs_collection, timestamps=True
)
user_relationships_repository = BaseRepository(
    collection=user_relationships_collection, timestamps=True
)
notes_repository = BaseRepository(collection=notes_collection, timestamps=True)
segments_repository = BaseRepository(collection=segments_collection, timestamps=True)
contact_persons_repository = BaseRepository(
    collection=contact_persons_collection, timestamps=True
)
leaves_repository = BaseRepository(collection=leaves_collection, timestamps=True)
password_reset_tokens_repository = BaseRepository(
    collection=password_reset_tokens_collection, timestamps=True
)
