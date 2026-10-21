import json
import copy
from aiohttp import web
from config.logger import setup_logging
from core.api.base_handler import BaseHandler
from core.utils.util import get_vision_url, is_valid_image_file
from core.utils.vllm import create_instance
from config.config_loader import get_private_config_from_api
from core.utils.auth import AuthToken
import base64
from typing import Tuple, Optional
from plugins_func.register import Action

TAG = __name__

# 设置最大文件大小为5MB
MAX_FILE_SIZE = 5 * 1024 * 1024



def _white_balance(image_data: bytes, logger) -> bytes:
    """KidsWise 补丁：灰度世界白平衡，只在内存里做，不落盘。

    板载 OV3660 在 YUV422 模式下画面明显偏紫（R、B 偏高，G 偏低），会让视觉模型把白杯子说成紫色。
    三个通道均值差距不大时原样返回，避免把正常画面调坏。
    """
    try:
        import io
        from PIL import Image, ImageChops

        img = Image.open(io.BytesIO(image_data)).convert("RGB")
        means = [sum(i * n for i, n in enumerate(ch.histogram())) / max(img.width * img.height, 1) for ch in img.split()]
        gray = sum(means) / 3
        if gray < 8 or max(means) / max(min(means), 1) < 1.12:
            logger.bind(tag=TAG).info(f"白平衡：通道均值 {[round(m) for m in means]}，无需校正")
            return image_data
        gains = [min(gray / max(m, 1), 2.0) for m in means]
        channels = [ch.point(lambda v, g=g: min(255, int(v * g))) for ch, g in zip(img.split(), gains)]
        out = io.BytesIO()
        Image.merge("RGB", channels).save(out, format="JPEG", quality=90)
        logger.bind(tag=TAG).info(f"白平衡：通道均值 {[round(m) for m in means]}，增益 {[round(g, 2) for g in gains]}")
        return out.getvalue()
    except Exception as e:  # 校正失败就用原图，不能影响拍照
        logger.bind(tag=TAG).warning(f"白平衡跳过: {e}")
        return image_data

def _rotate_upright(image_data: bytes, logger) -> bytes:
    """KidsWise 补丁：把照片转正，只在内存里做，不落盘。

    木米外壳里主板转了 90° 放（镜头才能对上左眼），拍出来的照片也跟着歪。
    WAYWISE_CAMERA_ROTATE 填照片需要顺时针转的角度（0/90/180/270），默认 0：裸板正放时不用转。
    """
    import os
    try:
        angle = int(os.environ.get("WAYWISE_CAMERA_ROTATE", "0")) % 360
    except ValueError:
        angle = 0
    if angle not in (90, 180, 270):
        return image_data
    try:
        import io
        from PIL import Image

        img = Image.open(io.BytesIO(image_data)).convert("RGB")
        # PIL 的 ROTATE_* 是逆时针
        img = img.transpose({90: Image.ROTATE_270, 180: Image.ROTATE_180, 270: Image.ROTATE_90}[angle])
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=90)
        return out.getvalue()
    except Exception as e:  # 转不了就用原图，不能影响拍照
        logger.bind(tag=TAG).warning(f"照片转正跳过: {e}")
        return image_data

