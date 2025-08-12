


from fastapi import FastAPI, Body
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from PIL import Image, ImageDraw, ImageFont
import io
import struct
import os
import usb.core
import usb.util

VENDOR_ID = 0x04f9
PRODUCT_ID = 0x209c


app = FastAPI()


# Подключаем папки фронта
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

@app.get("/")
def redirect_to_static():
    return RedirectResponse(url="/static/index.html")


USB_PRINTER_PATH = "/dev/usb/lp0"

# Размер ленты 62 мм (ширина в точках 696 px)
LABEL_WIDTH = 696
def text_to_image(text: str) -> Image.Image:
    font = ImageFont.load_default()  # Можно заменить на TTF-шрифт
    dummy_img = Image.new("1", (1, 1), 1)
    draw = ImageDraw.Draw(dummy_img)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]
    img = Image.new("1", (LABEL_WIDTH, text_height + 20), 1)
    draw = ImageDraw.Draw(img)
    draw.text(((LABEL_WIDTH - text_width) // 2, 10), text, font=font, fill=0)
    return img.transpose(Image.ROTATE_270)  # Поворот для вертикальной печати

def image_to_brother_raster(img: Image.Image) -> bytes:
    """
    Конвертируем монохромную картинку в Brother Raster Mode.
    """
    img = img.convert("1")  # Монохром
    width, height = img.size
    raster_data = bytearray()

    # Init
    raster_data += b'\x1b@'  # Initialize printer
    raster_data += b'\x1bia' + b'\x01'  # Switch to raster mode

    # Параметры страницы
    raster_data += b'\x1biM\x00'  # No compression

    # Линии пикселей
    row_bytes = (width + 7) // 8
    for y in range(height):
        row = bytearray()
        for x in range(0, width, 8):
            byte = 0
            for bit in range(8):
                if x + bit < width:
                    pixel = img.getpixel((x + bit, y))
                    if pixel == 0:  # Черный
                        byte |= (1 << (7 - bit))
            row.append(byte)
        raster_data += b'\x67' + struct.pack('<H', row_bytes) + row

    # Конец страницы
    raster_data += b'\x1a'  # Print command
    return raster_data



def send_to_printer(data: bytes):
    print(f"Sending {len(data)} bytes to printer")
    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if dev is None:
        raise ValueError("Printer not found")

    if dev.is_kernel_driver_active(0):
        dev.detach_kernel_driver(0)
    
    dev.set_configuration()
    cfg = dev.get_active_configuration()
    intf = cfg[(0, 0)]

    endpoint = usb.util.find_descriptor(
        intf,
        custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
    )
    if endpoint is None:
        raise ValueError("No OUT endpoint found")

    dev.write(endpoint.bEndpointAddress, data)

@app.post("/print")
def print_label(content: str = Body(..., embed=True)):
    if not os.path.exists(USB_PRINTER_PATH):
        return {"error": f"Printer not found at {USB_PRINTER_PATH}"}

    img = text_to_image(content)
    raster_data = image_to_brother_raster(img)
    send_to_printer(raster_data)
    return {"status": "Printed", "text": content}