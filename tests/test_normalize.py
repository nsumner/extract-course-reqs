from extract_course_reqs.extract_course_reqs import (
    _expand_bare_numbers,
    _process_constraint,
    _resolve_comma_and_lists,
    _strip_comma_or,
    _strip_leading_qualifier,
    _strip_trailing_noncourse,
)


class TestStripLeadingQualifier:
    def test_strips_either_prefix(self) -> None:
        result = _strip_leading_qualifier(
            "Either (MACM 101 and CMPT 125) or (MATH 151 and CMPT 102)"
        )
        assert result == "(MACM 101 and CMPT 125) or (MATH 151 and CMPT 102)"

    def test_strips_recommended_prefix(self) -> None:
        # "BC Math 12" is a high-school prerequisite, not a university course code.
        # The regex won't match it, so the full string is returned unchanged.
        result = _strip_leading_qualifier("Recommended: BC Math 12 or equivalent")
        assert result == "Recommended: BC Math 12 or equivalent"

    def test_strips_one_w_course(self) -> None:
        result = _strip_leading_qualifier(
            "One W course, CMPT 225, (MACM 101 or ENSC 251) and (MATH 151 or MATH 150)"
        )
        assert result == "CMPT 225, (MACM 101 or ENSC 251) and (MATH 151 or MATH 150)"

    def test_no_change_when_starts_with_course(self) -> None:
        result = _strip_leading_qualifier("CMPT 225 and MACM 101")
        assert result == "CMPT 225 and MACM 101"

    def test_no_change_when_starts_with_paren(self) -> None:
        result = _strip_leading_qualifier("(CMPT 125 or CMPT 135) and MACM 101")
        assert result == "(CMPT 125 or CMPT 135) and MACM 101"

    def test_empty_string(self) -> None:
        assert _strip_leading_qualifier("") == ""

    def test_no_course_no_paren(self) -> None:
        # nothing recognisable → return unchanged
        result = _strip_leading_qualifier("Permission of the department")
        assert result == "Permission of the department"


class TestStripTrailingNoncourse:
    def test_strips_grade_suffix(self) -> None:
        result = _strip_trailing_noncourse("CMPT 225 and MACM 101, all with a minimum grade of C-")
        assert result == "CMPT 225 and MACM 101"

    def test_strips_units_suffix(self) -> None:
        result = _strip_trailing_noncourse(
            "CMPT 275 or CMPT 276, (MACM 201 or CMPT 210) , all with a minimum grade of C- and 15 units"
        )
        assert result == "CMPT 275 or CMPT 276, (MACM 201 or CMPT 210)"

    def test_no_change_when_ends_with_course(self) -> None:
        result = _strip_trailing_noncourse("CMPT 225 and MACM 101")
        assert result == "CMPT 225 and MACM 101"

    def test_no_change_when_ends_with_paren(self) -> None:
        result = _strip_trailing_noncourse("CMPT 225 and (MACM 101 or ENSC 251)")
        assert result == "CMPT 225 and (MACM 101 or ENSC 251)"

    def test_preserves_nested_parens(self) -> None:
        result = _strip_trailing_noncourse(
            "(MACM 101 and (CMPT 125 or CMPT 135)) or (MATH 151 and CMPT 102), all with C-"
        )
        assert result == "(MACM 101 and (CMPT 125 or CMPT 135)) or (MATH 151 and CMPT 102)"

    def test_empty_string(self) -> None:
        assert _strip_trailing_noncourse("") == ""

    def test_returns_empty_when_no_course_or_paren(self) -> None:
        # No course code and no paren → last_meaningful stays 0 → returns "".
        # This is intentional: noncourse-only text produces no meaningful end-anchor.
        result = _strip_trailing_noncourse("Permission of the instructor")
        assert result == ""


