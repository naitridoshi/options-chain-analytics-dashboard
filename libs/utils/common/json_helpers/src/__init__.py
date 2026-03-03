import json
from os import listdir, path


def remove_key_from_training_data(dir_path: str, key: str) -> None:
    for file_name in listdir(dir_path):
        file_path = path.join(dir_path, file_name)
        with open(file_path, "r") as file:
            data = json.load(file)

        for _dict in data:
            if key in _dict:
                del _dict[key]

        file_path = file_path.split("/")
        file_name = file_path[-1].split(".")[0]
        file_path = "/".join(file_path[:-1])
        with open(f"{file_path}/{file_name}.json", "w") as file:
            json.dump(data, file, indent=4)

    return None


def create_chunks_of_json_file(file_path: str, chunk_size: int = 50) -> None:
    """
    Splits a JSON file containing a list of dictionaries into multiple files,
    each containing a specified number of dictionaries.

    Args:
        file_path: Path to the JSON file to be chunked
        chunk_size: Number of dictionaries per output file (default: 50)

    Returns:
        None
    """
    with open(file_path, "r") as file:
        data = json.load(file)

    # To modify the data
    # for _dict in data:
    #     if "url" in _dict and (
    #         "cl/es/" in _dict["url"] or
    #         "gb/en/" in _dict["url"] or
    #         "au/en/" in _dict["url"] or
    #         "uy/en/" in _dict["url"] or
    #         "ca/en/" in _dict["url"]
    #     ):
    #         del _dict

    base_path = file_path.rsplit(".", 1)[0]

    for i in range(0, len(data), chunk_size):
        chunk = data[i : i + chunk_size]
        output_path = f"{base_path}_{i // chunk_size + 1}.json"

        with open(output_path, "w") as out_file:
            json.dump(chunk, out_file, indent=4)

    return None


def edit_key_name_in_training_data(
    dir_path: str, key: str, new_key: str
) -> None:
    for file_name in listdir(dir_path):
        file_path = path.join(dir_path, file_name)
        with open(file_path, "r") as file:
            data = json.load(file)

        for _dict in data:
            if key in _dict:
                _dict[new_key] = _dict[key]
                del _dict[key]

        file_path = file_path.split("/")
        file_name = file_path[-1].split(".")[0]
        file_path = "/".join(file_path[:-1])
        with open(f"{file_path}/{file_name}.json", "w") as file:
            json.dump(data, file, indent=4)

    return None
