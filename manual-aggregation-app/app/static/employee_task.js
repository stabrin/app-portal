document.addEventListener('DOMContentLoaded', function() {
    const scanInput = document.getElementById('scan-input');
    const instructionEl = document.getElementById('ui-instruction');
    const alertContainer = document.getElementById('alert-container');
    const unitContainer = document.getElementById('current-unit-container');
    const unitTitle = document.getElementById('current-unit-title');
    const unitItemsList = document.getElementById('current-unit-items');
    const unitParent = document.getElementById('current-unit-parent');

    // Фокусируемся на поле ввода при загрузке и при клике в любом месте
    scanInput.focus();
    document.body.addEventListener('click', () => scanInput.focus());
    
    // --- Изначальный запрос состояния ---
    // Чтобы если сотрудник перезагрузил страницу, он продолжил с того же места
    // Для этого нужен GET эндпоинт, который вернет состояние
    // Пока для простоты начнем с чистого листа
    instructionEl.textContent = "Отсканируйте код товара, короба или управляющий код";


    // --- Функция отправки данных на бэкенд ---
    async function sendScan(code) {
        try {
            const response = await fetch('/api/scan', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': '{{ csrf_token() }}' // Если вы используете CSRF-защиту в формах
                },
                body: JSON.stringify({ scanned_code: code })
            });

            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }

            const result = await response.json();
            updateUI(result);

        } catch (error) {
            console.error("Fetch Error:", error);
            showAlert(`Критическая ошибка сети: ${error.message}`, 'danger');
        } finally {
            scanInput.value = ''; // Очищаем поле ввода
            scanInput.focus();
        }
    }

    // --- Обработчик ввода ---
/**
 * Обработчик события 'change' для поля ввода сканера.
 * Срабатывает, когда сканер завершает ввод данных (обычно эмулируя нажатие Enter).
 */
scanInput.addEventListener('change', function(event) {

    // 1. Получаем отсканированное значение и убираем лишние пробелы по краям.
    const code = event.target.value.trim();

    // 2. Если после очистки ничего не осталось (например, случайный ввод пробела),
    // то ничего не делаем и выходим из функции.
    if (!code) {
        return;
    }

    // 3. ПРОВЕРКА НА СТОРОНЕ КЛИЕНТА: является ли код командой выхода.
    // Такие команды лучше обрабатывать немедленно в браузере, не отправляя на сервер.
    if (code === 'CMD_LOGOUT') {
        
        // Показываем пользователю информационное сообщение о том, что происходит.
        showAlert('Завершение смены...', 'warning');

        // Получаем URL для выхода из скрытого элемента на странице.
        // Это безопаснее и гибче, чем "зашивать" URL прямо в JavaScript.
        const logoutUrl = document.getElementById('logout-url').dataset.url;

        // Немедленно перенаправляем браузер на страницу выхода из системы.
        window.location.href = logoutUrl;
        
        // Важно: прерываем выполнение функции здесь, чтобы этот код
        // не был отправлен на бэкенд через вызов sendScan().
        return; 
    }

    // 4. Если это НЕ команда выхода, то это обычный код (DM товара, SSCC короба)
    // или команда для бэкенда (например, CMD_COMPLETE_UNIT).
    // Отправляем его на сервер для полноценной обработки.
    sendScan(code);

    // Примечание: Очистка поля ввода (`scanInput.value = ''`) и повторная фокусировка (`scanInput.focus()`)
    // уже реализованы внутри функции sendScan() в блоке `finally`.
    // Это гарантирует, что поле будет готово к следующему сканированию в любом случае:
    // и при успешном ответе сервера, и при ошибке.
});

    // --- Функция обновления интерфейса ---
    function updateUI(data) {
        console.log("Received data:", data);

        // 1. Показать сообщение/ошибку
        if (data.message) {
            let alertType = 'info';
            if (data.status === 'success') alertType = 'success';
            if (data.status === 'error') alertType = 'danger';
            showAlert(data.message, alertType);
        }

        // 2. Обновить инструкцию
        if (data.ui_instruction) {
            instructionEl.textContent = data.ui_instruction;
        }

        // 3. Обновить отображение текущего юнита
        if (data.current_unit) {
            unitContainer.style.display = 'block';
            unitTitle.textContent = `Собирается: ${data.current_unit.type || 'Юнит'}`;
            
            // Очищаем список
            unitItemsList.innerHTML = '';
            // Наполняем новым содержимым
            if (data.current_unit.items && data.current_unit.items.length > 0) {
                data.current_unit.items.forEach(item => {
                    const li = document.createElement('li');
                    li.className = 'list-group-item';
                    li.textContent = `Вложение: ${item}`;
                    unitItemsList.appendChild(li);
                });
            }
            
            if (data.current_unit.parent_code) {
                unitParent.textContent = `Код родителя: ${data.current_unit.parent_code}`;
            } else {
                unitParent.textContent = '';
            }

        } else {
            unitContainer.style.display = 'none';
        }
    }

    // --- Вспомогательная функция для алертов ---
    function showAlert(message, type = 'info') {
        const alertDiv = `
            <div class="alert alert-${type} alert-dismissible fade show" role="alert">
                ${message}
                <button type="button" class="btn-close" data-bs-dismiss="alert" aria-label="Close"></button>
            </div>
        `;
        alertContainer.innerHTML = alertDiv;
    }
});