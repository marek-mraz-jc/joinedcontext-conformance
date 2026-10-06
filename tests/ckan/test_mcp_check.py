"""The MCP check's own rules (T-3152), offline, for the fast lane."""

from mcp_check import client_config, describes, tool_problems

URL = "https://portal.example/api/endpoint/abc/mcp"
DESCRIPTION = (
    "For AI assistants: a Model Context Protocol server over this dataset's endpoint https://portal.example/api/endpoint/abc. "
    "Tools: `query_entities` …\n\nLicence: Open Data Commons Open Database License; the data is reused under it.\n"
)


def test_a_description_names_the_tools_the_endpoint_and_the_licence_by_any_of_its_names():
    endpoint = "https://portal.example/api/endpoint/abc"
    assert describes(DESCRIPTION, endpoint, ["Open Data Commons Open Database License (ODbL)", "odc-odbl"]) == []
    assert describes("", endpoint, ["cc-by"]) == ["the tools", "the endpoint", "the licence"]
    assert describes(DESCRIPTION, endpoint, ["Creative Commons Attribution", "cc-by"]) == ["the licence"]


def test_a_tool_needs_a_description_and_an_object_schema_and_the_reading_tools_must_be_there():
    good = {"type": "object", "properties": {}}
    tools = [{"name": n, "description": "d", "inputSchema": good} for n in ("query_entities", "get_entity", "describe_schema")]
    assert tool_problems(tools) == []
    assert tool_problems(tools[:2]) == ["no tool describe_schema"]
    assert tool_problems(tools + [{"name": "x", "inputSchema": {"type": "string"}}]) == ["x has no description", "x has no parameter schema"]


def test_the_page_configuration_must_be_json_naming_this_resource():
    page = f'<pre><code>{{&#34;mcpServers&#34;: {{&#34;d&#34;: {{&#34;type&#34;: &#34;http&#34;, &#34;url&#34;: &#34;{URL}&#34;}}}}}}</code></pre>'
    assert client_config(page, URL) is None
    assert client_config(page, URL + "x").startswith("the configuration names")
    assert client_config("<pre><code>mcpServers {</code></pre>", URL) == "the page's configuration is not JSON with mcpServers"
    assert client_config("<p>nothing</p>", URL) == "the page shows no MCP client configuration"
