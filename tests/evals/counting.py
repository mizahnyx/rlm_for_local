"""Eval suite — counting and aggregation tasks (numeric tolerance)."""

from tests.evals import EvalTask

PAD = "Unrelated padding text about various topics. " * 50

TASKS = [
    EvalTask(
        name="count_fruits",
        query="How many fruits are listed in total?",
        context=("apple\n" * 15 + "banana\n" * 8 + "cherry\n" * 12 + PAD),
        expected_pattern="35",
        tolerance=0.0,
    ),
    EvalTask(
        name="count_colors",
        query="How many color names appear in the list?",
        context=("red\n" * 7 + "blue\n" * 5 + "green\n" * 9 + "yellow\n" * 3 + PAD),
        expected_pattern="24",
        tolerance=0.0,
    ),
    EvalTask(
        name="count_errors",
        query="How many ERROR log lines are in the log file?",
        context=(
            "2024-01-15 08:00:01 INFO Server starting\n"
            "2024-01-15 08:00:05 ERROR Database connection failed\n"
            "2024-01-15 08:00:10 INFO Retrying connection\n"
            "2024-01-15 08:00:15 ERROR Connection timeout\n"
            "2024-01-15 08:00:20 INFO Connection established\n"
            "2024-01-15 08:00:25 WARN High memory usage\n"
            "2024-01-15 08:00:30 ERROR Disk space low\n"
            "2024-01-15 08:00:35 ERROR Permission denied\n"
            "2024-01-15 08:00:40 INFO Health check passed\n"
            + "Additional system log entries with routine operational messages. " * 50
        ),
        expected_pattern=r"\b4\b",
        tolerance=0.0,
    ),
    EvalTask(
        name="sum_prices",
        query="What is the total cost of all items?",
        context=(
            "Shopping cart contents:\n"
            "Widget A: $12.50\nWidget B: $8.75\nWidget C: $15.00\nWidget D: $6.25\n"
            + "Detailed product descriptions follow. " * 50
        ),
        expected_pattern="42\\.?50",
        tolerance=0.01,
    ),
    EvalTask(
        name="average_temperature",
        query="What is the average temperature from the readings?",
        context=(
            "Daily temperature readings (Celsius):\n"
            "Monday: 22\nTuesday: 24\nWednesday: 19\nThursday: 26\n"
            "Friday: 21\nSaturday: 23\nSunday: 25\n"
            + "Detailed meteorological analysis follows. " * 50
        ),
        expected_pattern="22\\.?86",
        tolerance=0.1,
    ),
]
