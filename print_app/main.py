
import cups
from fastapi import FastAPI, Body, Request
from fastapi.responses import RedirectResponse, HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

app = FastAPI()

# Подключаем папки фронта
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

CUPS_SERVER = "cups-server"
CUPS_PORT = 631



@app.get("/")
def redirect_to_static():
    return RedirectResponse(url="/static/index.html")

@app.post("/print")
def print_label(content: str = Body(..., embed=True)):
    conn = cups.Connection(host=CUPS_SERVER, port=CUPS_PORT)
    printers = conn.getPrinters()
    if not printers:
        return {"error": "No printers found"}

    printer_name = list(printers.keys())[0]
    file_path = "/tmp/label.txt"
    with open(file_path, "w") as f:
        f.write(content)

    conn.printFile(printer_name, file_path, "Label Job", {})
    return {"status": "Printed", "printer": printer_name}