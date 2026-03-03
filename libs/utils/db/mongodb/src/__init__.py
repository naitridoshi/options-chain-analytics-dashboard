from pymongo import MongoClient
from pymongo.errors import CollectionInvalid, PyMongoError

from libs.utils.common.constants.src.db_collections import (
    AUDIT_LOGS_COLLECTION,
    CALENDAR_COLLECTION,
    CALL_REPORTS_COLLECTION,
    CLIENTS_COLLECTION,
    CONTACT_PERSONS_COLLECTION,
    LEAVES_COLLECTION,
    NOTES_COLLECTION,
    PASSWORD_RESET_TOKENS_COLLECTION,
    PERMISSIONS_COLLECTION,
    ROLES_COLLECTION,
    SEGMENTS_COLLECTION,
    USER_RELATIONSHIPS_COLLECTION,
    USERS_COLLECTION,
)
from libs.utils.config.src.mongodb import MONGO_DATABASE_NAME, MONGO_URI


def connect_db(db_name: str):
    try:
        client = MongoClient(MONGO_URI)
        return client[db_name]
    except PyMongoError as error:
        raise Exception(
            f'Failed to connect to database: "{db_name}",'
            f"ERROR: {str(error)}"
        )


tacb_db = connect_db(MONGO_DATABASE_NAME)

users_collection = tacb_db[USERS_COLLECTION]
roles_collection = tacb_db[ROLES_COLLECTION]
permissions_collection = tacb_db[PERMISSIONS_COLLECTION]
clients_collection = tacb_db[CLIENTS_COLLECTION]
calendar_collection = tacb_db[CALENDAR_COLLECTION]
call_reports_collection = tacb_db[CALL_REPORTS_COLLECTION]
audit_logs_collection = tacb_db[AUDIT_LOGS_COLLECTION]
user_relationships_collection = tacb_db[USER_RELATIONSHIPS_COLLECTION]
notes_collection = tacb_db[NOTES_COLLECTION]
segments_collection = tacb_db[SEGMENTS_COLLECTION]
contact_persons_collection = tacb_db[CONTACT_PERSONS_COLLECTION]
leaves_collection = tacb_db[LEAVES_COLLECTION]
password_reset_tokens_collection = tacb_db[PASSWORD_RESET_TOKENS_COLLECTION]

names = [
    USERS_COLLECTION,
    ROLES_COLLECTION,
    PERMISSIONS_COLLECTION,
    CLIENTS_COLLECTION,
    CALENDAR_COLLECTION,
    CALL_REPORTS_COLLECTION,
    AUDIT_LOGS_COLLECTION,
    USER_RELATIONSHIPS_COLLECTION,
    NOTES_COLLECTION,
    SEGMENTS_COLLECTION,
    CONTACT_PERSONS_COLLECTION,
    LEAVES_COLLECTION,
    PASSWORD_RESET_TOKENS_COLLECTION,
]

existing = set(tacb_db.list_collection_names())
for name in names:
    if name not in existing:
        try:
            tacb_db.create_collection(name)
        except CollectionInvalid:
            pass
