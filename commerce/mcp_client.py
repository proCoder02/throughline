"""
Minimal MCP (Model Context Protocol) client over Streamable HTTP -- JSON-RPC
2.0 requests against a remote MCP server (mcp.swiggy.com/food|im|dineout).
Only the two calls this integration needs (tools/list, tools/call); this is
deliberately not a general-purpose MCP SDK.
"""
from __future__ import annotations

import itertools
import json

import requests

from . import oauth

_id_counter = itertools.count(1)


class MCPToolError(Exception):
    def __init__(self, message: str, code=None):
        super().__init__(message)
        self.code = code


def _parse_response(resp) -> dict:
    content_type = resp.headers.get("Content-Type", "")
    if "text/event-stream" in content_type:
        # Streamable HTTP transport may reply as SSE -- the last "data:"
        # line carries the final JSON-RPC response.
        data_lines = [line[5:].strip() for line in resp.text.splitlines() if line.startswith("data:")]
        if not data_lines:
            raise MCPToolError("Empty SSE response from MCP server")
        return json.loads(data_lines[-1])
    return resp.json()


def _request(server: str, method: str, params: dict, access_token: str) -> dict:
    payload = {"jsonrpc": "2.0", "id": next(_id_counter), "method": method, "params": params}
    resp = requests.post(
        oauth.base_url(server),
        json=payload,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
        timeout=20,
    )
    resp.raise_for_status()
    body = _parse_response(resp)
    if "error" in body:
        err = body["error"]
        raise MCPToolError(err.get("message", "MCP call failed"), code=err.get("code"))
    return body.get("result", {})


def list_tools(server: str, access_token: str) -> list[dict]:
    return _request(server, "tools/list", {}, access_token).get("tools", [])


def call_tool(server: str, tool_name: str, arguments: dict, access_token: str) -> dict:
    result = _request(server, "tools/call", {"name": tool_name, "arguments": arguments}, access_token)
    if result.get("isError"):
        text = next(
            (c.get("text") for c in result.get("content", []) if c.get("type") == "text"),
            "Tool call failed",
        )
        raise MCPToolError(text)
    return result
