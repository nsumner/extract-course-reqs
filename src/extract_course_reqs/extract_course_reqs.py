import argparse
import datetime
import json
import re
from collections import deque
from collections.abc import Generator
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol

import requests
from bs4 import BeautifulSoup
from bs4.element import Tag

BASE = "https://www.sfu.ca"
CALENDAR = "/students/calendar/{year}/{term}/courses/"
PAGE = "{program}.html"

# Timeout for requests to the calendar web page...
_TIMEOUT = 10

# TODO: This pattern occurs a few times with variations. Centralize.
_COURSE_PATTERN = r"[A-Z]{2,4}\s*\d{3}[A-Za-z]?"
_COURSE_RE = re.compile(_COURSE_PATTERN)

# The raw cache holds the downloaded and extracted text strings from the
# calendar that contain the prerequisites, corequisites, and antirequisites.
# This provides some degree of an audit trail, but it also makes evolving
# the system easier without spamming SFU servers.
_RAW_CACHE = Path("raw_course_info.json")

_DEFAULT_GRAPH_FILE = "{program}-dependencies.json"
_DEFAULT_CALENDAR_FILE = "calendar.md"


# Disjunctive Normal Form constraints for which courses students must take
# to enrol in a particular course.
type DNF = list[list[str]]


class NonCourseRequirements(StrEnum):
    UNIT_REQUIREMENT = "units"
    INSTRUCTOR_PERMISSION = "permission"
    GRADE_CONSTRAINT = "minimum grade"
    RECOMMENDED = "recommended"


#############################################################################
# DNF parsing for complex requirements in prereqs and coreqs
#############################################################################

# A sequence of normalization passes remove corner cases from how the
# Calendar entries for CMPT are written in practice. Some of the passes
# make use of top-level parenthesized and non-parenthesized regions.


@dataclass
class Segment:
    text: str
    is_parenthesized: bool
    start: int
    end: int


def _iter_top_level(text: str) -> Generator[Segment]:
    """Yields segments of text that are either at depth 0 or fully enclosed in top-level parens."""
    depth = 0
    start = 0
    for i, c in enumerate(text):
        if c == "(":
            if depth == 0 and i > start:
                yield Segment(text[start:i], False, start, i)
                start = i
            depth += 1
        elif c == ")":
            depth = max(0, depth - 1)
            if depth == 0:
                yield Segment(text[start : i + 1], True, start, i + 1)
                start = i + 1

    if start < len(text):
        yield Segment(text[start:], False, start, len(text))


# TODO: This is CS specific. Evaluate whether we want to push this into
# non-course constraints. For other consumer tasks, this simplified things
# for now.
def _replace_course_shorthands(text: str) -> str:
    return text.replace("One W course", "(CMPT 105W or CMPT 376W)")


def _strip_leading_qualifier(text: str) -> str:
    """Strip text before the first course code or '('."""
    m = re.search(rf"{_COURSE_PATTERN}|\(", text)
    return text[m.start() :] if m else text


def _strip_trailing_noncourse(text: str) -> str:
    """Truncate after the last course code or ')' seen at paren depth 0."""
    last_meaningful = 0
    for segment in _iter_top_level(text):
        if segment.is_parenthesized:
            last_meaningful = segment.end
        else:
            matches = list(_COURSE_RE.finditer(segment.text))
            if matches:
                last_meaningful = segment.start + matches[-1].end()

    return text[:last_meaningful].rstrip(", ")


def _strip_comma_or(text: str) -> str:
    """Collapse ', or' into ' or' to prevent double-OR tokens."""
    return re.sub(r",\s*or\b", " or", text, flags=re.I)


def _strip_equivalents(text: str) -> str:
    return text.replace("or equivalent", "").replace("()", "")


def _expand_one_of(text: str) -> str:
    """'one of CMPT 125, 126, 128' -> '(CMPT 125 or CMPT 126 or CMPT 128)'"""

    def repl(match: re.Match[str]) -> str:
        content = match.group(1)
        parts = re.split(r",|\bor\b", content)
        expanded = [p.strip() for p in parts if p.strip()]
        return "(" + " or ".join(expanded) + ")"

    return re.sub(r"(?:one|any) of ([^.;]*)", repl, text, flags=re.I)


def _expand_parenthetical_or(text: str) -> str:
    """'MATH 152 or 155 (or 158)' -> 'MATH 152 or 155 or 158'"""
    return re.sub(r"\(\s*or\s+([^)]+)\)", r" or \1", text, flags=re.I)


