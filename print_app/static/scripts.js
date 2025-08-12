



document.getElementById('printBtn').addEventListener('click', async () => {
    const text = document.getElementById('text').value;
    const statusEl = document.getElementById('status');

    if (!text.trim()) {
        statusEl.textContent = "Введите текст!";
        statusEl.style.color = "red";
        return;
    }

    try {
        const res = await fetch('/print', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ content: text })
        });

        if (res.ok) {
            statusEl.textContent = "Отправлено на печать!";
            statusEl.style.color = "green";
        } else {
            statusEl.textContent = "Ошибка при отправке!";
            statusEl.style.color = "red";
        }
    } catch {
        statusEl.textContent = "Ошибка соединения!";
        statusEl.style.color = "red";
    }
});