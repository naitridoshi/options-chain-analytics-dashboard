import json
import os
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from json import dumps
from os import getcwd, path
from pathlib import Path
from uuid import UUID

from pydantic import BaseModel
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Match

from libs.utils.common.constants.src.custom_logger import Colors
from libs.utils.common.date_time.src import (
    convert_ms_to_readable_format,
    get_execution_time_in_seconds,
)
from libs.utils.common.enums.src.custom_logger import LogType


def color_string(message, color: Colors = Colors.CYAN):
    return f"{color.value}{str(message)}{Colors.RESET.value}"


def extra_details_for_req(
    inspect,
    cls_name,
    request: Request = None,
    request_body=None,
    response: Response = None,
    response_body=None,
    start_time: datetime = None,
    sendInTeams: bool = False,
):
    frame = inspect.currentframe().f_back
    frame_info = inspect.getframeinfo(frame)

    extra = {
        "fileName": frame_info.filename.split("/")[-1],
        "filePath": path.relpath(frame_info.filename, getcwd()),
        "line": frame_info.lineno,
        "column": frame_info.positions.col_offset,
        "functionName": frame_info.function,
        "className": cls_name,
        "qualname": f"{cls_name}.{frame_info.function}",
        "sendInTeams": sendInTeams,
    }
    if request:
        routes = request.app.router.routes
        path_params = dict()
        query_params = dict()
        for route in routes:
            match, scope = route.matches(request)
            if match == Match.FULL:
                path_params = scope.get("path_params")
                query_params = scope.get("query_params")

        args = request_body.copy()
        if "assistant_name" in args:
            args.pop("assistant_name")
        if "user_id" in args:
            args.pop("user_id")

        req_data = {
            "logType": LogType.REQUEST_INIT.value,
            "extraDetails": f"'{request.method} {request.url.path}', {args}",
            "request": {
                "method": request.method,
                "path": request.url.path,
                "headers": dict(request.headers),
                "body": dict(request_body),
                "params": path_params,
                "query": query_params,
                "calleeDetails": {
                    "host": request.client.host,
                    "port": request.client.port,
                },
            },
        }
        extra.update(req_data)

    if response:
        execution_time_ms = get_execution_time_in_seconds(start_time) * 1000
        execution_time = color_string(
            f"({convert_ms_to_readable_format(execution_time_ms)})",
            Colors.BOLD_GOLD,
        )
        res_data = {
            "logType": LogType.REQUEST_END.value,
            "extraDetails": f"{response.status_code} {execution_time}",
            "response": {
                "statusCode": response.status_code,
                "headers": dict(response.headers),
                "body": response_body,
                "executionTimeMs": execution_time_ms,
                "executionTime": execution_time,
            },
        }
        extra.update(res_data)

    return extra


def get_serialized_args(args: any):
    try:
        if isinstance(args, dict):
            return {
                key: (
                    {
                        k: v.value
                        for k, v in value.model_dump().items()
                        if isinstance(v, Enum)
                    }
                    if isinstance(value, BaseModel)
                    else value
                )
                for key, value in args.items()
                if key not in {"assistant_name", "user_id"}
            }
        elif isinstance(args, list):
            return dumps(args)
    except Exception as e:
        print(f"Error while serializing args: {e}")
        return str(args)


def get_serialized_kwargs(kwargs: any):
    def remove_sensitive_keys(data):
        if isinstance(data, dict):
            return {
                key: remove_sensitive_keys(value)
                for key, value in data.items()
                if key not in {"assistant_name", "user_id"}
            }
        return data

    try:
        if isinstance(kwargs, dict):
            return {
                key: (
                    value.model_dump()
                    if isinstance(value, BaseModel)
                    else remove_sensitive_keys(value)
                )
                for key, value in kwargs.items()
                if key not in ["assistant_name", "user_id"]
            }
        elif isinstance(kwargs, list):
            return dumps(kwargs)
    except Exception as e:
        print(f"Error while serializing kwargs: {e}")
        return str(kwargs)


