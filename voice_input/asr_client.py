"""
豆包流式语音识别 WebSocket 客户端
基于官方二进制协议实现
"""

import json
import gzip
import struct
import uuid
import threading
import logging
from typing import Callable, Optional

import websocket

from . import config

logger = logging.getLogger(__name__)


class ASRClient:
    """豆包 ASR WebSocket 客户端"""

    # 协议常量
    PROTOCOL_VERSION = 0b0001
    HEADER_SIZE = 0b0001

    # 消息类型
    MSG_FULL_CLIENT_REQUEST = 0b0001
    MSG_AUDIO_ONLY = 0b0010
    MSG_FULL_SERVER_RESPONSE = 0b1001
    MSG_ERROR = 0b1111

    # 序列化方式
    SERIAL_NONE = 0b0000
    SERIAL_JSON = 0b0001

    # 压缩方式
    COMPRESS_NONE = 0b0000
    COMPRESS_GZIP = 0b0001

    def __init__(self, on_result: Callable[[str, bool], None], on_error: Callable[[str], None]):
        """
        Args:
            on_result: 回调函数，接收 (文本, 是否确定分句)
            on_error: 错误回调
        """
        self.on_result = on_result
        self.on_error = on_error
        self.ws: Optional[websocket.WebSocketApp] = None
        self.connect_id = str(uuid.uuid4())
        self._connected = threading.Event()
        self._closed = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()  # 保护 ws 的 send/close
        self._connect_error: Optional[str] = None  # 连接失败原因

    def _build_header(self, msg_type: int, msg_flags: int = 0,
                      serial: int = SERIAL_NONE, compress: int = COMPRESS_GZIP) -> bytes:
        """构建4字节协议头"""
        byte0 = (self.PROTOCOL_VERSION << 4) | self.HEADER_SIZE
        byte1 = (msg_type << 4) | msg_flags
        byte2 = (serial << 4) | compress
        byte3 = 0x00  # reserved
        return bytes([byte0, byte1, byte2, byte3])

    def _build_full_client_request(self) -> bytes:
        """构建初始请求（full client request）"""
        payload = {
            "user": {"uid": "voice_input_mac"},
            "audio": {
                "format": config.AUDIO_FORMAT,
                "rate": config.AUDIO_RATE,
                "bits": config.AUDIO_BITS,
                "channel": config.AUDIO_CHANNEL,
            },
            "request": {
                "model_name": "bigmodel",
                "enable_itn": True,
                "enable_punc": True,
                "enable_ddc": False,
                "show_utterances": True,
                "result_type": "full",
            }
        }

        payload_json = json.dumps(payload).encode('utf-8')
        payload_compressed = gzip.compress(payload_json)

        header = self._build_header(
            self.MSG_FULL_CLIENT_REQUEST,
            msg_flags=0b0000,
            serial=self.SERIAL_JSON,
            compress=self.COMPRESS_GZIP
        )

        size = struct.pack('>I', len(payload_compressed))
        return header + size + payload_compressed

    def _build_audio_request(self, audio_data: bytes, is_last: bool = False) -> bytes:
        """构建音频请求"""
        payload_compressed = gzip.compress(audio_data)

        msg_flags = 0b0010 if is_last else 0b0000
        header = self._build_header(
            self.MSG_AUDIO_ONLY,
            msg_flags=msg_flags,
            serial=self.SERIAL_NONE,
            compress=self.COMPRESS_GZIP
        )

        size = struct.pack('>I', len(payload_compressed))
        return header + size + payload_compressed

    def _parse_response(self, data: bytes) -> None:
        """解析服务端响应"""
        if len(data) < 4:
            logger.warning(f"[{self.connect_id}] 响应数据太短: {len(data)} bytes")
            return

        header = data[0:4]
        msg_type = (header[1] >> 4) & 0x0F
        msg_flags = header[1] & 0x0F
        compress = header[2] & 0x0F

        if msg_type == self.MSG_ERROR:
            # 错误响应
            if len(data) >= 12:
                error_code = struct.unpack('>I', data[4:8])[0]
                error_size = struct.unpack('>I', data[8:12])[0]
                if len(data) >= 12 + error_size:
                    error_msg = data[12:12+error_size].decode('utf-8', errors='ignore')
                else:
                    error_msg = "响应数据不完整"
                self.on_error(f"错误 {error_code}: {error_msg}")
            else:
                self.on_error("收到错误响应但数据不完整")
            return

        if msg_type == self.MSG_FULL_SERVER_RESPONSE:
            # 根据 msg_flags 判断是否有 sequence
            # flags bit 0: 1 表示有 sequence
            has_sequence = (msg_flags & 0b0001) != 0
            offset = 4 + (4 if has_sequence else 0)  # header + optional sequence

            if len(data) < offset + 4:
                logger.warning(f"[{self.connect_id}] 响应数据不足以读取 payload size")
                return

            payload_size = struct.unpack('>I', data[offset:offset+4])[0]

            # 边界检查
            if len(data) < offset + 4 + payload_size:
                logger.warning(f"[{self.connect_id}] payload 数据不完整: 需要 {payload_size}, 实际 {len(data) - offset - 4}")
                return

            payload = data[offset+4:offset+4+payload_size]

            if compress == self.COMPRESS_GZIP:
                try:
                    payload = gzip.decompress(payload)
                except Exception as e:
                    logger.error(f"[{self.connect_id}] gzip 解压失败: {e}")
                    self.on_error(f"数据解压失败: {e}")
                    return

            try:
                result = json.loads(payload.decode('utf-8'))
                if 'result' in result:
                    text = result['result'].get('text', '')
                    # 检查是否有确定分句
                    is_definite = False
                    utterances = result['result'].get('utterances', [])
                    if utterances:
                        is_definite = utterances[-1].get('definite', False)
                    self.on_result(text, is_definite)
            except json.JSONDecodeError as e:
                logger.error(f"[{self.connect_id}] JSON 解析失败: {e}")
                self.on_error(f"结果解析失败: {e}")

    def _on_message(self, ws, message):
        """WebSocket 消息回调"""
        if isinstance(message, bytes):
            self._parse_response(message)

    def _on_error(self, ws, error):
        """WebSocket 错误回调"""
        error_str = str(error)
        logger.error(f"[{self.connect_id}] WebSocket 错误: {error_str}")
        self._connect_error = error_str
        self.on_error(error_str)

    def _on_close(self, ws, close_status_code, close_msg):
        """WebSocket 关闭回调"""
        logger.info(f"[{self.connect_id}] 连接关闭: {close_status_code} - {close_msg}")
        self._connected.clear()
        self._closed.set()

    def _on_open(self, ws):
        """WebSocket 连接成功回调"""
        logger.info(f"[{self.connect_id}] 连接成功")
        # 发送 full client request
        request = self._build_full_client_request()
        with self._lock:
            if self.ws:
                ws.send(request, opcode=websocket.ABNF.OPCODE_BINARY)
        self._connected.set()

    def connect(self) -> tuple[bool, str]:
        """
        建立连接
        Returns:
            (是否成功, 错误信息)
        """
        self._connect_error = None
        self._closed.clear()

        headers = {
            "X-Api-App-Key": config.get_app_key(),
            "X-Api-Access-Key": config.get_access_key(),
            "X-Api-Resource-Id": config.get_resource_id(),
            "X-Api-Connect-Id": self.connect_id,
        }

        self.ws = websocket.WebSocketApp(
            config.WSS_URL,
            header=headers,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
            on_open=self._on_open,
        )

        self._thread = threading.Thread(target=self._run_ws, daemon=True)
        self._thread.start()

        # 等待连接成功或失败
        connected = self._connected.wait(timeout=5.0)
        if not connected:
            error = self._connect_error or "连接超时"
            return False, error

        return True, ""

    def _run_ws(self):
        """运行 WebSocket（带 ping）"""
        try:
            self.ws.run_forever(ping_interval=30, ping_timeout=10)
        except Exception as e:
            logger.error(f"[{self.connect_id}] run_forever 异常: {e}")
        finally:
            self._closed.set()

    def send_audio(self, audio_data: bytes, is_last: bool = False) -> bool:
        """
        发送音频数据
        Returns:
            是否发送成功
        """
        with self._lock:
            if not self.ws or not self._connected.is_set():
                return False
            try:
                request = self._build_audio_request(audio_data, is_last)
                self.ws.send(request, opcode=websocket.ABNF.OPCODE_BINARY)
                return True
            except Exception as e:
                logger.error(f"[{self.connect_id}] 发送失败: {e}")
                self.on_error(f"发送失败: {e}")
                return False

    def close(self) -> None:
        """关闭连接"""
        with self._lock:
            if self.ws:
                try:
                    self.ws.close()
                except Exception as e:
                    logger.warning(f"[{self.connect_id}] 关闭异常: {e}")
                self.ws = None

        self._connected.clear()

        # 等待线程退出
        if self._thread and self._thread.is_alive():
            self._closed.wait(timeout=2.0)
