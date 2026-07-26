"""Eval suite definitions — needle-in-haystack tasks (regex match)."""

from tests.evals import EvalTask

CONTEXT_MULTIPLIER = 15

TASKS = [
    EvalTask(
        name="color_needle_blue",
        query="What color is mentioned in the context?",
        context=(
            "The sky was overcast and gray. Later, a brilliant blue emerged from behind the clouds. "
            "The observers noted the deep blue hue with satisfaction. The weather report confirmed "
            "scattered clouds but dominantly blue skies."
            + " Unrelated meteorological data filled the remaining pages with temperature readings "
              "and wind speed measurements." * CONTEXT_MULTIPLIER
        ),
        expected_pattern="(?i)blue",
    ),
    EvalTask(
        name="year_needle_1648",
        query="What year is mentioned in the context?",
        context=(
            "Historical records indicate various dates across the centuries. "
            "The treaty was signed in 1648 after prolonged negotiations. "
            "Many other events followed in subsequent centuries including the "
            "industrial revolution and two world wars."
            + " Additional historical context filled volumes with genealogical "
              "records and territorial disputes spanning generations." * CONTEXT_MULTIPLIER
        ),
        expected_pattern="1648",
    ),
    EvalTask(
        name="name_needle_alice",
        query="What person's name appears in the context?",
        context=(
            "The project team consisted of several engineers: Bob handled the database layer, "
            "Carol managed the API design, and Alice was responsible for the frontend "
            "implementation. David joined later as a consultant."
            + " Documentation continued with detailed architecture diagrams and deployment "
              "procedures spanning hundreds of pages." * CONTEXT_MULTIPLIER
        ),
        expected_pattern="(?i)alice",
    ),
    EvalTask(
        name="price_needle",
        query="What is the price of the widget in the context?",
        context=(
            "The company catalog listed various products: Premium Widget - $42.99, "
            "Standard Widget - $19.99, Budget Widget - $9.99. All prices were subject "
            "to change without notice. Shipping policies and return procedures were "
            "detailed in appendix B."
            + " The remainder of the catalog contained specifications for hundreds of "
              "unrelated products spanning multiple categories." * CONTEXT_MULTIPLIER
        ),
        expected_pattern="(?i)(42\\.99|forty-two)",
    ),
    EvalTask(
        name="email_needle",
        query="What email address is found in the context?",
        context=(
            "Contact information for the support team: general inquiries can be sent to "
            "support@example.com, billing questions to billing@example.com, and technical "
            "issues to dev@example.com."
            + " The knowledge base contained thousands of articles covering every aspect "
              "of the platform from installation to advanced configuration." * CONTEXT_MULTIPLIER
        ),
        expected_pattern="support@example\\.com",
    ),
    EvalTask(
        name="temperature_needle",
        query="What temperature is recorded in the context?",
        context=(
            "Sensor readings from the monitoring station: 2024-01-15 08:00 - 23.4C, "
            "2024-01-15 12:00 - 27.1C, 2024-01-15 16:00 - 25.8C. The annual average was "
            "calculated at 22.3C with a standard deviation of 1.7 degrees."
            + " Historical records dating back to 1950 contained hourly measurements "
              "across all twelve months." * CONTEXT_MULTIPLIER
        ),
        expected_pattern="25\\.?8",
    ),
    EvalTask(
        name="version_needle",
        query="What software version is mentioned in the context?",
        context=(
            "Release notes for Project Chimera: Version 3.7.2 includes critical security "
            "patches and performance improvements. Users on version 3.6.x should upgrade "
            "immediately. The migration guide covers all breaking changes."
            + " Documentation for previous versions was archived in the legacy knowledge "
              "base with detailed changelogs." * CONTEXT_MULTIPLIER
        ),
        expected_pattern="3\\.7\\.2",
    ),
]