# --- knobs you can tune ---
_MAX_DEPTH = 4
_MAX_ITEMS_PER_CONTAINER = 200
_MAX_STRING_LENGTH = 5000
_SAMPLE_ITEMS = 3
# redact these keys anywhere they appear
_REDACT_KEYS = {
    "password",
    "hashed_password",
    "access_token",
    "refresh_token",
    "secret",
    "token",
}
# keys we’ll auto-summarize (count + sample) when they’re large lists
_SUMMARIZE_KEYS = {"user_details", "wallets", "rules", "transactions"}

# minimal field allowlist we’ll try to include from ORM objects
_SAFE_ORM_FIELDS = {
    "id",
    "email",
    "role",
    "name",
    "type",
    "balance",
    "is_active",
    "user_id",
    "organization_id",
    "created_at",
    "updated_at",
    "active_wallet_id",
}


def _is_sa_model(obj) -> bool:
    # cheap check; avoids importing sqlalchemy in your logger
    return hasattr(obj, "_sa_instance_state") or hasattr(obj, "__mapper__")


def _orm_minimal(obj):
    """Return a small, safe snapshot for a SQLAlchemy model."""
    out = {"<type>": obj.__class__.__name__}
    # try to read common fields without triggering lazy loads
    for k in _SAFE_ORM_FIELDS:
        if hasattr(obj, k):
            try:
                v = getattr(obj, k)
            except Exception:
                continue
            out[k] = _to_json_safe(v, _depth=1)  # shallow
    # always include primary key-ish if available
    if "id" not in out:
        # best-effort string repr as last resort
        out["id"] = getattr(obj, "id", repr(obj))
    return out


def _redact_key(k: str, v):
    if k.lower() in _REDACT_KEYS:
        return "***redacted***"
    return v


def _truncate_str(s: str) -> str:
    return s if len(s) <= _MAX_STRING_LENGTH else s[:_MAX_STRING_LENGTH] + "…"


def _to_json_safe(obj, *, _seen=None, _path="$", _depth=0):
    if _seen is None:
        _seen = {}

    # depth cap
    if _depth > _MAX_DEPTH:
        return f"<max_depth at {_path}>"

    # primitives
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj
    if isinstance(obj, str):
        return _truncate_str(obj)

    oid = id(obj)
    if oid in _seen:
        return {"$ref": _seen[oid]}
    _seen[oid] = _path

    # common coercions
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, bytes):
        try:
            return _truncate_str(obj.decode("utf-8"))
        except Exception:
            return obj.hex()
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, BaseModel):
        return _to_json_safe(
            obj.model_dump(),
            _seen=_seen,
            _path=f"{_path}<pydantic>",
            _depth=_depth + 1,
        )
    if isinstance(obj, JSONResponse):
        body = obj.body
        if isinstance(body, (bytes, bytearray)):
            try:
                body = body.decode("utf-8")
                try:
                    body = json.loads(body)
                except Exception:
                    pass
            except Exception:
                body = body.hex()
        return _to_json_safe(
            body, _seen=_seen, _path=f"{_path}<JSONResponse>", _depth=_depth + 1
        )

    # SQLAlchemy model → minimal snapshot
    if _is_sa_model(obj):
        return _orm_minimal(obj)

    # mappings
    if isinstance(obj, Mapping):
        out = {}
        count = 0
        for k, v in obj.items():
            if count >= _MAX_ITEMS_PER_CONTAINER:
                out["<truncated>"] = (
                    f"only first {_MAX_ITEMS_PER_CONTAINER} items logged"
                )
                break
            key = str(k)
            if (
                key in _SUMMARIZE_KEYS
                and isinstance(v, Iterable)
                and not isinstance(v, (str, bytes, bytearray, Mapping))
            ):
                # summarize large collections with count + sample
                v_list = list(v)
                summary = {
                    "count": len(v_list),
                    "sample": [
                        _to_json_safe(
                            x,
                            _seen=_seen,
                            _path=f"{_path}.{key}[{i}]",
                            _depth=_depth + 1,
                        )
                        for i, x in enumerate(v_list[:_SAMPLE_ITEMS])
                    ],
                }
                out[key] = summary
            else:
                safe_v = _to_json_safe(
                    v, _seen=_seen, _path=f"{_path}.{key}", _depth=_depth + 1
                )
                out[key] = _redact_key(key, safe_v)
            count += 1
        return out

    # iterables (lists/tuples/sets/…)
    if isinstance(obj, Iterable):
        out_list = []
        for idx, item in enumerate(obj):
            if idx >= _MAX_ITEMS_PER_CONTAINER:
                out_list.append(f"<truncated: first {_MAX_ITEMS_PER_CONTAINER} items>")
                break
            # if the list looks like ORM rows, produce a summarized list
            if _is_sa_model(item):
                # summarize entire list: count + sample
                # convert current iterable to list once
                seq = list(obj)
                return {
                    "count": len(seq),
                    "sample": [
                        _to_json_safe(
                            x,
                            _seen=_seen,
                            _path=f"{_path}[{i}]",
                            _depth=_depth + 1,
                        )
                        for i, x in enumerate(seq[:_SAMPLE_ITEMS])
                    ],
                }
            out_list.append(
                _to_json_safe(
                    item,
                    _seen=_seen,
                    _path=f"{_path}[{idx}]",
                    _depth=_depth + 1,
                )
            )
        return out_list

    # objects with __dict__ → shallow snapshot without private/SQLA internals
    if hasattr(obj, "__dict__") and not _is_sa_model(obj):
        data = {"<type>": obj.__class__.__name__}
        try:
            attrs = {
                k: v
                for k, v in vars(obj).items()
                if not k.startswith("_sa_") and not k.startswith("_")
            }
        except Exception:
            attrs = {}
        for i, (k, v) in enumerate(attrs.items()):
            if i >= _MAX_ITEMS_PER_CONTAINER:
                data["<truncated>"] = (
                    f"only first {_MAX_ITEMS_PER_CONTAINER} attrs logged"
                )
                break
            data[str(k)] = _to_json_safe(
                v, _seen=_seen, _path=f"{_path}.{k}", _depth=_depth + 1
            )
        return data

    # fallback
    return repr(obj)


