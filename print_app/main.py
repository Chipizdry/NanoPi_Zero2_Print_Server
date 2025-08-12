from fastapi import FastAPI, Body
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from PIL import Image, ImageDraw, ImageFont
import struct
import os
import usb.core
import usb.util

# USB идентификаторы вашего принтера (Vendor ID и Product ID)
VENDOR_ID = 0x04f9
PRODUCT_ID = 0x209c

app = FastAPI()

# Монтируем папки для статики и шаблонов фронтенда
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

@app.get("/")
def redirect_to_static():
    # Перенаправляем корень на главный html файл фронтенда
    return RedirectResponse(url="/static/index.html")

# Путь к устройству принтера в системе (используется для проверки)
USB_PRINTER_PATH = "/dev/usb/lp0"

# Ширина этикетки в пикселях (62 мм примерно 696 точек)
LABEL_WIDTH = 696

def text_to_image(text: str) -> Image.Image:
    """
    Конвертируем текст в монохромное изображение, 
    которое затем будет отправлено на принтер.
    """
    font = ImageFont.load_default()  # Шрифт по умолчанию
    dummy_img = Image.new("1", (1, 1), 1)  # Создаём dummy изображение для расчёта размера текста
    draw = ImageDraw.Draw(dummy_img)
    bbox = draw.textbbox((0, 0), text, font=font)  # Получаем bounding box текста
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    # Создаём новое изображение нужного размера с белым фоном
    img = Image.new("1", (LABEL_WIDTH, text_height + 20), 1)
    draw = ImageDraw.Draw(img)
    # Рисуем текст по центру по горизонтали, с отступом сверху 10 px
    draw.text(((LABEL_WIDTH - text_width) // 2, 10), text, font=font, fill=0)

    # Поворачиваем изображение для вертикальной печати (если нужно)
    return img.transpose(Image.ROTATE_270)

def image_to_brother_raster(img: Image.Image) -> bytes:
    """
    Преобразуем изображение в формат Brother Raster Mode,
    который понимает ваш принтер.
    """
    img = img.convert("1")  # Приводим к монохрому формату (1 бит на пиксель)
    width, height = img.size
    raster_data = bytearray()

    # Инициализация принтера (ESC @)
    raster_data += b'\x1b@'
    # Включаем режим растровой печати (ESC i a 1)
    raster_data += b'\x1bia' + b'\x01'

    # Указываем, что сжатие не используется (ESC i M 0)
    raster_data += b'\x1biM\x00'

    row_bytes = (width + 7) // 8  # Кол-во байт на строку

    # Перебираем все строки изображения
    for y in range(height):
        row = bytearray()
        # Перебираем все пиксели строки по 8
        for x in range(0, width, 8):
            byte = 0
            # Формируем байт из 8 пикселей (битов)
            for bit in range(8):
                if x + bit < width:
                    pixel = img.getpixel((x + bit, y))
                    if pixel == 0:  # Черный пиксель
                        byte |= (1 << (7 - bit))
            row.append(byte)
        # Добавляем команду печати строки (ESC g + длина строки + данные)
        raster_data += b'\x67' + struct.pack('<H', row_bytes) + row

    # Команда конца печати (SUB - 0x1a)
    raster_data += b'\x1a'

    return raster_data

def send_to_printer(data: bytes):
    """
    Находит принтер по USB и отправляет ему данные.
    """
    print(f"Sending {len(data)} bytes to printer")
    # Находим устройство по VID и PID
    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if dev is None:
        raise ValueError("Printer not found")

    # Отсоединяем драйвер ядра, если он привязан
    if dev.is_kernel_driver_active(0):
        dev.detach_kernel_driver(0)

    # Устанавливаем конфигурацию по умолчанию
    dev.set_configuration()
    cfg = dev.get_active_configuration()
    intf = cfg[(0, 0)]

    # Находим выходной endpoint для записи данных
    endpoint = usb.util.find_descriptor(
        intf,
        custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT
    )
    if endpoint is None:
        raise ValueError("No OUT endpoint found")

    # Отправляем данные на принтер
    dev.write(endpoint.bEndpointAddress, data)

@app.post("/print")
def print_label(content: str = Body(..., embed=True)):
    """
    Основной метод API — принимает текст, конвертирует и отправляет на печать.
    """
    # Проверяем, что устройство принтера доступно в системе
    if not os.path.exists(USB_PRINTER_PATH):
        return {"error": f"Printer not found at {USB_PRINTER_PATH}"}

    # Конвертируем текст в изображение
    img = text_to_image(content)
    # Преобразуем изображение в бинарный формат для принтера
    raster_data = image_to_brother_raster(img)
    # Отправляем данные на принтер
    send_to_printer(raster_data)

    return {"status": "Printed", "text": content}