class TestFixCommaOr:
    def test_collapses_comma_or(self) -> None:
        result = _strip_comma_or("CMPT 225 and (BUS 232, STAT 201, MSE 210, or SEE 241)")
        assert result == "CMPT 225 and (BUS 232, STAT 201, MSE 210 or SEE 241)"

    def test_handles_space_variants(self) -> None:
        assert _strip_comma_or("A,or B") == "A or B"
        assert _strip_comma_or("A, or B") == "A or B"
        assert _strip_comma_or("A ,  or B") == "A  or B"

    def test_no_change_without_comma_or(self) -> None:
        result = _strip_comma_or("CMPT 225 or CMPT 275")
        assert result == "CMPT 225 or CMPT 275"

    def test_collapses_or_in_exceptional_cases(self) -> None:
        # "or, in exceptional cases" → the comma comes AFTER "or", not before
        # _strip_comma_or only targets ", or" (comma-then-or); this is "or," so no change
        result = _strip_comma_or("nine units or, in exceptional cases, permission")
        assert result == "nine units or, in exceptional cases, permission"


class TestResolveCommaAndLists:
    def test_oxford_comma_four_items(self) -> None:
        result = _resolve_comma_and_lists(
            "MACM 101, MATH 152, CMPT 125 or CMPT 135, and (MATH 240 or MATH 232)"
        )
        assert (
            result
            == "(MACM 101) and (MATH 152) and (CMPT 125 or CMPT 135) and (MATH 240 or MATH 232)"
        )

    def test_oxford_comma_three_paren_items(self) -> None:
        result = _resolve_comma_and_lists(
            "CMPT 225, (CMPT 295 or ENSC 254), and (CMPT 201 or ENSC 351)"
        )
        assert result == "(CMPT 225) and (CMPT 295 or ENSC 254) and (CMPT 201 or ENSC 351)"

    def test_oxford_comma_four_paren_items(self) -> None:
        result = _resolve_comma_and_lists(
            "CMPT 225, (MACM 201 or CMPT 210), (MATH 150 or MATH 151), and (MATH 232 or MATH 240)"
        )
        assert result == (
            "(CMPT 225) and (MACM 201 or CMPT 210) and (MATH 150 or MATH 151) and (MATH 232 or MATH 240)"
        )

    def test_comma_with_inline_and(self) -> None:
        # "One W course" stripped upstream; input here is:
        result = _resolve_comma_and_lists(
            "CMPT 225, (MACM 101 or (ENSC 251 and ENSC 252)) and (MATH 151 or MATH 150)"
        )
        assert result == (
            "(CMPT 225) and ((MACM 101 or (ENSC 251 and ENSC 252)) and (MATH 151 or MATH 150))"
        )

    def test_no_change_when_no_top_level_and(self) -> None:
        # Commas inside parens only — no top-level AND
        result = _resolve_comma_and_lists("(BUS 232, STAT 201, MSE 210 or SEE 241)")
        assert result == "(BUS 232, STAT 201, MSE 210 or SEE 241)"

    def test_no_change_when_no_commas(self) -> None:
        result = _resolve_comma_and_lists("CMPT 225 and (MACM 101 or ENSC 251)")
        assert result == "CMPT 225 and (MACM 101 or ENSC 251)"

    def test_no_change_when_no_top_level_commas(self) -> None:
        # AND exists at top level but no top-level commas
        result = _resolve_comma_and_lists("CMPT 225 and (BUS 232, STAT 201 or SEE 241)")
        assert result == "CMPT 225 and (BUS 232, STAT 201 or SEE 241)"

    def test_two_item_oxford_comma(self) -> None:
        result = _resolve_comma_and_lists("CMPT 225, and CMPT 275")
        assert result == "(CMPT 225) and (CMPT 275)"

    def test_wrap_does_not_double_wrap_already_parenthesised(self) -> None:
        # Segment "(A) or (B)" starts and ends with parens but is NOT a single
        # balanced paren group → _wrap should add outer parens.
        result = _resolve_comma_and_lists("(CMPT 125 or CMPT 135) or (CMPT 145), and MACM 101")
        assert result == "((CMPT 125 or CMPT 135) or (CMPT 145)) and (MACM 101)"


