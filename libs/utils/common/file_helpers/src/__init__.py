from os.path import exists


def read_file(file_path: str):
    return open(file_path, "r").read()


def check_file_or_directory_exists(path: str):
    if not exists(path):
        raise FileNotFoundError(f"{path} not found")
    return True
