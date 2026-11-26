"""Call one tool of the Tilt Graph MCP server over stdio and print the answer.

    python examples/mcp_call.py examples/cohort.rl pre_trade_check '{"wallet": "w_...", "adding_to_loser": true}'

This is what an agent's MCP client does for every tool call. It needs `pip install -e ".[mcp]"`.
"""
import asyncio
import json
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def call(database, tool, arguments):
    params = StdioServerParameters(command=sys.executable, args=["-m", "tiltgraph", "serve", "-d", database])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            if tool == "list":
                return [{"tool": t.name, "about": (t.description or "").split("\n")[0]}
                        for t in (await session.list_tools()).tools]
            result = await session.call_tool(tool, arguments)
            text = result.content[0].text
            try:
                return json.loads(text)
            except ValueError:
                return text


if __name__ == "__main__":
    db, tool = sys.argv[1], sys.argv[2]
    args = json.loads(sys.argv[3]) if len(sys.argv) > 3 else {}
    out = asyncio.run(call(db, tool, args))
    print(out if isinstance(out, str) else json.dumps(out, indent=1))