def _expand_bare_numbers(text: str) -> str:
    """Expand bare 3-digit course numbers by prepending the last seen dept code.

    Runs after _expand_parenthetical_or so that '(or 158)' is already flat.
    """
    last_dept: list[str | None] = [None]

    def _replace(m: re.Match[str]) -> str:
        if m.group("course"):
            last_dept[0] = m.group("dept")
            return m.group(0)
        elif last_dept[0]:
            return f"{last_dept[0]} {m.group('bare')}"
        return m.group(0)

    return re.sub(
        r"(?P<course>(?P<dept>[A-Z]{2,4})\s+\d{3}[A-Za-z]?)"
        r"|(?<![A-Z\d])(?P<bare>\d{3}[A-Za-z]?)\b",
        _replace,
        text,
    )


def _resolve_comma_and_lists(text: str) -> str:
    """
    When a depth-0 AND keyword and depth-0 commas both exist, treat the
    comma-separated items as an AND-list. Split at depth-0 commas (consuming
    ', and' as a single split point), wrap each segment in parens, join with
    ' and '.
    """
    segments = list(_iter_top_level(text))

    # No need to transform if there are no `and`s
    if not any(not s.is_parenthesized and re.search(r"\band\b", s.text, re.I) for s in segments):
        return text

    # Tag every split point first and then remove them afterward to
    # avoid changing the original text as much as possible.
    parts = []
    for s in segments:
        if s.is_parenthesized:
            parts.append(s.text)
        else:
            parts.append(re.sub(r",\s*(?:and\b)?", "||SPLIT||", s.text, flags=re.I))
    parts = [p.strip() for p in "".join(parts).split("||SPLIT||") if p.strip()]

    if len(parts) < 2:
        return text

    def _ensure_wrapped(s: str) -> str:
        segments = list(_iter_top_level(s))
        return s if len(segments) == 1 and segments[0].is_parenthesized else f"({s})"

    return " and ".join(_ensure_wrapped(p) for p in parts)


def _normalize(text: str) -> str:
    # Ordering matters here.
    # In particular, `one_of` -> `parenthetical_or` -> `bare_numbers`
    text = _replace_course_shorthands(text)
    text = _strip_leading_qualifier(text)
    text = _strip_trailing_noncourse(text)
    text = _strip_comma_or(text)
    text = _strip_equivalents(text)
    text = _resolve_comma_and_lists(text)
    text = _expand_one_of(text)
    text = _expand_parenthetical_or(text)
    text = _expand_bare_numbers(text)
    return text


_TOKEN_SPEC = [
    ("LPAREN", r"\("),
    ("RPAREN", r"\)"),
    ("AND", r"\band\b"),
    ("OR", r"\bor\b"),
    ("COMMA", r","),  # treated as OR
    ("COURSE", _COURSE_PATTERN),
]

_MASTER_RE = re.compile("|".join(f"(?P<{n}>{p})" for n, p in _TOKEN_SPEC), re.I)


@dataclass
class Token:
    kind: str
    value: str


def tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    for m in _MASTER_RE.finditer(text):
        kind = m.lastgroup
        val = m.group()
        if kind == "COMMA":
            kind = "OR"
        assert kind
        tokens.append(Token(kind, val))

    return tokens


class DNFParser:
    def __init__(self, tokens: list[Token]) -> None:
        self.tokens: deque[Token] = deque(tokens)
        self.unexpected: list[str] = []

    def peek(self) -> Token | None:
        return self.tokens[0] if self.tokens else None

    def pop(self) -> Token:
        return self.tokens.popleft()

    def parse(self) -> DNF:
        return self.expr()

    def expr(self) -> DNF:
        result = self.term()

        while (token := self.peek()) and token.kind == "OR":
            self.pop()
            result += self.term()

        return result

    def term(self) -> DNF:
        result = self.factor()

        while (token := self.peek()) and token.kind == "AND":
            self.pop()
            right = self.factor()
            result = self.and_product(result, right)

        return result

    def factor(self) -> DNF:
        tok = self.peek()

        if tok is None:
            return []

        if tok.kind == "COURSE":
            self.pop()
            return [[tok.value]]

        if tok.kind == "LPAREN":
            self.pop()
            inner = self.expr()
            if (token := self.peek()) and token.kind == "RPAREN":
                self.pop()
            return inner

        # skip unknown tokens, but print out any issues for hand validation
        print(f"Unexpected value while parsing DNF: {tok}")
        self.unexpected.append(tok.value)
        self.pop()
        return []

    @staticmethod
    def and_product(left: DNF, right: DNF) -> DNF:
        if not left:
            return right
        if not right:
            return left
        return [a + b for a in left for b in right]


