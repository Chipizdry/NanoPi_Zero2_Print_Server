import logging
from fastapi import FastAPI, Body, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from PIL import Image, ImageDraw, ImageFont
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

USB_PRINTER_PATH = "/dev/usb/lp0"
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
    logger.debug("Converting image to Brother Raster format")
    img = img.convert("1")
    width, height = img.size
    raster_data = bytearray()

    raster_data += b'\x1b@'
    raster_data += b'\x1bia' + b'\x01'
    raster_data += b'\x1biM\x00'

    row_bytes = (width + 7) // 8
    for y in range(height):
        row = bytearray()
        for x in range(0, width, 8):
            byte = 0
            for bit in range(8):
                if x + bit < width:
                    pixel = img.getpixel((x + bit, y))
                    if pixel == 0:
                        byte |= (1 << (7 - bit))
            row.append(byte)
        raster_data += b'\x67' + struct.pack('<H', row_bytes) + row

    raster_data += b'\x1a'
    logger.debug(f"Raster data length: {len(raster_data)} bytes")
    return raster_data

def send_to_printer(data: bytes):
    logger.info(f"Sending {len(data)} bytes to printer")

    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if dev is None:
        logger.error("Printer not found")
        raise RuntimeError("Printer not found")

    try:
        if dev.is_kernel_driver_active(0):
            logger.debug("Detaching kernel driver")
            dev.detach_kernel_driver(0)

        dev.set_configuration()
        cfg = dev.get_active_configuration()
        intf = cfg[(0, 0)]

        endpoint = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
        )
        if endpoint is None:
            logger.error("No OUT endpoint found")
            raise RuntimeError("No OUT endpoint found")

        logger.debug(f"Writing data to endpoint {endpoint.bEndpointAddress}")
        dev.write(endpoint.bEndpointAddress, data)
        logger.info("Data sent successfully")

    except Exception as e:
        logger.exception("Failed to send data to printer")
        raise RuntimeError(f"USB communication error: {e}")

@app.post("/print")
def print_label(content: str = Body(..., embed=True)):
    logger.info(f"Received print request with content: {content}")

    if not os.path.exists(USB_PRINTER_PATH):
        logger.error(f"Printer device not found at {USB_PRINTER_PATH}")
        return {"error": f"Printer not found at {USB_PRINTER_PATH}"}

    try:
        img = text_to_image(content)
        raster_data = image_to_brother_raster(img)
        send_to_printer(raster_data)
        logger.info("Print job completed successfully")
        return {"status": "Printed", "text": content}
    except Exception as e:
        logger.error(f"Error during print job: {e}")
        raise HTTPException(status_code=500, detail=str(e))
