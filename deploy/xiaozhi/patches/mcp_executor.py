"""设备端MCP工具执行器"""

from typing import Dict, Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.connection import ConnectionHandler
from ..base import ToolType, ToolDefinition, ToolExecutor
from plugins_func.register import Action, ActionResponse
from .mcp_handler import call_mcp_tool


class DeviceMCPExecutor(ToolExecutor):
    """设备端MCP工具执行器"""

    def __init__(self, conn):
        self.conn = conn

    async def execute(
        self, conn: "ConnectionHandler", tool_name: str, arguments: Dict[str, Any]
    ) -> ActionResponse:
        """执行设备端MCP工具"""
        if not hasattr(conn, "mcp_client") or not conn.mcp_client:
            return ActionResponse(
                action=Action.ERROR,
                response="设备端MCP客户端未初始化",
            )

        if not await conn.mcp_client.is_ready():
            return ActionResponse(
                action=Action.ERROR,
                response="设备端MCP客户端未准备就绪",
            )

        try:
            # 转换参数为JSON字符串
            import json

            args_str = json.dumps(arguments) if arguments else "{}"

            # 调用设备端MCP工具
            result = await call_mcp_tool(conn, conn.mcp_client, tool_name, args_str)

            resultJson = None
            if isinstance(result, str):
                try:
                    resultJson = json.loads(result)
                except Exception as e:
                    pass

            # 视觉大模型不经过二次LLM处理
            if (
                resultJson is not None
                and isinstance(resultJson, dict)
                and "action" in resultJson
            ):
                return ActionResponse(
                    action=Action[resultJson["action"]],
                    response=resultJson.get("response", ""),
                )

            return ActionResponse(action=Action.REQLLM, result=str(result))

        except ValueError as e:
            return ActionResponse(action=Action.NOTFOUND, response=str(e))
        except Exception as e:
            # Waywise 补丁：设备工具失败（如拍照失败）不要把「MCP错误: Failed to capture photo」念给孩子，
            # 作为失败的工具结果交回 LLM（我们的 agent），由 Mumi 用自己的话请孩子再试一次。
            import json as _json

            return ActionResponse(
                action=Action.REQLLM, result=_json.dumps({"success": False, "message": str(e)}, ensure_ascii=False)
            )

    def get_tools(self) -> Dict[str, ToolDefinition]:
        """获取所有设备端MCP工具"""
        if not hasattr(self.conn, "mcp_client") or not self.conn.mcp_client:
            return {}

        tools = {}
        mcp_tools = self.conn.mcp_client.get_available_tools()

        for tool in mcp_tools:
            func_def = tool.get("function", {})
            tool_name = func_def.get("name", "")

            if tool_name:
                tools[tool_name] = ToolDefinition(
                    name=tool_name, description=tool, tool_type=ToolType.DEVICE_MCP
                )

        return tools

    def has_tool(self, tool_name: str) -> bool:
        """检查是否有指定的设备端MCP工具"""
        if not hasattr(self.conn, "mcp_client") or not self.conn.mcp_client:
            return False

        return self.conn.mcp_client.has_tool(tool_name)
