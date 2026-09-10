"""Eval suite — multi-hop reasoning tasks.

Patterns are anchored at token boundaries; names tolerate hyphen/space
variation (``Martin Fowler`` / ``Martin-Fowler``) but not prefix extensions
(``Martina Fowler`` no longer matches). See ``tests/evals/__init__.py``.
"""

from tests.evals import EvalTask

PAD = "Additional reference material and appendices. " * 30

TASKS = [
    EvalTask(
        name="author_of_book",
        query="Who wrote the book mentioned in the context?",
        context=(
            "The reading list for the course included several important works. "
            "'Patterns of Enterprise Architecture' was assigned for week 3. "
            "The instructor noted that Martin Fowler's insights were particularly "
            "relevant to the design patterns module." + PAD
        ),
        expected_pattern=r"(?i)\bmartin[\s-]*fowler\b",
    ),
    EvalTask(
        name="capital_of_country",
        query="What is the capital of the country where the conference was held?",
        context=(
            "The annual tech conference was held in Tokyo this year. Attendees from "
            "over 30 countries participated in workshops and keynote sessions." + PAD
        ),
        expected_pattern=r"(?i)\btokyo\b",
    ),
    EvalTask(
        name="ceo_of_company",
        query="Who is the CEO of the company that acquired StartupX?",
        context=(
            "StartupX was acquired by MegaCorp in March 2024. The deal was announced "
            "by MegaCorp's CEO, Sarah Chen, who called it a strategic move into the "
            "AI space." + PAD
        ),
        expected_pattern=r"(?i)\bsarah[\s-]*chen\b",
    ),
    EvalTask(
        name="most_expensive_product",
        query="Which product category has the highest average price?",
        context=(
            "Product catalog:\n"
            "Electronics: Laptop $1200, Tablet $500, Phone $800\n"
            "Furniture: Desk $450, Chair $200, Shelf $150\n"
            "Clothing: Jacket $120, Shirt $40, Pants $60\n" + PAD
        ),
        expected_pattern=r"(?i)\belectronics\b",
    ),
    EvalTask(
        name="oldest_employee",
        query="What is the name of the oldest employee in the engineering department?",
        context=(
            "Engineering department roster:\n"
            "Alice Chen, age 34, Senior Developer\n"
            "Bob Smith, age 28, Developer\n"
            "Carol Davis, age 45, Team Lead\n"
            "David Lee, age 31, Developer\n"
            "Eve Wilson, age 39, Architect\n" + PAD
        ),
        expected_pattern=r"(?i)\bcarol[\s-]*davis\b",
    ),
]
