import logging
from fastapi import FastAPI, Body, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from PIL import Image, ImageDraw, ImageFont
from brother_ql.raster import BrotherQLRaster
from brother_ql.conversion import convert
import struct
import os
import usb.core
import usb.util



# Настройка логгера
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)

VENDOR_ID = 0x04f9
PRODUCT_ID = 0x209c

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

@app.get("/")
def redirect_to_static():
    logger.debug("Redirecting to /static/index.html")
    return RedirectResponse(url="/static/index.html")

USB_PRINTER_PATH = "/dev/usblp0"
LABEL_WIDTH = 696

def text_to_image(text: str) -> Image.Image:
    logger.debug(f"Converting text to image: {text}")
    font = ImageFont.load_default()
    dummy_img = Image.new("1", (1, 1), 1)
    draw = ImageDraw.Draw(dummy_img)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    img = Image.new("1", (LABEL_WIDTH, text_height + 20), 1)
    draw = ImageDraw.Draw(img)
    draw.text(((LABEL_WIDTH - text_width) // 2, 10), text, font=font, fill=0)

    rotated_img = img.transpose(Image.ROTATE_270)
    logger.debug(f"Image size after rotation: {rotated_img.size}")
    return rotated_img

def image_to_brother_raster(img: Image.Image) -> bytes:
    """
    Конвертирует PIL.Image в полный Brother Raster поток для QL-810W
    с использованием официального метода convert().
    """
    logger.debug("Converting image using BrotherQLRaster (official)")

    img = img.convert("RGB")  # brother_ql ожидает RGB
    qlr = BrotherQLRaster('QL-810W')
    qlr.exception_on_warning = True

    # Используем convert() вместо add_label()
    # label_size='62' — ширина ленты 62 мм
    instructions = convert(
        qlr=qlr,
        images=[img],
        label='62',
        rotate='90',       # т.к. изображение уже повёрнуто
        threshold=70,
        dither=False,
        compress=False,
        red=False,
        dpi_600=False,
        hq=True,
        cut=True
    )

    # Данные для отправки на принтер находятся в qlr.data
    data = b''.join(qlr.data)
    logger.debug(f"Generated Brother Raster length: {len(data)} bytes")
    return data


def send_to_printer(data: bytes):
    logger.info(f"Preparing to send {len(data)} bytes to printer")

    # Логируем все устройства, чтобы понять, что видит pyusb
    devices = list(usb.core.find(find_all=True))
    logger.debug(f"Found {len(devices)} USB device(s)")
    for i, d in enumerate(devices, start=1):
        try:
            logger.debug(f"[{i}] VID={hex(d.idVendor)} PID={hex(d.idProduct)} "
                         f"Manufacturer={usb.util.get_string(d, d.iManufacturer)} "
                         f"Product={usb.util.get_string(d, d.iProduct)} "
                         f"Serial={usb.util.get_string(d, d.iSerialNumber)}")
        except Exception as e:
            logger.debug(f"[{i}] Could not read descriptor: {e}")

    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if dev is None:
        logger.error("Target printer not found")
        raise RuntimeError("Printer not found")

    logger.debug(f"Using printer: VID={hex(dev.idVendor)}, PID={hex(dev.idProduct)}")
    try:
        logger.debug(f"Manufacturer: {usb.util.get_string(dev, dev.iManufacturer)}")
        logger.debug(f"Product: {usb.util.get_string(dev, dev.iProduct)}")
        logger.debug(f"Serial: {usb.util.get_string(dev, dev.iSerialNumber)}")
    except Exception as e:
        logger.warning(f"Could not read printer strings: {e}")

    try:
        if dev.is_kernel_driver_active(0):
            logger.debug("Detaching kernel driver from interface 0")
            dev.detach_kernel_driver(0)
    except usb.core.USBError as e:
        logger.warning(f"Kernel driver detach failed: {e}")

    dev.set_configuration()
    cfg = dev.get_active_configuration()
    logger.debug(f"Active configuration: {cfg.bConfigurationValue}")

    intf = cfg[(0, 0)]
    logger.debug(f"Interface: {intf.bInterfaceNumber}, AltSetting: {intf.bAlternateSetting}")

    endpoints = list(intf)
    for ep in endpoints:
        logger.debug(f"Endpoint: address={hex(ep.bEndpointAddress)}, "
                     f"type={ep.bmAttributes}, max_packet_size={ep.wMaxPacketSize}")

    endpoint_out = usb.util.find_descriptor(
        intf,
        custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
    )
    if endpoint_out is None:
        logger.error("No OUT endpoint found")
        raise RuntimeError("No OUT endpoint found")

    logger.debug(f"Writing to endpoint {endpoint_out.bEndpointAddress}")
    try:
        bytes_written = dev.write(endpoint_out.bEndpointAddress, data, timeout=5000)
        logger.info(f"Write complete, bytes written: {bytes_written}")
    except Exception as e:
        logger.exception(f"USB write failed: {e}")
        raise

    # Попробуем получить ответ, если есть IN endpoint
    endpoint_in = usb.util.find_descriptor(
        intf,
        custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN
    )
    if endpoint_in:
        try:
            response = dev.read(endpoint_in.bEndpointAddress, endpoint_in.wMaxPacketSize, timeout=2000)
            logger.debug(f"Printer response: {response}")
        except usb.core.USBError as e:
            logger.debug(f"No response from printer: {e}")

    usb.util.dispose_resources(dev)


@app.post("/print")
def print_label(content: str = Body(..., embed=True)):
    print(f"[INFO] Received print request with content: {content}")
    
    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if dev is None:
        print(f"[ERROR] Printer device with VID={hex(VENDOR_ID)} PID={hex(PRODUCT_ID)} not found")
        return {"error": "Printer device not found"}

    img = text_to_image(content)
    raster_data = image_to_brother_raster(img)
    try:
        send_to_printer(raster_data)
    except Exception as e:
        print(f"[ERROR] Failed to send data to printer: {e}")
        return {"error": str(e)}

    return {"status": "Printed", "text": content}