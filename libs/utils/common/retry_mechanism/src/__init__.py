from libs.utils.common.custom_logger.src import CustomLogger

log = CustomLogger("RetryMechanism")

logger, listener = log.get_logger()
listener.start()


def retry_function(func, retries=3, *args, **kwargs):
    """
    Attempts to execute a function with the given arguments, retrying up to a
    specified number of times if an exception is raised.

    :param func: The function to execute.
    :param args: Positional arguments to pass to the function.
    :param retries: The number of times to retry the function if an exception
    is raised.
    :param kwargs: Keyword arguments to pass to the function.
    :return: The result of the function if successful.
    :raises Exception: The last exception raised if all retries fail.
    """
    attempt = 0
    while attempt < retries:
        try:
            return func(*args, **kwargs)
        except Exception as e:
            attempt += 1
            if attempt == retries:
                logger.error(
                    f"{retries} attempts failed to execute {func.__name__} "
                    f"with error: {e}"
                )
                raise e
