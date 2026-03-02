from pathlib import Path
from typing import Union


def log_to_file(file_location: Union[str, Path], text: str, add_newline: bool = True) -> None:
	"""
	Append text to a log file.

	- If the file exists, content is appended.
	- If the file does not exist, it is created.

	Args:
		file_location: Full path to the log file.
		text: Text content to write.
		add_newline: When True, appends a trailing newline after text.
	"""
	path = Path(file_location)
	path.parent.mkdir(parents=True, exist_ok=True)

	message = f"{text}\n" if add_newline else text

	with path.open("a", encoding="utf-8", buffering=8192) as log_file:
		log_file.write(message)
