import os

import pandas as pd

from libs.utils.common.custom_logger.src import CustomLogger

log = CustomLogger("ExcelHelpers", is_request=False, queue_logger=False)

logger = log.get_logger()


def convert_excel_to_json(excel_file_path: str, output_dir: str = None):
    """
    Convert all sheets in an Excel file to separate JSON files.

    Parameters:
    excel_file_path (str): Path to the Excel file.
    output_dir (str): Directory where the JSON files will be saved.

    Returns:
    None
    """
    # Determine output directory if not provided
    if output_dir is None:
        base_name = os.path.splitext(os.path.basename(excel_file_path))[0]
        output_dir = os.path.join(os.path.dirname(excel_file_path), base_name)

    # Load the Excel file
    try:
        excel_data = pd.read_excel(excel_file_path, sheet_name=None)
    except Exception as e:
        logger.error(f"Error reading the Excel file: {e}")
        return

    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)

    # Loop through each sheet and save it as a JSON file
    for sheet_name, data in excel_data.items():
        # Define the output file path
        output_file = os.path.join(output_dir, f"{sheet_name}.json")

        try:
            # Convert DataFrame to JSON and save to file
            data.to_json(output_file, orient="records", indent=4)
            logger.info(f"Saved sheet '{sheet_name}' to '{output_file}'")

        except Exception as e:
            logger.error(f"Error saving sheet '{sheet_name}' to JSON: {e}")


if __name__ == "__main__":
    convert_excel_to_json("data.xlsx")
