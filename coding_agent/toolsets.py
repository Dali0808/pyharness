"""Application-owned tool registration; interfaces cannot grant themselves tools."""
from pathlib import Path

from agent.tools import ToolRegistry
from coding_agent.builtins import (
    Approval, create_edit_file_tool, create_glob_file_tool, create_grep_file_tool,
    create_list_dir_tool, create_read_file_tool, create_write_file_tool,
)
from coding_agent.command import CommandApproval, create_run_command_tool


def reading_tools(root: Path) -> ToolRegistry:
    tools = ToolRegistry()
    for factory in (create_read_file_tool, create_list_dir_tool, create_glob_file_tool, create_grep_file_tool):
        tools.register(factory(root))
    return tools


def coding_tools(root: Path, approve: Approval, approve_command: CommandApproval) -> ToolRegistry:
    tools = ToolRegistry()
    tools.register(create_read_file_tool(root))
    tools.register(create_write_file_tool(root, approve))
    tools.register(create_list_dir_tool(root))
    tools.register(create_glob_file_tool(root))
    tools.register(create_grep_file_tool(root))
    tools.register(create_edit_file_tool(root, approve))
    tools.register(create_run_command_tool(root, approve_command))
    return tools