def get_serialized_result(result) -> str:
    try:
        safe_obj = _to_json_safe(result)
        return json.dumps(safe_obj, ensure_ascii=False)
    except Exception as e:
        return f"<unserializable: {e!r}>"


def serialize_value(value):
    """Helper function to serialize a value for logging."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseModel):
        # Serialize BaseModel by converting Enum fields to their values
        return {
            k: (v.value if isinstance(v, Enum) else v)
            for k, v in value.model_dump().items()
        }
    if hasattr(value, "dict"):
        return value.dict()
    if isinstance(value, dict):
        return {k: (v.value if isinstance(v, Enum) else v) for k, v in value.items()}
    try:
        dumps({value})
        return value
    except TypeError:
        return str(value)


def serialize_args_kwargs(args, kwargs, params):
    """Serialize function arguments and keyword arguments."""
    args_dict = {
        param: serialize_value(arg)
        for param, arg in zip(params, args)
        if param != "self" and param != "cls"
    }
    kwargs_dict = {key: serialize_value(value) for key, value in kwargs.items()}
    return args_dict, kwargs_dict


def get_callee_class_name(inspect):
    return (
        inspect.stack()[1][0].f_locals["self"].__class__.__name__
        if "self" in inspect.stack()[1][0].f_locals
        else None
    )


def get_frame_info(inspect):
    return inspect.getframeinfo(inspect.currentframe().f_back.f_back)


def convert_logfile_to_json(log_file_path: str):
    with open(log_file_path, "r") as file:
        log_data = file.readlines()

    json_data = [json.loads(line) for line in log_data if line.strip()]

    keys_to_remove = [
        "msg",
        "args",
        "pathname",
        "filename",
        "levelno",
        "levelname",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "taskName",
    ]

    for log in json_data:
        for key in keys_to_remove:
            if key in log:
                del log[key]

    output_json_path = os.path.splitext(log_file_path)[0] + ".json"

    with open(output_json_path, "w") as json_file:
        json.dump(json_data, json_file, indent=4)

    return json_data


if __name__ == "__main__":
    convert_logfile_to_json("logs/TOURISM_ASSISTANT.log.1")
