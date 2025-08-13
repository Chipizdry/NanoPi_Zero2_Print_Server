import logging
from fastapi import FastAPI, Body, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from PIL import Image, ImageDraw, ImageFont
from brother_ql.raster import BrotherQLRaster
from brother_ql.conversion import convert
import struct
import time
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



def check_printer_ready(dev):
    try:
        # Чтение статуса принтера (Brother specific)
        status = dev.ctrl_transfer(
            0xC0,  # bmRequestType (IN)
            0x01,   # bRequest (GET_STATUS)
            0, 0,   # wValue, wIndex
            8       # wLength
        )
        if status[0] & 0x20:  # Проверка готовности
            raise RuntimeError("Printer not ready")
    except usb.core.USBError as e:
        raise RuntimeError(f"Printer status error: {e}")        

@app.on_event("startup")
async def startup_event():
    # Проверяем права доступа к USB
    if os.name == 'posix':  # Для Linux
        usb_devices = "/dev/bus/usb"
        if os.path.exists(usb_devices):
            os.system(f"chmod -R 666 {usb_devices}/*/*")
            logger.info("Fixed USB permissions")

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
    logger.debug("Converting image using BrotherQLRaster (official)")

    img = img.convert("RGB")  # brother_ql ожидает RGB
    qlr = BrotherQLRaster('QL-810W')
    qlr.exception_on_warning = True

    instructions = convert(
        qlr=qlr,
        images=[img],
        label='62',       # Для 62мм ленты
        rotate='90',      # Обязательно для Brother
        threshold=70,     # Оптимальное значение
        dither=True,      # Включить для лучшего качества
        compress=False,   # Отключить сжатие
        red=False,       # False для черной ленты
        dpi_600=False,    # Для QL-810W лучше False
        hq=True,         # Высокое качество
        cut=True         # Автоматическая обрезка
    )

    # Преобразуем список int в bytes
    data = bytes(qlr.data)
    logger.debug(f"Generated Brother Raster length: {len(data)} bytes")
    return data

def send_to_printer(data: bytes):
    logger.info(f"Preparing to send {len(data)} bytes to printer")
    
    try:
        # Поиск устройства с обработкой ошибок
        dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
        if dev is None:
            raise RuntimeError("Printer not found")

        # Логирование подключенных устройств
        devices = list(usb.core.find(find_all=True))
        logger.debug(f"Found {len(devices)} USB device(s)")
        for i, d in enumerate(devices, start=1):
            try:
                manufacturer = usb.util.get_string(d, d.iManufacturer) if d.iManufacturer else "N/A"
                product = usb.util.get_string(d, d.iProduct) if d.iProduct else "N/A"
                serial = usb.util.get_string(d, d.iSerialNumber) if d.iSerialNumber else "N/A"
                logger.debug(f"[{i}] VID={hex(d.idVendor)} PID={hex(d.idProduct)} "
                             f"Manufacturer={manufacturer} Product={product} Serial={serial}")
            except Exception as e:
                logger.debug(f"[{i}] Basic info: VID={hex(d.idVendor)} PID={hex(d.idProduct)} Error: {str(e)}")

        # Сброс и настройка устройства
        try:
            dev.reset()
            time.sleep(1)
            dev.set_configuration()
            time.sleep(1)
        except usb.core.USBError as e:
            logger.error(f"USB setup error: {e}")
            raise RuntimeError("Printer initialization failed")

        # Проверка состояния принтера
        try:
            status = dev.ctrl_transfer(0xC0, 0x01, 0, 0, 8)
            if status[0] & 0x08:
                raise RuntimeError("Printer error (check paper/ribbon)")
        except usb.core.USBError as e:
            logger.warning(f"Status check failed: {e}")

        # Отправка данных
        cfg = dev.get_active_configuration()
        intf = cfg[(0,0)]
        endpoint_out = usb.util.find_descriptor(
            intf,
            custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
        )
        
        if endpoint_out is None:
            raise RuntimeError("No OUT endpoint found")

        # Отправка пакетами
        chunk_size = endpoint_out.wMaxPacketSize
        for i in range(0, len(data), chunk_size):
            chunk = data[i:i+chunk_size]
            dev.write(endpoint_out.bEndpointAddress, chunk, timeout=5000)
            time.sleep(0.01)

        logger.info("Data sent successfully")
        
    except Exception as e:
        logger.error(f"Print failed: {e}")
        raise
    finally:
        if 'dev' in locals():
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