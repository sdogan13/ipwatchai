"""
Class 99 (Global Brand) Utility Functions

Class 99 is a special designation meaning "Global Brand" - it covers ALL 45 Nice classes.
This module provides utility functions for handling Class 99 logic across the system.
"""

from typing import List, Set, Optional, Union

# Class 99 represents a "Global Brand" that covers all 45 Nice classes
GLOBAL_CLASS = 99
ALL_NICE_CLASSES = set(range(1, 46))  # Classes 1-45


def is_global_class(class_num: Union[int, str]) -> bool:
    """
    Check if a class number represents the Global Brand (Class 99).

    Args:
        class_num: Class number to check (int or string)

    Returns:
        True if class_num is 99 (Global Brand)
    """
    try:
        return int(class_num) == GLOBAL_CLASS
    except (ValueError, TypeError):
        return False


def expand_classes(classes: List[int]) -> Set[int]:
    """
    Expand class list - if Class 99 is present, expand to all 45 classes.

    Args:
        classes: List of Nice class numbers

    Returns:
        Set of expanded class numbers (1-45 if 99 was in input, otherwise original set)

    Examples:
        >>> expand_classes([99])
        {1, 2, 3, ..., 45}
        >>> expand_classes([5, 10, 99])
        {1, 2, 3, ..., 45}
        >>> expand_classes([5, 10, 35])
        {5, 10, 35}
    """
    if not classes:
        return set()

    class_set = set(classes)

    # If Class 99 (Global Brand) is present, expand to all classes
    if GLOBAL_CLASS in class_set:
        return ALL_NICE_CLASSES.copy()

    return class_set


def classes_overlap(classes_a: List[int], classes_b: List[int]) -> bool:
    """
    Check if two class lists have any overlap, considering Class 99 as covering all classes.

    Args:
        classes_a: First list of Nice class numbers
        classes_b: Second list of Nice class numbers

    Returns:
        True if there's any overlap between the two class lists

    Examples:
        >>> classes_overlap([5, 10], [10, 20])
        True
        >>> classes_overlap([5, 10], [20, 30])
        False
        >>> classes_overlap([99], [5])  # Class 99 overlaps with everything
        True
        >>> classes_overlap([5], [99])  # Class 99 overlaps with everything
        True
    """
    if not classes_a or not classes_b:
        return False

    expanded_a = expand_classes(classes_a)
    expanded_b = expand_classes(classes_b)

    return bool(expanded_a & expanded_b)


def get_overlapping_classes(classes_a: List[int], classes_b: List[int]) -> Set[int]:
    """
    Get the set of overlapping classes between two class lists.

    Args:
        classes_a: First list of Nice class numbers
        classes_b: Second list of Nice class numbers

    Returns:
        Set of class numbers that overlap between the two lists

    Examples:
        >>> get_overlapping_classes([5, 10, 15], [10, 15, 20])
        {10, 15}
        >>> get_overlapping_classes([99], [5, 10])  # Class 99 = all classes
        {5, 10}
        >>> get_overlapping_classes([5, 10], [99])  # Class 99 = all classes
        {5, 10}
    """
    if not classes_a or not classes_b:
        return set()

    expanded_a = expand_classes(classes_a)
    expanded_b = expand_classes(classes_b)

    return expanded_a & expanded_b


def format_class_display(classes: List[int]) -> str:
    """
    Format class list for display, showing "Global (All Classes)" for Class 99.

    Args:
        classes: List of Nice class numbers

    Returns:
        Formatted string for display

    Examples:
        >>> format_class_display([99])
        "Global (All Classes)"
        >>> format_class_display([5, 10, 35])
        "5, 10, 35"
        >>> format_class_display([5, 99, 10])  # 99 takes precedence
        "Global (All Classes)"
    """
    if not classes:
        return "None"

    if GLOBAL_CLASS in classes:
        return "Global (All Classes)"

    return ", ".join(str(c) for c in sorted(classes))


def should_include_in_class_filter(trademark_classes: List[int], filter_classes: List[int]) -> bool:
    """
    Determine if a trademark should be included when filtering by specific classes.

    A trademark with Class 99 should appear in ANY class filter.
    A filter for Class 99 should return ALL trademarks.

    Args:
        trademark_classes: Classes registered for the trademark
        filter_classes: Classes being filtered/searched for

    Returns:
        True if trademark should be included in the filtered results

    Examples:
        >>> should_include_in_class_filter([99], [5])  # Global brand appears in class 5 search
        True
        >>> should_include_in_class_filter([5, 10], [99])  # Class 99 filter returns everything
        True
        >>> should_include_in_class_filter([5, 10], [5])  # Normal overlap
        True
        >>> should_include_in_class_filter([5, 10], [20])  # No overlap
        False
    """
    return classes_overlap(trademark_classes, filter_classes)


def get_class_sql_condition(filter_classes: List[int], column_name: str = "nice_classes") -> str:
    """
    Generate SQL condition for class filtering that handles Class 99.

    Args:
        filter_classes: Classes to filter by
        column_name: Name of the array column containing classes

    Returns:
        SQL WHERE clause fragment

    Examples:
        >>> get_class_sql_condition([5, 10])
        "(nice_classes && ARRAY[5, 10] OR 99 = ANY(nice_classes))"
        >>> get_class_sql_condition([99])
        "TRUE"  # Class 99 filter means return all trademarks
    """
    if not filter_classes:
        return "TRUE"

    # If filtering for Class 99, return all trademarks
    if GLOBAL_CLASS in filter_classes:
        return "TRUE"

    # Otherwise: match if classes overlap OR trademark has Class 99 (global brand)
    class_array = ", ".join(str(c) for c in filter_classes)
    return f"({column_name} && ARRAY[{class_array}] OR {GLOBAL_CLASS} = ANY({column_name}))"


def calculate_class_overlap_score(
    query_classes: List[int],
    trademark_classes: List[int]
) -> float:
    """
    Calculate a score based on class overlap, considering Class 99.

    Used for risk scoring - higher overlap means higher conflict risk.

    Args:
        query_classes: Classes from the search query/new trademark
        trademark_classes: Classes of the existing trademark

    Returns:
        Score from 0.0 to 1.0 representing class overlap
        - 1.0: Perfect overlap or one side has Class 99
        - 0.5-0.99: Partial overlap
        - 0.0: No overlap
    """
    if not query_classes or not trademark_classes:
        return 0.0

    # If either has Class 99 (Global Brand), it's a full class conflict
    if GLOBAL_CLASS in query_classes or GLOBAL_CLASS in trademark_classes:
        return 1.0

    query_set = set(query_classes)
    trademark_set = set(trademark_classes)

    overlap = query_set & trademark_set

    if not overlap:
        return 0.0

    # Score based on the smaller set's coverage
    smaller_size = min(len(query_set), len(trademark_set))
    overlap_ratio = len(overlap) / smaller_size

    return overlap_ratio


def is_class_conflict_high_risk(
    query_classes: List[int],
    trademark_classes: List[int],
    threshold: float = 0.5
) -> bool:
    """
    Determine if class overlap represents a high conflict risk.

    Args:
        query_classes: Classes from the search query
        trademark_classes: Classes of the existing trademark
        threshold: Minimum overlap score to consider high risk (default 0.5)

    Returns:
        True if class overlap score exceeds threshold
    """
    score = calculate_class_overlap_score(query_classes, trademark_classes)
    return score >= threshold
