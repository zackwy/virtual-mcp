"""Add_number tool for MCP server.
"""

from core.server import mcp
from core.utils import get_tool_config


@mcp.tool()
def add_numbers(a: float, b: float) -> int:
 """Add two numbers together and return the result.

 Args:
     a: The first number to add.
     b: The second number to add.

 Returns:
     A string representing the sum of the two numbers.
 """
 # Optional: Get config if you want to control formatting/units
 config = get_tool_config("add_numbers")
 unit = config.get("unit", "")

 result = a + b

 return int(a + b)
