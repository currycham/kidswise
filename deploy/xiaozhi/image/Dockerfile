# 在官方 xiaozhi-esp32-server 镜像上只加 Pillow：拍照结果交给视觉模型前，在内存里做白平衡校正（板载 OV3660 偏紫）
FROM ghcr.nju.edu.cn/xinnan-tech/xiaozhi-esp32-server:server_latest
RUN pip install --no-cache-dir -i https://mirrors.cloud.tencent.com/pypi/simple pillow
