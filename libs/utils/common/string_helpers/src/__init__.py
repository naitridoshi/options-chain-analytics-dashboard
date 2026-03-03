def convert_to_camel_case(input_string: str):
    # Split the string by underscores
    words = input_string.split("_")
    # Capitalize the first letter of each word and join them without spaces
    camel_case_string = "".join(word.capitalize() for word in words)
    return camel_case_string
