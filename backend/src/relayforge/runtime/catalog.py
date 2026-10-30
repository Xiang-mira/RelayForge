"""One bounded feedback demo, separate from the original ten-task corpus."""

CONTROLLED_TEMPLATE = "mcp_feedback"
MODEL_TEMPLATE = "mcp_header_parser"
ITERATION_TEMPLATES = {"mcp_csv_records", "mcp_command_words", "mcp_query_codec"}
PYTEST_TEMPLATES = {MODEL_TEMPLATE, *ITERATION_TEMPLATES}
CONTROLLED_TEMPLATES = {CONTROLLED_TEMPLATE, *PYTEST_TEMPLATES}
