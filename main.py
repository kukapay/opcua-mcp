from mcp.server.fastmcp import FastMCP, Context
from opcua import Client
from contextlib import asynccontextmanager
from typing import AsyncIterator
import asyncio
import os
from typing import List, Dict, Any
from opcua import ua  #

server_url = os.getenv("OPCUA_SERVER_URL", "opc.tcp://localhost:4840")


# Manage the lifecycle of the OPC UA client connection
@asynccontextmanager
async def opcua_lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """Handle OPC UA client connection lifecycle."""
    client = Client(server_url)
    try:
        # Connect to OPC UA server synchronously, wrapped in a thread for async compatibility
        await asyncio.to_thread(client.connect)
        print("Connected to OPC UA server")
        yield {"opcua_client": client}
    finally:
        # Disconnect from OPC UA server on shutdown
        await asyncio.to_thread(client.disconnect)
        print("Disconnected from OPC UA server")


# Create an MCP server instance
mcp = FastMCP("OPCUA-Control", lifespan=opcua_lifespan)


# Tool: Read the value of an OPC UA node
@mcp.tool()
def read_opcua_node(node_id: str, ctx: Context) -> str:
    """
    Read the value of a specific OPC UA node.

    Parameters:
        node_id (str): The OPC UA node ID in the format 'ns=<namespace>;i=<identifier>'.
                       Example: 'ns=2;i=2'.

    Returns:
        str: The value of the node as a string, prefixed with the node ID.
    """
    client = ctx.request_context.lifespan_context["opcua_client"]
    node = client.get_node(node_id)
    value = node.get_value()  # Synchronous call to get node value
    return f"Node {node_id} value: {value}"


# Tool: Write a value to an OPC UA node
@mcp.tool()
def write_opcua_node(node_id: str, value: str, ctx: Context) -> str:
    """
    Write a value to a specific OPC UA node.

    Parameters:
        node_id (str): The OPC UA node ID in the format 'ns=<namespace>;i=<identifier>'.
                       Example: 'ns=2;i=3'.
        value (str): The value to write to the node. Will be converted based on node type.

    Returns:
        str: A message indicating success or failure of the write operation.
    """
    client = ctx.request_context.lifespan_context["opcua_client"]
    node = client.get_node(node_id)
    try:
        # Convert value based on the node's current type
        current_value = node.get_value()
        python_typed_value = value
        if isinstance(current_value, float):
            python_typed_value = float(value)
        elif isinstance(current_value, int):
            python_typed_value = int(value)
        # use variant type of node
        node.set_value(python_typed_value, node.get_data_type_as_variant_type())
        return f"Successfully wrote {value} to node {node_id}"
    except Exception as e:
        return f"Error writing to node {node_id}: {str(e)}"


@mcp.tool()
def browse_opcua_node_children(node_id: str, ctx: Context) -> str:
    """
    Browse the children of a specific OPC UA node.

    Parameters:
        node_id (str): The OPC UA node ID to browse (e.g., 'ns=0;i=85' for Objects folder).

    Returns:
        str: A string representation of a list of child nodes, including their NodeId and BrowseName.
             Returns an error message on failure.
    """
    client = ctx.request_context.lifespan_context["opcua_client"]
    try:
        node = client.get_node(node_id)
        children = node.get_children()

        children_info = []
        for child in children:
            try:
                browse_name = child.get_browse_name()
                children_info.append(
                    {
                        "node_id": child.nodeid.to_string(),
                        "browse_name": f"{browse_name.NamespaceIndex}:{browse_name.Name}",
                    }
                )
            except Exception as e:
                children_info.append(
                    {
                        "node_id": child.nodeid.to_string(),
                        "browse_name": f"Error getting name: {e}",
                    }
                )

        # import json
        # return json.dumps(children_info, indent=2)
        return f"Children of {node_id}: {children_info!r}"

    except Exception as e:
        return f"Error Browse children of node {node_id}: {str(e)}"


