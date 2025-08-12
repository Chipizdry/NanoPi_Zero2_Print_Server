

import cups
from fastapi import FastAPI, Body

app = FastAPI()

CUPS_SERVER = "cups-server"  # имя контейнера
CUPS_PORT = 631

@app.get("/")
def root():
    return {"message": "Print API is running"}

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