#############################################################################
# Scraping requirements from the SFU Calendar
#############################################################################


@dataclass(frozen=True, kw_only=True)
class _RawScrape:
    course: str
    title: str
    description: str


@dataclass(kw_only=True)
class _RawRequirements:
    course: str
    title: str
    prereqs: str = ""
    coreqs: str = ""
    antireqs: str = ""


@dataclass(kw_only=True)
class ProcessedRequirements:
    course: str
    title: str
    prereqs: DNF = field(default_factory=list)
    coreqs: DNF = field(default_factory=list)
    antireqs: list[str] = field(default_factory=list)
    noncourse: list[NonCourseRequirements] = field(default_factory=list)


def _scrape_course(title: Tag, description: Tag) -> _RawScrape | None:
    title_text = " ".join(title.text.split())
    code_match = _COURSE_RE.search(title_text)
    if not code_match:
        return None

    code = code_match.group()
    description_text = " ".join(description.text.split())

    return _RawScrape(
        course=code, title=title_text.split(" - ", 1)[1], description=description_text
    )


def _scrape_courses(program: str, year: int, term: str) -> list[_RawScrape]:
    calendar = CALENDAR.format(year=year, term=term)
    url = BASE + calendar + PAGE.format(program=program)

    # The first child of a course header should be a link to the page for the course
    def is_course_header(tag: Tag) -> bool:
        if tag.name != "h3":
            return False

        child = tag.find(recursive=False)
        return (
            isinstance(child, Tag) and child.name == "a" and calendar in str(child.get("href", ""))
        )

    html = requests.get(url, timeout=_TIMEOUT).text
    soup = BeautifulSoup(html, "html.parser")
    course_data = (
        (header, description)
        for header in soup.find_all(is_course_header)
        if (description := header.find_next_sibling("p"))
    )
    return [
        scrape
        for header, description in course_data
        if (scrape := _scrape_course(header, description))
    ]


def _extract_course_requirements(scrape: _RawScrape) -> _RawRequirements:
    description = scrape.description
    results = _RawRequirements(course=scrape.course, title=scrape.title)

    # 1. Extract Corequisites (contained in one sentence)
    coreq_match = re.search(r"Corequisite:\s*(.*?)\.", description, re.IGNORECASE)
    if coreq_match:
        results.coreqs = coreq_match.group(1).strip()

    # 2. Extract Prerequisites (contained in one sentence)
    pre_match = re.search(r"Prerequisite:\s*(.*?)\.", description, re.IGNORECASE)
    if pre_match:
        results.prereqs = pre_match.group(1).strip()

    # 3. Extract Antirequisites (contained in one or more sentences)
    prefix = r"Students (?:with credit for|who have taken|who have obtained credit for)"
    exclusion = r"may not (?:take|then take)"
    no_dot = r"[^.]"
    anti_pattern = rf"({no_dot}*?{prefix}\s+({no_dot}*?){exclusion}{no_dot}*\.)"
    anti_matches = re.findall(anti_pattern, description, re.IGNORECASE | re.DOTALL)
    results.antireqs = ", ".join(m[1].strip() for m in anti_matches)

    return results


#############################################################################
# Post-processing to extract the meaningful information from scraped strings
#############################################################################


def _process_constraint(raw: str) -> tuple[DNF, list[NonCourseRequirements]]:
    noncourse = [req for req in NonCourseRequirements if req.value in raw.lower()]

    text = _normalize(raw)
    tokens = tokenize(text)
    parser = DNFParser(tokens)
    dnf = parser.parse()
    if parser.unexpected:
        print("   in ", text)

    # deduplicate clauses
    dnf = [list(dict.fromkeys(clause)) for clause in dnf]
    dnf = list({tuple(sorted(c)): c for c in dnf}.values())

    return (dnf, noncourse)


_ANTIREQ_COURSE_PATTERN: re.Pattern[str] = re.compile(
    r"(?P<subj>[A-Z]{2,4})?\s*(?P<numb>\d{3}[A-Za-z]?)",
)
_ANTILIST_DELIMITER_PATTERN: re.Pattern[str] = re.compile(r",|\bor\b")
_ANTILIST_IGNORE_KEYWORDS: set[str] = {"under the title", "before", "between"}