@mcp.tool()
def read_multiple_opcua_nodes(node_ids: List[str], ctx: Context) -> str:
    """
    Read the values of multiple OPC UA nodes in a single request.

    Parameters:
        node_ids (List[str]): A list of OPC UA node IDs to read (e.g., ['ns=2;i=2', 'ns=2;i=3']).

    Returns:
        str: A string representation of a dictionary mapping node IDs to their values, or an error message.
    """
    client = ctx.request_context.lifespan_context["opcua_client"]
    try:
        nodes_to_read = [client.get_node(nid) for nid in node_ids]
        values = []
        # Iterate over each node in nodes_to_read
        for node in nodes_to_read:
            try:
                # Get the value of the current node
                value = node.get_value()
                # Append the value to the values list
                values.append(value)
            except Exception as e:
                # In case of an error, append the error message
                values.append(f"Error reading node {node.nodeid.to_string()}: {str(e)}")

        # Map node IDs to their corresponding values
        results = {
            node.nodeid.to_string(): value for node, value in zip(nodes_to_read, values)
        }

        return f"Read multiple nodes values: {results!r}"

    except ua.UaError as e:
        status_name = e.code_as_name() if hasattr(e, "code_as_name") else "Unknown"
        status_code_hex = f"0x{e.code:08X}" if hasattr(e, "code") else "N/A"
        return f"Error reading multiple nodes {node_ids}: OPC UA Error - Status: {status_name} ({status_code_hex})"
    except Exception as e:
        return f"Error reading multiple nodes {node_ids}: {type(e).__name__} - {str(e)}"


@mcp.tool()
def write_multiple_opcua_nodes(
    nodes_to_write: List[Dict[str, Any]], ctx: Context
) -> str:
    """
    Write values to multiple OPC UA nodes in a single request.

    Parameters:
        nodes_to_write (List[Dict[str, Any]]): A list of dictionaries, where each dictionary
                                               contains 'node_id' (str) and 'value' (Any).
                                               The value will be wrapped in an OPC UA Variant.
                                               Example: [{'node_id': 'ns=2;i=2', 'value': 10.5},
                                                         {'node_id': 'ns=2;i=3', 'value': 'active'}]

    Returns:
        str: A message indicating the success or failure of the write operation.
             Returns status codes for each write attempt.
    """
    client = ctx.request_context.lifespan_context["opcua_client"]

    node_ids_for_error_msg = [
        item.get("node_id", "unknown_node") for item in nodes_to_write
    ]

    try:
        nodes = [client.get_node(item["node_id"]) for item in nodes_to_write]

        # Iterate over nodes and values to set each value individually
        status_report = []
        for node, item in zip(nodes, nodes_to_write):
            try:
                # Create a Variant from the value
                value_as_variant = ua.Variant(item["value"])
                # Set the value of the node
                current_value = node.get_value()
                if isinstance(current_value, (int, float)):
                    node.set_value(float(value_as_variant.Value))
                else:
                    node.set_value(value_as_variant.Value)

                status_report.append(
                    {
                        "node_id": item["node_id"],
                        "value_written": item["value"],
                        "status": "Success",
                    }
                )
            except Exception as e:
                return f"Error writing to node {node}: {str(e)}"
        # Return the status report
        return f"Write multiple nodes results: {status_report!r}"

    except ua.UaError as e:
        status_name = e.code_as_name() if hasattr(e, "code_as_name") else "Unknown"
        status_code_hex = f"0x{e.code:08X}" if hasattr(e, "code") else "N/A"
        return f"Error writing multiple nodes {node_ids_for_error_msg}: OPC UA Error - Status: {status_name} ({status_code_hex})"
    except Exception as e:
        return f"Error writing multiple nodes {node_ids_for_error_msg}: {type(e).__name__} - {str(e)}"


# Run the server
if __name__ == "__main__":
    mcp.run()
