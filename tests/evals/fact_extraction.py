"""Eval suite — fact extraction tasks (anchored regex match).

Patterns are anchored at token boundaries and tolerate hyphen/space/case
variation in the *answer* (see ``tests/evals/__init__.py`` for the convention).
"""

from tests.evals import EvalTask

PAD = "Additional reference sections with supplementary material. " * 30

TASKS = [
    EvalTask(
        name="extract_isbn",
        query="What is the ISBN of the referenced book?",
        context=(
            "Required textbook: 'Introduction to Algorithms' by Cormen, Leiserson, "
            "Rivest, and Stein. ISBN: 978-0-262-03384-8. Fourth edition, published "
            "2022. Available at the campus bookstore or online retailers." + PAD
        ),
        # Both the hyphenated and the bare ISBN-13 form are correct answers.
        expected_pattern=r"(?<!\d)978-?0-?262-?03384-?8(?!\d)",
    ),
    EvalTask(
        name="extract_phone",
        query="What is the phone number for customer support?",
        context=(
            "For assistance, contact our support team: Phone: (555) 123-4567, "
            "Email: help@example.com, Live Chat: available 9am-5pm EST. "
            "Response times are typically under 2 hours during business hours." + PAD
        ),
        # "(555) 123-4567", "555-123-4567", "555 123 4567" are all the number.
        expected_pattern=r"(?<!\d)\(?555\)?[\s-]*123[\s-]?4567(?!\d)",
    ),
    EvalTask(
        name="extract_ip",
        query="What is the primary server IP address mentioned?",
        context=(
            "Server configuration:\n"
            "Primary: 192.168.1.100 (production)\n"
            "Backup: 192.168.1.101 (failover)\n"
            "Database: 10.0.0.50 (internal)\n"
            "Load Balancer: 203.0.113.10 (public)\n" + PAD
        ),
        expected_pattern=r"(?<!\d)192\.168\.1\.100(?!\d)",
    ),
    EvalTask(
        name="extract_currency",
        query="In what currency are the prices listed?",
        context=(
            "International pricing guide:\n"
            "United States: $199.99 USD\n"
            "European Union: 179.99 EUR\n"
            "United Kingdom: 159.99 GBP\n"
            "Japan: 22,000 JPY\n"
            "All prices include applicable taxes." + PAD
        ),
        expected_pattern=r"(?i)\bUSD\b",
    ),
    EvalTask(
        name="extract_date",
        query="On what date was the report published?",
        context=(
            "Quarterly Financial Report\n"
            "Publication Date: March 15, 2024\n"
            "Prepared by: Financial Analysis Division\n"
            "Classification: Internal Use Only\n"
            "Summary: Revenue exceeded projections by 12% in Q1 2024." + PAD
        ),
        # "March 15, 2024" / "march 15 2024" both correct.
        expected_pattern=r"(?i)\bmarch\s+15,?\s+2024(?!\d)",
    ),
]