def _process_antireq_list(raw: str) -> list[str]:
    if not raw:
        return []

    # First break things into one chunk per course
    segments = (
        segment.strip()
        for segment in _ANTILIST_DELIMITER_PATTERN.split(raw)
        if segment and not any(key in segment for key in _ANTILIST_IGNORE_KEYWORDS)
    )

    # The make a clean list where we carry forward the subject information
    # in the cases it is missing.
    cleaned_list: list[str] = []
    current_subj: str | None = None
    for segment in segments:
        if match := _ANTIREQ_COURSE_PATTERN.search(segment):
            current_subj = match.group("subj") or current_subj
            if current_subj:
                cleaned_list.append(f"{current_subj} {match.group('numb')}")

    return cleaned_list


def _process_course(entry: _RawRequirements) -> ProcessedRequirements:
    requirements = ProcessedRequirements(course=entry.course, title=entry.title)
    requirements.prereqs, prereq_noncourse = _process_constraint(entry.prereqs)
    requirements.coreqs, coreq_noncourse = _process_constraint(entry.coreqs)
    requirements.antireqs = _process_antireq_list(entry.antireqs)
    requirements.noncourse = prereq_noncourse + coreq_noncourse

    return requirements


#############################################################################
# Calendar exporting
#############################################################################


def _save_markdown_calendar(scrapes: list[_RawScrape], outpath: Path) -> None:
    with open(outpath, "w") as f:
        for scrape in scrapes:
            print(f"## {scrape.course} -- {scrape.title}\n\n{scrape.description}\n\n", file=f)


#############################################################################
# Main
#############################################################################


class _ParsedArgs(Protocol):
    force: bool
    year: int
    term: str
    extract_calendar: bool
    output: Path
    program: list[str]


def _parse_args() -> _ParsedArgs:
    parser = argparse.ArgumentParser(description="A script to process calendars and dependencies.")

    parser.add_argument(
        "--force", action="store_true", default=False, help="Force refreshing the calendar"
    )

    parser.add_argument(
        "--year",
        type=int,
        default=None,
        help="Sets the calendar year (defaults to the last collected or this year)",
    )

    parser.add_argument(
        "--term",
        choices=["spring", "summer", "fall"],
        default=None,
        help="Sets the calendar term (defaults to the last collected or the latest this year)",
    )

    parser.add_argument(
        "--extract-calendar",
        action="store_true",
        default=False,
        help="Just extract calendar data to a markdown file",
    )

    parser.add_argument(
        "--program",
        action="append",
        help="Which program (or programs) to extract (defaults to cmpt)",
    )

    parser.add_argument("--output", type=Path, default=None, help="Output filename")

    args = parser.parse_args()

    # All default values and relationships between arguments must be set after
    # initial parsing.

    if args.year or args.term or args.extract_calendar or args.program:
        args.force = True

    if args.program is None:
        args.program = ["cmpt"]

    if args.output is None:
        if args.extract_calendar:
            args.output = Path(_DEFAULT_CALENDAR_FILE)
        else:
            args.output = Path(_DEFAULT_GRAPH_FILE.format(program='-'.join(args.program)))

    now = datetime.datetime.now()
    if not args.year:
        args.year = now.year
    if not args.term:
        current_month = now.month
        # The calendar for the following term should be available two months
        # in advance... I think. Maybe check in the future. We could
        # speculatively try, but it doesn't seem worth it.
        if current_month in [1, 2]:
            args.term = "spring"
        elif 3 <= current_month <= 6:
            args.term = "summer"
        else:
            args.term = "fall"
    return args


def main() -> None:
    args = _parse_args()

    if args.force or not _RAW_CACHE.exists():
        # TODO: Clean up in python 3.15
        scraped_courses = [scraped for program in args.program
                           for scraped in _scrape_courses(program.lower(), args.year, args.term)]

        if args.extract_calendar:
            print("Saving markdown calendar to", args.output)
            _save_markdown_calendar(scraped_courses, args.output)
            return

        raw_courses = [_extract_course_requirements(scrape) for scrape in scraped_courses]

        with open(_RAW_CACHE, "w") as f:
            json.dump(list(asdict(c) for c in raw_courses), f, indent=2)
    else:
        print("Reading cached course information...")
        with open(_RAW_CACHE, encoding="utf-8") as f:
            raw_courses = [_RawRequirements(**c) for c in json.load(f)]

    courses = [_process_course(raw) for raw in raw_courses]

    with open(args.output, "w") as f:
        json.dump(list(asdict(c) for c in courses), f, indent=2)

    print(f"Done analyzing {len(courses)} courses")
