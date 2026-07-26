"""Eval suite — fact extraction tasks (regex match)."""

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
        expected_pattern="978-0-262-03384-8",
    ),
    EvalTask(
        name="extract_phone",
        query="What is the phone number for customer support?",
        context=(
            "For assistance, contact our support team: Phone: (555) 123-4567, "
            "Email: help@example.com, Live Chat: available 9am-5pm EST. "
            "Response times are typically under 2 hours during business hours." + PAD
        ),
        expected_pattern=r"\(?555\)?\s*123-4567",
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
        expected_pattern=r"192\.168\.1\.100",
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
        expected_pattern="(?i)USD",
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
        expected_pattern="(?i)march 15,? 2024",
    ),
]
