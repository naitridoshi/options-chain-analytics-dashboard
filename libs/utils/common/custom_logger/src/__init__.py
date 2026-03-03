import inspect
import logging
import logging.config
import queue
import traceback
from datetime import datetime, timezone
from functools import wraps
from logging.handlers import QueueListener
from os import getcwd, path

from libs.utils.common.constants.src.custom_logger import Colors
from libs.utils.common.custom_logger.src.handlers import (
    RequestDetailsFilter,
    console_handler,
    dynamic_file_handler,
    teams_handler,
)
from libs.utils.common.custom_logger.src.helper import (
    color_string,
    get_callee_class_name,
    get_frame_info,
    get_serialized_args,
    get_serialized_kwargs,
    get_serialized_result,
    serialize_args_kwargs,
)
from libs.utils.common.date_time.src import (
    convert_ms_to_readable_format,
    get_execution_time_in_seconds,
)
from libs.utils.common.enums.src.custom_logger import LogType
from libs.utils.config.src.teams import MS_TEAMS_WEBHOOK_ENABLED


class CustomLogger:
    def __init__(
        self,
        logger_name: str,
        queue_logger: bool = True,
        is_request: bool = True,
    ):
        self.logger_name = logger_name
        self.queue_logger = queue_logger
        self.is_request = is_request
        if queue_logger:
            self.root_logger, self.root_listener = self.get_logger()
        else:
            self.root_logger = self.get_logger()

    def get_logger(
        self,
        enable_console_handler: bool = True,
        enable_files_handler: bool = True,
    ):
        logger = logging.getLogger(self.logger_name)

        logger.setLevel(logging.DEBUG)

        request_id_filter = RequestDetailsFilter(is_request=self.is_request)
        logger.addFilter(request_id_filter)

        # Remove all handlers associated with the logger
        if logger.hasHandlers():
            logger.handlers.clear()

        _handlers = []
        _handlers.append(console_handler) if enable_console_handler else None
        _handlers.append(dynamic_file_handler) if enable_files_handler else None
        _handlers.append(teams_handler) if MS_TEAMS_WEBHOOK_ENABLED else None

        if self.queue_logger:
            log_queue = queue.Queue()
            queue_handler = logging.handlers.QueueHandler(log_queue)
            logger.addHandler(queue_handler)

            listener = QueueListener(
                log_queue, *_handlers, respect_handler_level=True
            )
            return logger, listener

        for _handler in _handlers:
            logger.addHandler(_handler)

        return logger

    def log_function_call(
        self,
        frame_info: traceback,
        function,
        args: dict,
        kwargs: dict,
        callee_class_name: str,
    ):
        class_name = None
        qual_name = function.__qualname__.split(".")
        if len(qual_name) > 1:
            class_name = qual_name[0]
        extra = {
            "logType": LogType.FUNCTION_INVOKE.value,
            "calleeFunctionName": frame_info.function,
            "calleeClassName": callee_class_name,
            "arguments": args,
            "kwargs": kwargs,
            "fileName": frame_info.filename.split("/")[-1],
            "filePath": path.relpath(frame_info.filename, getcwd()),
            "line": frame_info.lineno,
            "column": frame_info.positions.col_offset,
            "className": class_name,
            "functionName": function.__name__,
            "qualname": function.__qualname__,
            "extraDetails": f"args: {get_serialized_args(args)}, "
            f"kwargs: {get_serialized_kwargs(kwargs)}",
        }
        self.root_logger.debug("Function called...", extra=extra)

    def log_function_error(
        self,
        frame_info: traceback,
        function,
        start_time: datetime,
        error: Exception,
        callee_class_name: str,
    ):
        execution_time_ms = get_execution_time_in_seconds(start_time) * 1000
        execution_time = color_string(
            f"({convert_ms_to_readable_format(execution_time_ms)})",
            Colors.BOLD_GOLD,
        )
        class_name = None
        qual_name = function.__qualname__.split(".")
        if len(qual_name) > 1:
            class_name = qual_name[0]
        extra = {
            "logType": LogType.FUNCTION_RETURN.value,
            "calleeFunctionName": frame_info.function,
            "calleeClassName": callee_class_name,
            "fileName": frame_info.filename.split("/")[-1],
            "filePath": path.relpath(frame_info.filename, getcwd()),
            "line": frame_info.lineno,
            "column": frame_info.positions.col_offset,
            "className": class_name,
            "functionName": function.__name__,
            "qualname": function.__qualname__,
            "errorStack": str(traceback.extract_tb(error.__traceback__)),
            "executionTimeMs": execution_time_ms,
            "executionTime": execution_time,
            "extraDetails": f"{execution_time}, {error}",
        }
        self.root_logger.error(f"{error}", extra=extra)

    def log_function_return(
        self, frame_info: traceback, function, start_time: datetime, result: any
    ):
        execution_time_ms = get_execution_time_in_seconds(start_time) * 1000
        execution_time = color_string(
            f"({convert_ms_to_readable_format(execution_time_ms)})",
            Colors.BOLD_GOLD,
        )
        class_name = None
        qual_name = function.__qualname__.split(".")
        if len(qual_name) > 1:
            class_name = qual_name[0]

        serialized_result = get_serialized_result(result)

        extra = {
            "logType": LogType.FUNCTION_RETURN.value,
            "fileName": frame_info.filename.split("/")[-1],
            "filePath": path.relpath(frame_info.filename, getcwd()),
            "line": frame_info.lineno,
            "column": frame_info.positions.col_offset,
            "className": class_name,
            "functionName": function.__name__,
            "returns": result,
            "executionTimeMs": execution_time_ms,
            "executionTime": execution_time,
            "qualname": function.__qualname__,
            "extraDetails": f"{execution_time}, returns: {serialized_result}",
        }
        self.root_logger.debug("Function returned", extra=extra)

    def track(self, func):
        if inspect.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args, **kwargs):
                start_time = datetime.now(timezone.utc)

                params = inspect.signature(func).parameters

                args_dict, kwargs_dict = serialize_args_kwargs(
                    args, kwargs, params
                )

                callee_class_name = get_callee_class_name(inspect)

                self.log_function_call(
                    get_frame_info(inspect),
                    func,
                    args_dict,
                    kwargs_dict,
                    callee_class_name,
                )

                frame_info = get_frame_info(inspect)
                try:
                    result = await func(*args, **kwargs)
                except Exception as error:
                    print(
                        color_string(traceback.format_exc(), Colors.BRIGHT_RED)
                    )
                    self.log_function_error(
                        frame_info, func, start_time, error, callee_class_name
                    )
                    raise

                self.log_function_return(frame_info, func, start_time, result)
                return result

            return async_wrapper

        else:

            @wraps(func)
            def sync_wrapper(*args, **kwargs):
                start_time = datetime.now(timezone.utc)

                params = inspect.signature(func).parameters

                args_dict, kwargs_dict = serialize_args_kwargs(
                    args, kwargs, params
                )

                callee_class_name = get_callee_class_name(inspect)

                self.log_function_call(
                    get_frame_info(inspect),
                    func,
                    args_dict,
                    kwargs_dict,
                    callee_class_name,
                )

                frame_info = get_frame_info(inspect)

                try:
                    result = func(*args, **kwargs)
                except Exception as error:
                    print(
                        color_string(traceback.format_exc(), Colors.BRIGHT_RED)
                    )
                    self.log_function_error(
                        frame_info, func, start_time, error, callee_class_name
                    )
                    raise

                self.log_function_return(frame_info, func, start_time, result)
                return result

            return sync_wrapper


if __name__ == "__main__":
    log = CustomLogger("test", queue_logger=False, is_request=False)
    test_logger = log.get_logger()
    test_logger.info("Test")
