# 设备固件（DFRobot ESP32-S3 AI CAM / DFR1154）

基于开源小智固件 [78/xiaozhi-esp32](https://github.com/78/xiaozhi-esp32) **v2.1.0**，只改板子的 sdkconfig，不改代码：

| 改动 | 为什么 |
|---|---|
| `CONFIG_OTA_URL` 指向我们的服务器 | 配网时不用再手动填 OTA 地址 |
| 自定义唤醒词「你好木米」（`ni hao mu mi`，MultiNet7 中文） | 原厂唤醒词是「你好小智」；木米是 Mumi 的中文名 |
| 摄像头 OV3660，**传感器直出 JPEG 1280×720**（`XIAOZHI_CAMERA_ALLOW_JPEG_INPUT`） | 板上不做颜色转换。YUV422 240×240 能认物体但偏紫；RGB565 不交换字节序时画面花掉，交换后仍偏紫 |
| 关闭 `SEND_WAKE_WORD_DATA` | 否则唤醒词录音会被当成孩子的第一句话去识别（「你好木米」被识别成「你好，猫咪」） |

完整配置见 [`df-s3-ai-cam.config.json`](df-s3-ai-cam.config.json)，它会覆盖上游的 `main/boards/df-s3-ai-cam/config.json`。

为什么用 v2.1.0：它是支持自定义唤醒词、又能用 ESP-IDF 5.x 编译、而且有这块板子配置的版本。v2.2 以后要求 ESP-IDF 6。
DFRobot 官方提供的预编译固件是 1.6.6，不支持自定义唤醒词。

## 编译（在服务器上用 Docker，不用在 Mac 装工具链）

```bash
git clone --depth 1 --branch v2.1.0 https://github.com/78/xiaozhi-esp32.git v210 && cd v210
cp <本仓库>/firmware/df-s3-ai-cam.config.json main/boards/df-s3-ai-cam/config.json
sudo docker run --rm -v $PWD:/project -w /project -e HOME=/tmp \
  -e IDF_COMPONENT_STORAGE_URL="https://components-file.espressif.cn;default" \
  espressif/idf:release-v5.5 \
  bash -c "git config --global --add safe.directory /project; python scripts/release.py df-s3-ai-cam"
# 产物：releases/v2.1.0_df-s3-ai-cam.zip → merged-binary.bin
```

踩过的坑：
- 必须用 ESP-IDF **5.5**（上游 CI 用 `espressif/idf:release-v5.5`）。用 5.4.2 时，组件 `esp_emote_gfx==2.0.0` 会编译失败（`-Werror=format`）
- 开了自定义唤醒词，就必须选一个 MultiNet 模型（`CONFIG_SR_MN_CN_MULTINET7_QUANT=y`），否则打包资源时报错
- 国内拉组件：设置的是 `IDF_COMPONENT_STORAGE_URL`，不是 `IDF_COMPONENT_REGISTRY_URL`

## 烧录（Mac，USB-C 数据线）

```bash
python3 -m venv .venv && .venv/bin/pip install esptool
ls /dev/cu.usbmodem*          # 插上板子后多出来的那个
.venv/bin/esptool --port /dev/cu.usbmodemXXXX erase-flash
.venv/bin/esptool --port /dev/cu.usbmodemXXXX --baud 921600 write-flash 0x0 merged-binary.bin
```

只改固件、想保留 Wi-Fi 配置时，跳过 `erase-flash` 直接 `write-flash`（合并镜像不包含 Wi-Fi 配置所在的分区）。

烧完板子会开 `Xiaozhi-xxxx` 热点，手机连上后填 2.4GHz Wi-Fi 即可（OTA 地址已编进固件）。
烧不进去只有两种原因：线不是数据线，或者没进下载模式（按住 BOOT，重新插 USB，再松开）。

原厂固件（DFRobot 预编译 1.6.6，唤醒词「你好小智」）可以随时烧回去：
[DFR1154_Examples / 6.6 xiaozhi / bin / xiaozhi-CAM.zip](https://github.com/DFRobot/DFR1154_Examples/tree/master/6.6%20xiaozhi/bin)。