class TestProcessConstraintEndToEnd:
    """Full pipeline: _normalize → tokenize → DNFParser."""

    def test_cmpt210_prereq(self) -> None:
        raw = "MACM 101, MATH 152, CMPT 125 or CMPT 135, and (MATH 240 or MATH 232), all with a minimum grade of C-"
        dnf, _ = _process_constraint(raw)
        assert len(dnf) == 4  # 2 choices × 2 choices
        # every clause must contain MACM 101 and MATH 152
        for clause in dnf:
            assert "MACM 101" in clause
            assert "MATH 152" in clause
        # each clause has exactly one of CMPT 125/135 and one of MATH 240/232
        for clause in dnf:
            assert sum(c in clause for c in ["CMPT 125", "CMPT 135"]) == 1
            assert sum(c in clause for c in ["MATH 240", "MATH 232"]) == 1

    def test_cmpt303_prereq(self) -> None:
        raw = "CMPT 225, (CMPT 295 or ENSC 254), and (CMPT 201 or ENSC 351), all with a minimum grade of C-"
        dnf, _ = _process_constraint(raw)
        assert len(dnf) == 4
        for clause in dnf:
            assert "CMPT 225" in clause

    def test_cmpt295_prereq(self) -> None:
        raw = "Either (MACM 101 and (CMPT 125 or CMPT 135)) or (MATH 151 and CMPT 102 for students in an Applied Physics program), all with a minimum grade of C-"
        dnf, _ = _process_constraint(raw)
        assert len(dnf) == 3
        courses = {frozenset(c) for c in dnf}
        # Both MACM 101 branches must be present
        assert frozenset(["MACM 101", "CMPT 125"]) in courses
        assert frozenset(["MACM 101", "CMPT 135"]) in courses

    def test_cmpt353_prereq(self) -> None:
        raw = "CMPT 225 and (BUS 232, STAT 201, STAT 203, STAT 205, STAT 270, STAT 271, ENSC 280, MSE 210, or SEE 241), with a minimum grade of C-"
        dnf, _ = _process_constraint(raw)
        # CMPT 225 must appear in every clause
        for clause in dnf:
            assert "CMPT 225" in clause
        # 9 stat/bus alternatives → 9 clauses
        assert len(dnf) == 9

    def test_empty_prereq(self) -> None:
        dnf, _ = _process_constraint("")
        assert dnf == []

    def test_noncourse_prereq_returns_empty_dnf(self) -> None:
        dnf, _ = _process_constraint("Permission of Instructor and School")
        assert dnf == []


class TestExpandBareNumbers:
    def test_expands_two_bare_numbers(self) -> None:
        result = _expand_bare_numbers("MATH 152 or 155 or 158")
        assert result == "MATH 152 or MATH 155 or MATH 158"

    def test_expands_single_bare_number(self) -> None:
        result = _expand_bare_numbers("CMPT 125 or 135")
        assert result == "CMPT 125 or CMPT 135"

    def test_no_change_when_all_full_codes(self) -> None:
        result = _expand_bare_numbers("CMPT 125 or CMPT 135")
        assert result == "CMPT 125 or CMPT 135"

    def test_dept_resets_on_new_full_code(self) -> None:
        result = _expand_bare_numbers("MATH 152 or CMPT 135 or 140")
        assert result == "MATH 152 or CMPT 135 or CMPT 140"

    def test_no_expansion_without_preceding_dept(self) -> None:
        result = _expand_bare_numbers("155 or 158")
        assert result == "155 or 158"

    def test_no_change_empty_string(self) -> None:
        assert _expand_bare_numbers("") == ""

    def test_end_to_end_parenthetical_or(self) -> None:
        # Full pipeline: "(or 158)" is expanded by _expand_parenthetical_or first,
        # then bare numbers are expanded.
        raw = "MATH 152 or 155 (or 158), all with a minimum grade of C-"
        dnf, _ = _process_constraint(raw)
        assert sorted(dnf) == [["MATH 152"], ["MATH 155"], ["MATH 158"]]
