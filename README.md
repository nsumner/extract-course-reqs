# extract-course-reqs

A Python tool for extracting requirement information from the SFU program calendar.

## Overview

This tool scrapes the SFU calendar for CMPT courses and their requirements.
It extracts a structured (DNF) representation of:

* prerequisites
* corequisites
* antirequisites
* non-course requirements

and saves them to a JSON file (`cmpt-dependencies.json`) for consumption by other tools.


## Installation

The project is managed by [`uv`](https://docs.astral.sh/uv/) for self-contained
dependencies. Install into the project directory with:

```bash
uv sync
uv pip install -e .
```


## Usage

Run the extraction:

```bash
uv run extract-course-reqs
```

which will output structured data to `cmpt-dependencies.json` by default.


Additional options allow setting the `--year`, `--term`, `--output` file path.
By default, information from the SFU Calendar is cached, processed, and reused,
but setting the `--year` or `--term` (or using the `--force` option) will 
refresh the calendar information.

The `--extract-calendar` option exracts the SFU calendar in markdown format
to `calendar.md` without computing dependencies. This can be useful just to
have a clean working copy of the calendar:

```bash
uv run extract-course-reqs --extract-calendar --year 2021 --output some/other/path/2021-calendar.md
```




### Output Structure

The `cmpt_dependencies.json` file contains an array of course objects with:

- `course`: Course code (e.g., `"CMPT 125"`)
- `title`: Course title
- `prereqs`: DNF list of prerequisite courses (e.g., `[["CMPT 120"], ["CMPT 130"]]`)
- `coreqs`: DNF list of corequisite courses
- `antireqs`: List of antirequisite courses
- `noncourse`: Non-course-related requirements (e.g., "minimum grade of C-")

## Example

A prerequisite like:

> "(CMPT 125 or CMPT 135) and MACM 101"

Is normalized to DNF form within the results:

```json
  {
    "course": "CMPT 201",
    "title": "Systems Programming (4)",
    "prereqs": [
      [
        "CMPT 125",
        "MACM 101"
      ],
      [
        "CMPT 135",
        "MACM 101"
      ]
    ],
    "coreqs": [],
    "noncourse": [],
    "antireqs": [
      "CMPT 300"
    ]
  }
```

## Development

### Linting, Type Checking, and Testing

```bash
uv run ruff check
uv run ruff format
uv run mypy .
uv run pytest
```

## License

MIT