class VisionHandler(BaseHandler):
    def __init__(self, config: dict):
        super().__init__(config)
        # 初始化认证工具
        self.auth = AuthToken(config["server"]["auth_key"])

    def _create_error_response(self, message: str) -> dict:
        """创建统一的错误响应格式"""
        return {"success": False, "message": message}

    def _verify_auth_token(self, request) -> Tuple[bool, Optional[str]]:
        """验证认证token"""
        # 测试模式：允许特定测试令牌或跳过验证
        auth_header = request.headers.get("Authorization", "")
        client_id = request.headers.get("Client-Id", "")

        # 允许测试客户端跳过认证
        if client_id == "web_test_client":
            device_id = request.headers.get("Device-Id", "test_device")
            return True, device_id

        if not auth_header.startswith("Bearer "):
            return False, None

        token = auth_header[7:]  # 移除"Bearer "前缀
        return self.auth.verify_token(token)

    async def handle_post(self, request):
        """处理 MCP Vision POST 请求"""
        response = None  # 初始化response变量
        try:
            # 验证token
            is_valid, token_device_id = self._verify_auth_token(request)
            if not is_valid:
                response = web.Response(
                    text=json.dumps(
                        self._create_error_response("无效的认证token或token已过期")
                    ),
                    content_type="application/json",
                    status=401,
                )
                return response

            # 获取请求头信息
            device_id = request.headers.get("Device-Id", "")
            client_id = request.headers.get("Client-Id", "")
            if device_id != token_device_id:
                raise ValueError("设备ID与token不匹配")
            # 解析multipart/form-data请求
            reader = await request.multipart()

            # 读取question字段
            question_field = await reader.next()
            if question_field is None:
                raise ValueError("缺少问题字段")
            question = await question_field.text()
            self.logger.bind(tag=TAG).debug(f"Question: {question}")

            # 读取图片文件
            image_field = await reader.next()
            if image_field is None:
                raise ValueError("缺少图片文件")

            # 读取图片数据
            image_data = await image_field.read()
            if not image_data:
                raise ValueError("图片数据为空")

            # 检查文件大小
            if len(image_data) > MAX_FILE_SIZE:
                raise ValueError(
                    f"图片大小超过限制，最大允许{MAX_FILE_SIZE/1024/1024}MB"
                )

            # 检查文件格式
            if not is_valid_image_file(image_data):
                raise ValueError(
                    "不支持的文件格式，请上传有效的图片文件（支持JPEG、PNG、GIF、BMP、TIFF、WEBP格式）"
                )

            # 将图片转换为base64编码
            image_data = _rotate_upright(image_data, self.logger)  # KidsWise 补丁：外壳里主板转了 90°
            image_data = _white_balance(image_data, self.logger)  # KidsWise 补丁：去掉偏紫
            image_base64 = base64.b64encode(image_data).decode("utf-8")

            # 如果开启了智控台，则从智控台获取模型配置
            current_config = copy.deepcopy(self.config)
            read_config_from_api = current_config.get("read_config_from_api", False)
            if read_config_from_api:
                current_config = await get_private_config_from_api(
                    current_config,
                    device_id,
                    client_id,
                )

            select_vllm_module = current_config["selected_module"].get("VLLM")
            if not select_vllm_module:
                raise ValueError("您还未设置默认的视觉分析模块")

            vllm_type = (
                select_vllm_module
                if "type" not in current_config["VLLM"][select_vllm_module]
                else current_config["VLLM"][select_vllm_module]["type"]
            )

            if not vllm_type:
                raise ValueError(f"无法找到VLLM模块对应的供应器{vllm_type}")

            vllm = create_instance(
                vllm_type, current_config["VLLM"][select_vllm_module]
            )

            result = vllm.response(question, image_base64)

            # KidsWise 补丁：不带 action，让拍照结果作为工具结果回到 LLM（我们的 agent），
            # 由 Mumi 用自己的口吻接着聊、顺势教词，而不是把视觉模型的原始描述直接念给孩子。
            return_json = {
                "success": True,
                "response": result,
            }

            response = web.Response(
                text=json.dumps(return_json, separators=(",", ":")),
                content_type="application/json",
            )
        except ValueError as e:
            self.logger.bind(tag=TAG).error(f"MCP Vision POST请求异常: {e}")
            return_json = self._create_error_response(str(e))
            response = web.Response(
                text=json.dumps(return_json, separators=(",", ":")),
                content_type="application/json",
            )
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"MCP Vision POST请求异常: {e}")
            return_json = self._create_error_response("处理请求时发生错误")
            response = web.Response(
                text=json.dumps(return_json, separators=(",", ":")),
                content_type="application/json",
            )
        finally:
            if response:
                self._add_cors_headers(response)
            return response

    async def handle_get(self, request):
        """处理 MCP Vision GET 请求"""
        try:
            vision_explain = get_vision_url(self.config)
            if vision_explain and len(vision_explain) > 0 and "null" != vision_explain:
                message = (
                    f"MCP Vision 接口运行正常，视觉解释接口地址是：{vision_explain}"
                )
            else:
                message = "MCP Vision 接口运行不正常，请打开data目录下的.config.yaml文件，找到【server.vision_explain】，设置好地址"

            response = web.Response(text=message, content_type="text/plain")
        except Exception as e:
            self.logger.bind(tag=TAG).error(f"MCP Vision GET请求异常: {e}")
            return_json = self._create_error_response("服务器内部错误")
            response = web.Response(
                text=json.dumps(return_json, separators=(",", ":")),
                content_type="application/json",
            )
        finally:
            self._add_cors_headers(response)
            return response
